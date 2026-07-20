"""
NanTuPy - FastAPI 应用入口
从 Flask 重构的社交平台后端
"""
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
import os
import time

from app.core.config import Config
from app.database import init_db
from app.routers import auth, posts, friends, interactions, chat, notifications, ws
from app.routers import search, topics, upload, recommendations, blocks, reports, health, admin, comic, roles
from app.routers import communities, push

# 配置日志：生产环境用 INFO，避免 DEBUG 级别把 SQL/敏感数据写进日志。
# 通过 LOG_LEVEL 环境变量覆盖（DEBUG/INFO/WARNING）。
_log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('app.log', encoding='utf-8')
    ]
)

# WS 日志保持 INFO 级别（连接/断开/认证可见，收发详细内容仅 DEBUG）
for _ws_logger_name in ['app.routers.ws', 'app.ws_manager']:
    _l = logging.getLogger(_ws_logger_name)
    _l.setLevel(logging.INFO)
    _l.propagate = True

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("Starting NanTuPy server...")
    init_db()
    logger.info("Database tables initialized")
    logger.info("WS endpoint: ws://0.0.0.0:5000/ws")
    logger.info("WS logging: enabled (CONNECT/DISCONNECT/RECV/SEND_SEQ/SEND_RAW)")
    yield
    logger.info("Shutting down NanTuPy server...")


# 安全：生产环境通过 HIDE_API_DOCS=1 关闭 openapi.json / docs / redoc，
# 避免暴露全部接口结构。开发环境保留（默认不设该变量）。
_hide_docs = os.environ.get('HIDE_API_DOCS', '0') == '1'
app = FastAPI(
    title="NanTuPy",
    description="社交平台后端 API (FastAPI 重构版)",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None if _hide_docs else "/docs",
    redoc_url=None if _hide_docs else "/redoc",
    openapi_url=None if _hide_docs else "/openapi.json",
)

# CORS 中间件
# 开发环境允许 localhost/127.0.0.1/私有局域网来源，方便 Flutter Web 调试；
# 生产环境默认只允许正式前端域名，可用 CORS_ORIGINS 覆盖。
_cors_settings = Config.get_cors_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_settings["allow_origins"],
    allow_origin_regex=_cors_settings["allow_origin_regex"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

def _get_perf_slow_request_ms():
    try:
        return int(os.environ.get('PERF_SLOW_REQUEST_MS', '500'))
    except ValueError:
        logger.warning('Invalid PERF_SLOW_REQUEST_MS value; falling back to 500')
        return 500


_perf_slow_request_ms = _get_perf_slow_request_ms()
_perf_logger = logging.getLogger('app.performance')


@app.middleware("http")
async def log_request_timing(request, call_next):
    start = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        path = request.url.path
        if path.startswith('/api/'):
            log_payload = {
                'method': request.method,
                'path': path,
                'status_code': status_code,
                'elapsed_ms': elapsed_ms,
            }
            if elapsed_ms >= _perf_slow_request_ms:
                _perf_logger.warning('slow_request %s', log_payload)
            else:
                _perf_logger.debug('request_timing %s', log_payload)

# COOP / COEP 安全头 — Flutter Web (CanvasKit + WASM) 需要 SharedArrayBuffer
# 注意：只应在静态 HTML 页面响应上设置，API 和 OPTIONS 预检不需要
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    # 跳过 API 路由和 OPTIONS 预检
    path = request.url.path
    if request.method == "OPTIONS" or path.startswith("/api/") or path.startswith("/ws"):
        return response
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
    # 配合 COEP 需要的 CORP 头
    response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
    return response

# 注册路由
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(posts.router, prefix="/api/posts", tags=["Posts"])
app.include_router(friends.router, prefix="/api/friends", tags=["Friends"])
app.include_router(interactions.router, prefix="/api", tags=["Interactions"])
app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["Notifications"])
app.include_router(search.router, prefix="/api/search", tags=["Search"])
app.include_router(topics.router, prefix="/api/topics", tags=["Topics"])
app.include_router(upload.router, prefix="/api/upload", tags=["Upload"])
app.include_router(recommendations.router, prefix="/api/recommendations", tags=["Recommendations"])
app.include_router(blocks.router, prefix="/api/blocks", tags=["Blocks"])
app.include_router(reports.router, prefix="/api/reports", tags=["Reports"])
app.include_router(health.router, prefix="", tags=["Health"])
app.include_router(admin.router, prefix="", tags=["Admin"])
app.include_router(comic.router, prefix="/api/comic", tags=["Comic"])
app.include_router(roles.router, tags=["Roles"])
app.include_router(communities.router, prefix="/api/communities", tags=["Communities"])
app.include_router(push.router, prefix="/api/push", tags=["Push"])
app.add_api_websocket_route("/ws", ws.websocket_endpoint)


@app.get("/")
async def root():
    return {
        "message": "Welcome to NanTuPy API",
        "version": "2.0.0",
        "framework": "FastAPI",
        "endpoints": {
            "auth": "/api/auth",
            "posts": "/api/posts",
            "friends": "/api/friends",
            "chat": "/api/chat",
        }
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy", "message": "NanTuPy API is running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=5000, reload=True)
