"""南图 NonTo MCP 路由（第一阶段：只读版，设计文档 v1.0 已决策定版）。

挂载方式（见同目录外 README / PATCH 说明，不改动现有路由）：
    # app/main.py
    from app.routers import mcp
    app.include_router(mcp.router)   # 路由本身已定义 /mcp，不再加 prefix

设计要点（全部来自 v1.0 冻结决策，不自行放宽）：
- 传输只做 Streamable HTTP，端点 POST /mcp；GET /mcp 只返回服务器信息。
- 与 NonToPython 同进程挂载，独立开关：NONTO_MCP_ENABLED=0 时全部工具
  返回 forbidden_scope，现有 API 与 App 不受影响。
- 不改 JWT 结构：用现有 app.core.auth_core.verify_token 解析
  Authorization: Bearer 令牌；令牌不进 URL、不记日志、不记原文。
  个人上下文工具（mcp:read:self）必须有有效 JWT，公开工具可匿名。
- 可见性复用现有服务层：帖子用 post_visibility_service.load_visible_post /
  post_visibility_predicate，话题用 TopicService，搜索用 SearchService，
  通知用 notification_query_service.visible_notification_query。
  本文件禁止直接绕过这些校验查全量再过滤。
- 只读：本版不含任何写入工具、私信、实时聊天、后台管理、账号安全操作。
- 审计：只记时间、用户 id、工具名、结果码；不记令牌、不记完整正文。
- 限流：进程内按 IP + 工具计数，只读默认每分钟 60 次，超限返回
  rate_limited；生产多实例部署时应换成 Redis 限流（见 README）。

注意：本文件依赖 NonToPython 现有模型与服务层，本地无后端数据库时
只能做语法与静态检查；集成测试应在仓库 CI / 本地后端环境跑。
"""
import base64
import hashlib
import logging
import os
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.core.auth_core import AuthError, verify_token
from app.database import get_db
from app.models.models import ComicCity, ComicEvent, ComicEventFollow, Notification, Post, Topic, topic_followers
from app.services.notification_query_service import visible_notification_query
from app.services.post_visibility_service import load_visible_post, post_visibility_predicate
from app.services.search_service import SearchService
from app.services.topic_service import TopicService

logger = logging.getLogger("nantu.mcp")
router = APIRouter(tags=["MCP"])

MCP_ENABLED = os.environ.get("NONTO_MCP_ENABLED", "1") == "1"
_RATE_LIMIT_PER_MINUTE = int(os.environ.get("NONTO_MCP_RATE_LIMIT_PER_MINUTE", "60"))
_rate_buckets: dict[tuple[str, str], deque] = defaultdict(deque)


def _check_rate_limit(client_ip: str, tool: str) -> bool:
    now = time.time()
    bucket = _rate_buckets[(client_ip, tool)]
    while bucket and bucket[0] < now - 60:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT_PER_MINUTE:
        return False
    bucket.append(now)
    return True


def _optional_user(request: Request):
    """解析 Bearer 令牌；无令牌返回 None，令牌无效抛 401（由调用方转为 MCP 错误）。"""
    auth = request.headers.get("authorization") or ""
    parts = auth.split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        return None
    try:
        return verify_token(parts[1])
    except AuthError as exc:
        raise _MCPError("unauthorized", f"令牌无效或过期：{exc.message}")
    except Exception:
        raise _MCPError("unauthorized", "令牌无效或过期")


class _MCPError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _viewer_id(user) -> int | None:
    return getattr(user, "id", None) if user is not None else None


def _require_self(user):
    if user is None:
        raise _MCPError("unauthorized", "该工具需要本人登录 JWT（mcp:read:self）")
    return user


def _decode_cursor(cursor, default_page: int = 1) -> int:
    if not cursor:
        return default_page
    try:
        return max(1, int(base64.urlsafe_b64decode(str(cursor).encode()).decode()))
    except Exception:
        return default_page


def _encode_cursor(page: int) -> str:
    return base64.urlsafe_b64encode(str(page).encode()).decode()


def _limit(args: dict) -> int:
    try:
        return max(1, min(50, int(args.get("limit", 20))))
    except Exception:
        return 20


def _page_items(query, page: int, limit: int):
    """取 limit+1 条判断是否还有下一页；返回 (items, next_cursor)。"""
    rows = query.offset((page - 1) * limit).limit(limit + 1).all()
    has_more = len(rows) > limit
    return rows[:limit], (_encode_cursor(page + 1) if has_more else None)


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _truncate(text: str | None, limit: int = 2000) -> dict:
    text = text or ""
    if len(text) <= limit:
        return {"text": text, "truncated": False}
    return {"text": text[:limit], "truncated": True}


def _public_user(user) -> dict | None:
    if user is None:
        return None
    return {"id": user.id, "username": user.username,
            "avatar_url": getattr(user, "avatar_url", None), "bio": getattr(user, "bio", None)}


def _serialize_post(post) -> dict:
    body = _truncate(post.content)
    return {"id": post.id, "user_id": post.user_id, "content": body["text"],
            "content_truncated": body["truncated"], "images": post.images,
            "video_url": post.video_url, "visibility": post.visibility,
            "created_at": _iso(post.created_at)}


def _serialize_topic(topic) -> dict:
    return {"id": topic.id, "name": topic.name, "description": topic.description,
            "icon_url": topic.icon_url, "color": topic.color,
            "post_count": topic.post_count, "follower_count": topic.follower_count,
            "is_trending": topic.is_trending, "created_at": _iso(topic.created_at)}


def _serialize_comic(event, city_name: str | None = None) -> dict:
    return {"id": event.id, "name": event.name, "city": city_name, "venue": event.venue,
            "start_date": str(event.start_date) if event.start_date else None,
            "end_date": str(event.end_date) if event.end_date else None,
            "status": event.status, "like_count": event.like_count,
            "comment_count": event.comment_count, "created_at": _iso(event.created_at)}


def _serialize_notification(note) -> dict:
    return {"id": note.id, "notification_type": note.notification_type,
            "title": note.title, "content": note.content, "is_read": note.is_read,
            "created_at": _iso(note.created_at)}


def _envelope(data=None, next_cursor=None):
    out = {"ok": True, "data": data}
    if next_cursor is not None:
        out["next_cursor"] = next_cursor
    return out


# ------------------------------------------------------------------ 工具实现
def _tool_search_posts(db, user, args):
    result = SearchService.search_posts(db, args["query"], page=_decode_cursor(args.get("cursor")),
                                        per_page=_limit(args), user_id=_viewer_id(user))
    items = result.get("items", result) if isinstance(result, dict) else result
    return _envelope([_serialize_post(p) if hasattr(p, "content") else p for p in items])


def _tool_get_post(db, user, args):
    post = load_visible_post(db, int(args["post_id"]), _viewer_id(user))
    if post is None:
        # 统一口径：不存在与不可见不区分，避免泄露未公开内容是否存在
        raise _MCPError("not_found", "不存在或无权访问")
    return _envelope(_serialize_post(post))


def _tool_list_topics(db, user, args):
    page, limit = _decode_cursor(args.get("cursor")), _limit(args)
    try:
        topics = TopicService.get_all_topics(db, page=page, per_page=limit)
        items = topics.get("items", topics) if isinstance(topics, dict) else topics
    except Exception:
        rows, nxt = _page_items(db.query(Topic).order_by(Topic.follower_count.desc()), page, limit)
        return _envelope([_serialize_topic(t) for t in rows], nxt)
    return _envelope([_serialize_topic(t) if hasattr(t, "name") else t for t in items])


def _tool_trending_topics(db, user, args):
    rows = db.query(Topic).filter(Topic.is_trending.is_(True)).order_by(
        Topic.follower_count.desc()).limit(_limit(args)).all()
    return _envelope([_serialize_topic(t) for t in rows])


def _tool_get_topic_posts(db, user, args):
    page, limit = _decode_cursor(args.get("cursor")), _limit(args)
    result = TopicService.get_topic_posts(db, int(args["topic_id"]), page=page,
                                          per_page=limit, current_user_id=_viewer_id(user))
    items = result.get("items", result) if isinstance(result, dict) else result
    return _envelope([_serialize_post(p) if hasattr(p, "content") else p for p in items])


def _tool_search_users(db, user, args):
    result = SearchService.search_users(db, args["query"], page=1,
                                        per_page=_limit(args), current_user_id=_viewer_id(user))
    items = result.get("items", result) if isinstance(result, dict) else result
    return _envelope([_public_user(u) if hasattr(u, "username") else u for u in items])


def _tool_search_topics(db, user, args):
    q = f"%{args['query']}%"
    rows = db.query(Topic).filter(Topic.name.ilike(q)).order_by(
        Topic.follower_count.desc()).limit(_limit(args)).all()
    return _envelope([_serialize_topic(t) for t in rows])


def _tool_comic_timeline(db, user, args):
    page, limit = _decode_cursor(args.get("cursor")), _limit(args)
    query = db.query(ComicEvent).order_by(ComicEvent.start_date.desc())
    if args.get("city"):
        query = query.join(ComicCity, ComicEvent.city_id == ComicCity.id).filter(
            ComicCity.name.ilike(f"%{args['city']}%"))
    rows, nxt = _page_items(query, page, limit)
    cities = {c.id: c.name for c in db.query(ComicCity).all()}
    return _envelope([_serialize_comic(e, cities.get(e.city_id)) for e in rows], nxt)


def _tool_comic_detail(db, user, args):
    event = db.query(ComicEvent).filter(ComicEvent.id == int(args["event_id"])).first()
    if event is None:
        raise _MCPError("not_found", "不存在或无权访问")
    city = db.query(ComicCity).filter(ComicCity.id == event.city_id).first() if event.city_id else None
    return _envelope(_serialize_comic(event, city.name if city else None))


def _tool_my_comics(db, user, args):
    _require_self(user)
    page, limit = _decode_cursor(args.get("cursor")), _limit(args)
    query = db.query(ComicEvent).join(
        ComicEventFollow, ComicEvent.id == ComicEventFollow.event_id).filter(
        ComicEventFollow.user_id == user.id).order_by(ComicEvent.start_date.desc())
    rows, nxt = _page_items(query, page, limit)
    cities = {c.id: c.name for c in db.query(ComicCity).all()}
    return _envelope([_serialize_comic(e, cities.get(e.city_id)) for e in rows], nxt)


def _tool_list_notifications(db, user, args):
    _require_self(user)
    query = visible_notification_query(db, user.id, include_message_notifications=False)
    # 可见性查询已限定本人；此处只做排序与分页，不再放宽
    query = query.order_by(Notification.created_at.desc())
    if args.get("unread_only"):
        query = query.filter(Notification.is_read.is_(False))
    page, limit = _decode_cursor(args.get("cursor")), _limit(args)
    rows, nxt = _page_items(query, page, limit)
    return _envelope([_serialize_notification(n) for n in rows], nxt)


def _tool_list_followed_topics(db, user, args):
    _require_self(user)
    rows = db.query(Topic).join(topic_followers, Topic.id == topic_followers.c.topic_id).filter(
        topic_followers.c.user_id == user.id).order_by(Topic.follower_count.desc()).limit(_limit(args)).all()
    return _envelope([_serialize_topic(t) for t in rows])


def _tool(name, description, properties, required, handler, scope):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required},
            "handler": handler, "scope": scope}


def _s():
    return {"type": "string"}


def _i():
    return {"type": "integer"}


def _b():
    return {"type": "boolean"}


TOOLS = [
    _tool("search_posts", "按关键词搜索帖子（先过可见性校验再返回）",
          {"query": _s(), "limit": _i(), "cursor": _s()}, ["query"], _tool_search_posts, "mcp:read"),
    _tool("get_post", "读取单篇帖子；不可见时统一返回不存在或无权访问",
          {"post_id": _i()}, ["post_id"], _tool_get_post, "mcp:read"),
    _tool("list_topics", "话题列表", {"limit": _i(), "cursor": _s()}, [], _tool_list_topics, "mcp:read"),
    _tool("trending_topics", "热门话题", {"limit": _i()}, [], _tool_trending_topics, "mcp:read"),
    _tool("get_topic_posts", "某话题下的帖子", {"topic_id": _i(), "limit": _i(), "cursor": _s()},
          ["topic_id"], _tool_get_topic_posts, "mcp:read"),
    _tool("search_users", "搜索用户公开资料（不返回邮箱与密码哈希等字段）",
          {"query": _s(), "limit": _i()}, ["query"], _tool_search_users, "mcp:read"),
    _tool("search_topics", "搜索话题", {"query": _s(), "limit": _i()}, ["query"], _tool_search_topics, "mcp:read"),
    _tool("get_comic_timeline", "漫展时间线，可按城市名筛选",
          {"limit": _i(), "cursor": _s(), "city": _s()}, [], _tool_comic_timeline, "mcp:read"),
    _tool("get_comic_detail", "漫展详情", {"event_id": _i()}, ["event_id"], _tool_comic_detail, "mcp:read"),
    _tool("list_my_comics", "我关注的漫展（需本人 JWT，mcp:read:self）",
          {"limit": _i(), "cursor": _s()}, [], _tool_my_comics, "mcp:read:self"),
    _tool("list_notifications", "我自己的通知（需本人 JWT，不含私信通知）",
          {"limit": _i(), "cursor": _s(), "unread_only": _b()}, [], _tool_list_notifications, "mcp:read:self"),
    _tool("list_followed_topics", "我关注的话题（需本人 JWT，mcp:read:self）",
          {"limit": _i()}, [], _tool_list_followed_topics, "mcp:read:self"),
]
TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


def _public_tools():
    return [{k: t[k] for k in ("name", "description", "inputSchema")} for t in TOOLS]


def _handle_rpc(request: Request, db: Session, body: dict):
    method = body.get("method", "")
    rid = body.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": body.get("params", {}).get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}}, "serverInfo": {"name": "nantu-mcp", "version": "1.0.0"}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": _public_tools()}}
    if method == "tools/call":
        params = body.get("params", {}) or {}
        name, args = params.get("name"), params.get("arguments") or {}
        tool = TOOLS_BY_NAME.get(name)
        if tool is None:
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": '{"ok": false, "error": "not_found: 未知工具"}'}], "isError": True}}
        if not MCP_ENABLED:
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": '{"ok": false, "error": "forbidden_scope: MCP 总开关已关闭"}'}], "isError": True}}
        client_ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(client_ip, name):
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": '{"ok": false, "error": "rate_limited: 超出只读限流，请稍后重试"}'}], "isError": True}}
        try:
            user = _optional_user(request)
            result = tool["handler"](db, user, args)
            logger.info("mcp_tool user_id=%s tool=%s ok=true",
                        _viewer_id(user), name)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": _json(result)}]}}
        except _MCPError as exc:
            logger.info("mcp_tool user_id=%s tool=%s ok=false code=%s",
                        _viewer_id(_safe_user(request)), name, exc.code)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": _json({"ok": False, "error": f"{exc.code}: {exc.message}"})}], "isError": True}}
        except Exception as exc:  # 不把内部异常细节原样返回给客户端
            logger.exception("mcp_tool_failed tool=%s", name)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": '{"ok": false, "error": "backend_error: 服务端处理失败"}'}], "isError": True}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"未知方法: {method}"}}


def _safe_user(request: Request):
    try:
        return _optional_user(request)
    except Exception:
        return None


def _json(value) -> str:
    import json as _json_lib
    return _json_lib.dumps(value, ensure_ascii=False, default=str)


@router.get("/mcp")
def mcp_info():
    return {"name": "nantu-mcp", "version": "1.0.0", "enabled": MCP_ENABLED,
            "tools": [t["name"] for t in TOOLS]}


@router.post("/mcp")
async def mcp_endpoint(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    if isinstance(body, dict) and str(body.get("method", "")).startswith("notifications/"):
        return Response(status_code=202)
    result = _handle_rpc(request, db, body if isinstance(body, dict) else {})
    return JSONResponse(content=result)
