"""
健康检查路由 - FastAPI 重构版
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db

router = APIRouter()


@router.get("/")
def health_check(db: Session = Depends(get_db)):
    """数据库连接健康检查"""
    response = {
        "status": "healthy",
        "timestamp": None,
        "database": "unknown",
    }
    try:
        result = db.execute(text("SELECT 1")).fetchone()
        if result and result[0] == 1:
            response["database"] = "connected"
    except Exception as e:
        response["status"] = "unhealthy"
        response["database"] = f"error: {str(e)}"

    from datetime import datetime
    response["timestamp"] = datetime.utcnow().isoformat() + "Z"
    return response


@router.get("/ping")
def ping():
    """简单 ping 检查"""
    return {"status": "pong"}