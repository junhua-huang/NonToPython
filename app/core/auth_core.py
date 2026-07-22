"""
鉴权核心模块 — 纯函数式 Token 校验

唯一鉴权入口，HTTP / WebSocket 统一调用。
不依赖 FastAPI Request / WebSocket 对象，可独立测试。
"""
from typing import Optional
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.models import User
from app.core.config import Config
from app.services.moderation_errors import AccountDisabled


class AuthError(Exception):
    """鉴权异常"""
    def __init__(self, message: str, code: int = 401):
        self.message = message
        self.code = code
        super().__init__(message)


def verify_token(token: str) -> User:
    """
    纯函数：验证 JWT Token 并返回用户对象。

    Args:
        token: JWT access_token 字符串

    Returns:
        User: 数据库中的用户对象

    Raises:
        AuthError: Token 无效 / 过期 / 用户不存在
    """
    if not token or not token.strip():
        raise AuthError("access_token is required", 401)

    token = token.strip()

    # 1. 解码 JWT
    try:
        payload = jwt.decode(token, Config.JWT_SECRET_KEY, algorithms=["HS256"])
        user_id_raw = payload.get("sub")
        if user_id_raw is None:
            raise AuthError("Invalid token: missing subject", 401)
        user_id = int(user_id_raw)
    except JWTError as e:
        raise AuthError(f"Token invalid or expired: {str(e)}", 401)
    except (ValueError, TypeError):
        raise AuthError("Invalid token format", 401)

    # 2. 查数据库验证用户存在
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            raise AuthError("User not found", 401)
        if user.is_active is False:
            raise AccountDisabled()
        # 把 user 从 session 中分离，避免调用方闭包 session 问题
        db.expunge(user)
        return user
    finally:
        db.close()
