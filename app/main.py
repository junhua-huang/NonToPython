"""
NanTuPy - FastAPI 应用入口
从 Flask 重构的社交平台后端
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from app.database import init_db
from app.routers import auth, posts, friends, interactions, chat, notifications, ws
from app.routers import search, topics, upload, recommendations, blocks, reports, health, admin, comic

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('app.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("Starting NanTuPy server...")
    init_db()
    logger.info("Database tables initialized")
    yield
    logger.info("Shutting down NanTuPy server...")


app = FastAPI(
    title="NanTuPy",
    description="社交平台后端 API (FastAPI 重构版)",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
