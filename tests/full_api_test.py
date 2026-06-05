"""
NanTuPy 全量 API 接口测试脚本
覆盖所有 HTTP 端点和 WebSocket 端点（Auth/Posts/Comments/Friends/Chat/Notifications/Search/Topics/Upload/Recommendations/Blocks/Reports/Comic/Admin/Health/WebSocket）

用法: python tests/full_api_test.py [--host HOST] [--port PORT]
"""

import requests
import json
import time
import sys
import uuid
import asyncio
from typing import Optional

# ============================================================
# 配置
# ============================================================
BASE_HOST = "127.0.0.1"
BASE_PORT = 5000

for i, arg in enumerate(sys.argv):
    if arg == "--host" and i + 1 < len(sys.argv):
        BASE_HOST = sys.argv[i + 1]
    elif arg == "--port" and i + 1 < len(sys.argv):
        BASE_PORT = int(sys.argv[i + 1])

BASE_URL = f"http://{BASE_HOST}:{BASE_PORT}"
API_BASE = f"{BASE_URL}/api"
WS_URL = f"ws://{BASE_HOST}:{BASE_PORT}/ws"

HEADERS = {"Content-Type": "application/json"}

# ============================================================
# 全局状态
# ============================================================
results = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "total": 0}
token: Optional[str] = None
user_id: Optional[int] = None
test_email = f"test_{uuid.uuid4().hex[:8]}@nantu.test"
test_password = "Test@123456"
test_username = f"tester_{uuid.uuid4().hex[:6]}"

# 测试中产生的 ID（用于级联测试）
ids: dict = {
    "post_id": None,
    "comment_id": None,
    "friendship_id": None,
    "topic_id": None,
    "conversation_id": None,
    "notification_id": None,
    "event_id": None,
    "comic_comment_id": None,
}

# ============================================================
# 工具函数
# ============================================================

def auth_headers() -> dict:
    h = dict(HEADERS)
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _record(method: str, path: str, resp, expect_ok: bool = True, label: str = ""):
    results["total"] += 1
    name = label or f"{method.upper()} {path}"
    try:
        status = resp.status_code
        if expect_ok:
            ok = 200 <= status < 300
        else:
            ok = status >= 400
        if ok:
            results["passed"] += 1
            print(f"  PASS  {method:6s} {path:<50s} -> {status}")
        else:
            results["failed"] += 1
            body = resp.text[:200]
            print(f"  FAIL  {method:6s} {path:<50s} -> {status}  {body}")
    except Exception as e:
        results["failed"] += 1
        print(f"  FAIL  {method:6s} {path:<50s} -> ERROR: {e}")


def get(path, expect_ok=True, label=""):
    resp = requests.get(f"{API_BASE}{path}", headers=auth_headers(), timeout=15)
    _record("GET", path, resp, expect_ok, label)
    return resp


def post(path, data=None, expect_ok=True, label=""):
    resp = requests.post(f"{API_BASE}{path}", json=data or {}, headers=auth_headers(), timeout=15)
    _record("POST", path, resp, expect_ok, label)
    return resp


def put(path, data=None, expect_ok=True, label=""):
    resp = requests.put(f"{API_BASE}{path}", json=data or {}, headers=auth_headers(), timeout=15)
    _record("PUT", path, resp, expect_ok, label)
    return resp


def delete(path, expect_ok=True, label=""):
    resp = requests.delete(f"{API_BASE}{path}", headers=auth_headers(), timeout=15)
    _record("DELETE", path, resp, expect_ok, label)
    return resp


def root_get(path, expect_ok=True, label=""):
    """不带 /api 前缀的请求"""
    resp = requests.get(f"{BASE_URL}{path}", headers=auth_headers(), timeout=15)
    _record("GET", path, resp, expect_ok, label)
    return resp


def root_post(path, data=None, expect_ok=True, label=""):
    resp = requests.post(f"{BASE_URL}{path}", json=data or {}, headers=auth_headers(), timeout=15)
    _record("POST", path, resp, expect_ok, label)
    return resp


def root_delete(path, expect_ok=True, label=""):
    resp = requests.delete(f"{BASE_URL}{path}", headers=auth_headers(), timeout=15)
    _record("DELETE", path, resp, expect_ok, label)
    return resp


def print_section(title: str):
    print(f"\n{'='*65}")
    print(f"  {title}")
    print(f"{'='*65}")


# ============================================================
# 1. Health（不需要认证）
# ============================================================

def test_health():
    print_section("Health")

    # GET /
    r = root_get("/")
    root_get("/ping")
    root_get("/health")

    # 检查服务器是否在线
    try:
        requests.get(f"{BASE_URL}/health", timeout=5)
    except requests.ConnectionError:
        print("\n  *** 服务器未启动！请先启动服务器 ***\n")
        sys.exit(1)

# ============================================================
# 2. Auth
# ============================================================

def test_auth():
    global token, user_id
    print_section("Auth – 注册/登录/Profile")

    # 注册
    r = post("/auth/register", {
        "username": test_username,
        "email": test_email,
        "password": test_password,
    })
    if 200 <= r.status_code < 300:
        data = r.json()
        token = data.get("access_token")
        user_id = data.get("user_id")
        print(f"  注册成功: user_id={user_id}, username={test_username}")
    else:
        # 可能已注册过，尝试登录
        print(f"  注册返回 {r.status_code}，尝试登录...")

    # 登录
    r = post("/auth/login", {"email": test_email, "password": test_password})
    if 200 <= r.status_code < 300:
        data = r.json()
        token = data.get("access_token")
        user_id = data.get("user_id")

    # 获取当前用户信息
    get("/auth/me")
    get("/auth/profile")

    # 更新个人资料
    put("/auth/profile", {"bio": "Test bio from full_api_test"})

    # 修改密码
    post("/auth/change-password", {"old_password": test_password, "new_password": test_password}, expect_ok=True)

    # 获取公开用户信息
    if user_id:
        get(f"/auth/users/{user_id}")

    # 隐私设置
    get("/auth/privacy")
    put("/auth/privacy", {"show_email": False})

    # Token 刷新
    post("/auth/refresh")

    # 忘记密码（不依赖已登录）
    post("/auth/forgot-password", {"email": test_email})

    # 重置密码（获取code后重置）
    # 由于我们不知道生成的code，这里只测接口可达性
    post("/auth/reset-password", {"token": "000000", "new_password": "Fake@123"}, expect_ok=False)


# ============================================================
# 3. Posts
# ============================================================

def test_posts():
    global ids
    print_section("Posts – CRUD/浏览/热门")

    # 创建帖子
    r = post("/posts/", data={"content": "Test post from full_api_test", "visibility": "public"})
    if 200 <= r.status_code < 300:
        data = r.json()
        ids["post_id"] = data.get("post", {}).get("id")
        print(f"  创建的 post_id: {ids['post_id']}")

    # 获取帖子列表
    get("/posts/")

    # 获取单个帖子
    if ids["post_id"]:
        get(f"/posts/{ids['post_id']}")

    # 更新帖子
    if ids["post_id"]:
        put(f"/posts/{ids['post_id']}", {"content": "Updated test post"})

    # 获取用户帖子
    if user_id:
        get(f"/posts/user/{user_id}")

    # 获取用户点赞的帖子
    if user_id:
        get(f"/posts/user/{user_id}/liked")

    # 记录浏览
    if ids["post_id"]:
        post(f"/posts/{ids['post_id']}/view")

    # 帖子统计
    if ids["post_id"]:
        get(f"/posts/{ids['post_id']}/stats")


# ============================================================
# 4. Interactions（点赞 + 评论）
# ============================================================

def test_interactions():
    global ids
    print_section("Interactions – 点赞/评论")

    if not ids["post_id"]:
        print("  SKIP: 没有可用的 post_id")
        results["skipped"] += 10
        return

    # 点赞帖子
    post(f"/posts/{ids['post_id']}/like")

    # 获取点赞列表
    get(f"/posts/{ids['post_id']}/likes")

    # 创建评论
    r = post(f"/posts/{ids['post_id']}/comments", {
        "content": "Test comment from full_api_test",
    })
    if 200 <= r.status_code < 300:
        ids["comment_id"] = r.json().get("comment", {}).get("id")

    # 获取帖子评论
    get(f"/posts/{ids['post_id']}/comments")

    if ids["comment_id"]:
        # 获取评论详情
        get(f"/comments/{ids['comment_id']}")

        # 更新评论
        put(f"/comments/{ids['comment_id']}", {"content": "Updated comment"})

        # 点赞评论
        post(f"/comments/{ids['comment_id']}/like")

        # 取消点赞评论
        delete(f"/comments/{ids['comment_id']}/like", expect_ok=True)

    # 取消点赞帖子
    delete(f"/posts/{ids['post_id']}/like", expect_ok=True)


# ============================================================
# 5. Friends
# ============================================================

def test_friends():
    global ids
    print_section("Friends – 请求/接受/列表")

    # 获取好友列表
    get("/friends/")

    # 获取待处理请求
    get("/friends/requests/pending")
    get("/friends/requests/sent")
    get("/friends/requests/received")

    # 好友推荐
    get("/friends/recommendations")

    # 检查状态（对自己）
    if user_id:
        get(f"/friends/status/{user_id}")

    # 好友数量
    if user_id:
        get(f"/friends/count/{user_id}")

    # 发送好友请求会失败（自请求），但接口可达
    if user_id:
        post("/friends/request", {"receiver_id": user_id}, expect_ok=False)


# ============================================================
# 6. Chat
# ============================================================

def test_chat():
    global ids
    print_section("Chat – 会话/消息")

    # 获取会话列表
    r = get("/chat/conversations")

    # 在线用户
    get("/chat/users/online")

    # 未读消息数
    get("/chat/unread-count")

    # 如果有会话，测试消息
    if user_id:
        get(f"/chat/users/{user_id}/status")


# ============================================================
# 7. Notifications
# ============================================================

def test_notifications():
    global ids
    print_section("Notifications – 列表/已读/清空")

    # 获取通知列表
    r = get("/notifications/")
    if 200 <= r.status_code < 300:
        notifs = r.json().get("notifications", [])
        if notifs:
            ids["notification_id"] = notifs[0]["id"]

    # 未读数量
    get("/notifications/unread-count")

    # 标记单个已读
    if ids["notification_id"]:
        post(f"/notifications/{ids['notification_id']}/read")

    # 全部已读
    post("/notifications/mark-all-read")

    # 通知设置
    get("/notifications/settings")
    put("/notifications/settings", {"notify_push": True, "notify_message": True})


# ============================================================
# 8. Search
# ============================================================

def test_search():
    print_section("Search – 用户/帖子/话题/标签")

    get("/search/users", {"q": test_username} if test_username else {"q": "test"})
    get("/search/posts", {"q": "test"})
    get("/search/global", {"q": "test"})
    get("/search/hashtag/test")
    get("/search/trending-hashtags")
    get("/search/suggest/users", {"prefix": "t"})
    get("/search/topics", {"q": "test"})
    get("/search/mention-suggestions", {"prefix": "t"})
    get("/search/history")
    post("/search/history", {"query": "test full api", "type": "global"})
    delete("/search/history")


# ============================================================
# 9. Topics
# ============================================================

def test_topics():
    global ids
    print_section("Topics – 热门/搜索/关注")

    get("/topics/")
    get("/topics/trending")
    get("/topics/followed")
    get("/topics/my-referenced")
    get("/topics/suggest", {"prefix": "t"})

    # 创建话题
    r = post("/topics/", {"name": f"TestTopic_{uuid.uuid4().hex[:6]}", "description": "Test"})
    if 200 <= r.status_code < 300:
        ids["topic_id"] = r.json().get("topic", {}).get("id")

    if ids["topic_id"]:
        get(f"/topics/{ids['topic_id']}")
        get(f"/topics/{ids['topic_id']}/posts")
        post(f"/topics/{ids['topic_id']}/follow")
        post(f"/topics/{ids['topic_id']}/unfollow")

    # 按名称获取
    get("/topics/name/Test", expect_ok=False)  # 可能不存在


# ============================================================
# 10. Upload
# ============================================================

def test_upload():
    print_section("Upload – 预签名/确认/删除")

    post("/upload/presign", {"filename": "test.jpg", "file_type": "jpg", "upload_type": "avatar"})
    get("/upload/info", {"url": "https://example.com/test.jpg"}, expect_ok=False)


# ============================================================
# 11. Recommendations
# ============================================================

def test_recommendations():
    print_section("Recommendations – Feed/热门/推荐")

    get("/recommendations/feed")
    get("/recommendations/trending")
    get("/recommendations/users/suggest")
    get("/recommendations/friends/recommend")

    if ids["post_id"]:
        get(f"/recommendations/posts/{ids['post_id']}/related")


# ============================================================
# 12. Blocks
# ============================================================

def test_blocks():
    print_section("Blocks – 屏蔽/取消/列表")

    get("/blocks/")
    if user_id:
        get(f"/blocks/check/{user_id}")

    # 不真的屏蔽自己，只测接口可达
    if user_id:
        post("/blocks/", {"user_id": user_id}, expect_ok=False)  # 不能屏蔽自己


# ============================================================
# 13. Reports
# ============================================================

def test_reports():
    print_section("Reports – 举报")

    get("/reports/")
    get("/reports/check", {"report_type": "post", "target_id": 1})

    # 举报
    if ids["post_id"]:
        post("/reports/post", {"post_id": ids["post_id"], "reason": "test"})
    if ids["comment_id"]:
        post("/reports/comment", {"comment_id": ids["comment_id"], "reason": "test"})


# ============================================================
# 14. Comic
# ============================================================

def test_comic():
    global ids
    print_section("Comic – 漫展列表/关注/评论")

    get("/comic/cities")
    get("/comic/tags")
    get("/comic/events")
    get("/comic/events", {"city": "上海"})
    get("/comic/my-events")
    get("/comic/my-followed")

    # 创建漫展
    r = post("/comic/events", {
        "name": f"Test Comic Expo {uuid.uuid4().hex[:4]}",
        "cityId": 1,
        "venue": "Test Venue",
        "startDate": "2026-07-01",
        "endDate": "2026-07-03",
        "intro": "Test comic event from full_api_test",
    })
    if 200 <= r.status_code < 300:
        ids["event_id"] = r.json().get("id")

    if ids["event_id"]:
        get(f"/comic/events/{ids['event_id']}")
        post(f"/comic/events/{ids['event_id']}/follow")

        # 发表评论
        r2 = post(f"/comic/events/{ids['event_id']}/comments",
                  {"content": "Test comic comment"})
        if 200 <= r2.status_code < 300:
            ids["comic_comment_id"] = r2.json().get("comment", {}).get("id")

        get(f"/comic/events/{ids['event_id']}/comments")

    if ids["comic_comment_id"]:
        post(f"/comic/events/comments/{ids['comic_comment_id']}/like")
        get(f"/comic/events/comments/{ids['comic_comment_id']}/replies")

    # 关注效果
    if ids["event_id"]:
        post(f"/comic/events/{ids['event_id']}/follow")  # toggle: 取消关注


# ============================================================
# 15. Admin（需要管理员权限）
# ============================================================

def test_admin():
    print_section("Admin – 敏感词管理（预期 403/401）")

    # 普通用户无权限
    root_get("/sensitive-words", expect_ok=False)


# ============================================================
# 16. WebSocket
# ============================================================

def test_websocket():
    print_section("WebSocket – 连接/认证/ping-pong")

    if not token:
        print("  SKIP: 没有 token")
        results["skipped"] += 1
        return

    try:
        import websockets
    except ImportError:
        print("  SKIP: websockets 库未安装")
        results["skipped"] += 1
        return

    async def _ws_test():
        try:
            uri = f"{WS_URL}/?token={token}"
            async with websockets.connect(uri, max_size=2**23) as ws:
                # 等待 connected 消息
                msg = await asyncio.wait_for(ws.recv(), timeout=8)
                data = json.loads(msg)
                if data.get("type") == "connected":
                    results["passed"] += 1
                    results["total"] += 1
                    print(f"  PASS  WS    /ws/?token=...{' '*35} -> connected")
                else:
                    results["failed"] += 1
                    results["total"] += 1
                    print(f"  FAIL  WS    unexpected: {data}")

                # ping-pong
                await ws.send(json.dumps({"type": "ping"}))
                pong = await asyncio.wait_for(ws.recv(), timeout=5)
                pong_data = json.loads(pong)
                if pong_data.get("type") == "pong":
                    results["passed"] += 1
                    results["total"] += 1
                    print(f"  PASS  WS    ping/pong{' '*41} -> OK")
                else:
                    results["failed"] += 1
                    results["total"] += 1
                    print(f"  FAIL  WS    ping/pong unexpected: {pong_data}")

        except Exception as e:
            results["failed"] += 1
            results["total"] += 1
            print(f"  FAIL  WS    /ws/?token=...{' '*35} -> {type(e).__name__}: {e}")

    try:
        asyncio.get_event_loop().run_until_complete(_ws_test())
    except Exception as e:
        results["failed"] += 1
        results["total"] += 1
        print(f"  FAIL  WS    event_loop{' '*41} -> {e}")


# ============================================================
# 17. Cleanup
# ============================================================

def test_cleanup():
    global ids
    print_section("Cleanup – 删除测试数据")

    # 删除评论
    if ids["comment_id"]:
        delete(f"/comments/{ids['comment_id']}", expect_ok=True)

    # 删除漫展评论
    if ids["comic_comment_id"]:
        delete(f"/comic/events/comments/{ids['comic_comment_id']}", expect_ok=True)

    # 删除帖子
    if ids["post_id"]:
        delete(f"/posts/{ids['post_id']}", expect_ok=True)

    # 删除话题
    if ids["topic_id"]:
        delete(f"/topics/{ids['topic_id']}", expect_ok=True)

    # 删除通知
    if ids["notification_id"]:
        delete(f"/notifications/{ids['notification_id']}", expect_ok=True)

    # 清空所有通知
    delete("/notifications/clear-all", expect_ok=True)

    # 删除漫展
    if ids["event_id"]:
        delete_ev = requests.delete(
            f"{API_BASE}/comic/events/{ids['event_id']}",
            headers=auth_headers(),
            timeout=15,
        )
        # 事件删除可能不支持，忽略


# ============================================================
# Main
# ============================================================

def print_summary():
    print_section("测试结果摘要")
    total = results["total"]
    passed = results["passed"]
    failed = results["failed"]
    errors = results["errors"]
    skipped = results["skipped"]

    print(f"  Total:   {total}")
    print(f"  Passed:  {passed}")
    print(f"  Failed:  {failed}")
    print(f"  Errors:  {errors}")
    print(f"  Skipped: {skipped}")

    if total > 0:
        rate = passed / total * 100
        print(f"  通过率:  {rate:.1f}%")

    if failed + errors == 0:
        print("\n  所有测试通过!")
    else:
        print(f"\n  有 {failed + errors} 个测试未通过，请检查上方日志。")

    return failed + errors


def main():
    print("=" * 65)
    print(f"  NanTuPy 全量 API 接口测试")
    print(f"  Base URL: {BASE_URL}")
    print(f"  API Base: {API_BASE}")
    print(f"  WS URL:   {WS_URL}")
    print("=" * 65)

    # 检查服务器
    test_health()

    # 按依赖顺序执行
    test_auth()

    if not token:
        print("\n  *** Token 获取失败，跳过后续需要认证的测试 ***\n")
        print_summary()
        return 1

    test_posts()
    test_interactions()
    test_friends()
    test_chat()
    test_notifications()
    test_search()
    test_topics()
    test_upload()
    test_recommendations()
    test_blocks()
    test_reports()
    test_comic()
    test_admin()
    test_websocket()
    test_cleanup()

    # 最终摘要
    exit_code = print_summary()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
