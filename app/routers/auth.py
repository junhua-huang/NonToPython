"""
认证路由 - FastAPI 版本
注册 / 登录 / Token 刷新 / 密码重置 / 个人资料 / 隐私设置
"""
import re
import random
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Body, Query
from fastapi.security import HTTPBearer
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session
import bcrypt as bcrypt_lib
from jose import jwt, JWTError

from app.database import get_db
from app.models.models import User
from app.core.config import Config
from werkzeug.security import check_password_hash
from app.dependencies import get_current_user, get_optional_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Auth"])
security = HTTPBearer()

# ---- 内存密码重置令牌（简单实现，重启后失效） ----
_reset_tokens: dict[str, dict] = {}  # token -> {user_id, expires_at}


# ============================================================
# Pydantic Schemas
# ============================================================

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        v = v.strip()
        if not (3 <= len(v) <= 30):
            raise ValueError("Username must be 3-30 characters long")
        # 允许中文、英文、数字、下划线、点号
        if not re.match(r'^[\u4e00-\u9fff a-zA-Z0-9_.]+$', v):
            raise ValueError("Username can only contain Chinese, English letters, numbers, underscores, and dots")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError("Invalid email format")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        return v


class LoginRequest(BaseModel):
    login: str = ""   # username or email (backward compat)
    email: str = ""   # alias for login (frontend sends email)
    password: str

    def get_login(self) -> str:
        """优先 email 字段，回退 login"""
        return (self.email or self.login).strip().lower()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str


class AuthResponse(BaseModel):
    message: str
    access_token: str | None = None
    user_id: int | None = None
    username: str | None = None
    user: dict | None = None


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    display_name: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    cover_photo_url: str | None = None
    created_at: str | None = None


class PublicUserResponse(BaseModel):
    id: int
    username: str
    display_name: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    cover_photo_url: str | None = None
    created_at: str | None = None


class ProfileUpdateRequest(BaseModel):
    display_name: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    cover_photo_url: str | None = None


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class PrivacySettingsResponse(BaseModel):
    profile_visibility: str = "public"
    post_default_visibility: str = "public"
    show_email: bool = False
    allow_search: bool = True
    allow_friend_requests: str = "everyone"


class PrivacySettingsUpdateRequest(BaseModel):
    profile_visibility: str | None = None
    post_default_visibility: str | None = None
    show_email: bool | None = None
    allow_search: bool | None = None
    allow_friend_requests: str | None = None


# ============================================================
# 内部工具函数
# ============================================================

def _build_user_response(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "display_name": user.username,
        "bio": user.bio,
        "avatar_url": user.avatar_url,
        "cover_photo_url": user.cover_photo_url,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _build_public_user_response(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.username,
        "bio": user.bio,
        "avatar_url": user.avatar_url,
        "cover_photo_url": user.cover_photo_url,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


# ============================================================
# Endpoints — 注册/登录
# ============================================================

@router.post("/register", response_model=AuthResponse)
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    """用户注册"""
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(status_code=409, detail="Email already exists")

    user = User(
        username=data.username,
        email=data.email,
        password_hash=bcrypt_lib.hashpw(data.password.encode('utf-8'), bcrypt_lib.gensalt()).decode('utf-8'),
        bio="",
        avatar_url=f"https://api.dicebear.com/7.x/initials/svg?seed={data.username}",
        cover_photo_url="",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    access_token = _create_token(user)
    logger.info(f"New user registered: {user.username} (ID: {user.id})")
    return AuthResponse(
        message="Registration successful",
        access_token=access_token,
        user_id=user.id,
        username=user.username,
        user=_build_user_response(user),
    )


@router.post("/login", response_model=AuthResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    """用户登录（支持用户名或邮箱）"""
    login_value = data.get_login()
    if not login_value:
        raise HTTPException(status_code=400, detail="email or login is required")

    user = (
        db.query(User)
        .filter((User.username == login_value) | (User.email == login_value))
        .first()
    )
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username/email or password")

    # 兼容 scrypt (Flask 旧版) 和 bcrypt (FastAPI 新版) 两种哈希格式
    if user.password_hash.startswith("scrypt:"):
        if not check_password_hash(user.password_hash, data.password):
            raise HTTPException(status_code=401, detail="Invalid username/email or password")
    else:
        if not bcrypt_lib.checkpw(data.password.encode('utf-8'), user.password_hash.encode('utf-8')):
            raise HTTPException(status_code=401, detail="Invalid username/email or password")

    access_token = _create_token(user)
    logger.info(f"User logged in: {user.username} (ID: {user.id})")
    return AuthResponse(
        message="Login successful",
        access_token=access_token,
        user_id=user.id,
        username=user.username,
        user=_build_user_response(user),
    )


# ============================================================
# #1   GET /auth/profile（/me 别名）
# ============================================================

@router.get("/me")
def get_current_user_info_me(user: User = Depends(get_current_user)):
    """获取当前登录用户信息"""
    return _build_user_response(user)


@router.get("/profile")
def get_current_user_info(user: User = Depends(get_current_user)):
    """获取当前登录用户信息（/profile 别名，兼容前端）"""
    return _build_user_response(user)


# ============================================================
# #8   PUT /auth/profile  修改个人资料
# ============================================================

@router.put("/profile")
def update_profile(
    data: ProfileUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """修改当前用户个人资料"""
    current = db.query(User).filter(User.id == user.id).first()
    if data.display_name is not None:
        # display_name 映射为 username（如业务允许）
        pass
    if data.bio is not None:
        current.bio = data.bio
    if data.avatar_url is not None:
        current.avatar_url = data.avatar_url
    if data.cover_photo_url is not None:
        current.cover_photo_url = data.cover_photo_url
    try:
        db.commit()
        return {"message": "Profile updated", "user": _build_user_response(current)}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# #9   POST /auth/change-password  修改密码
# ============================================================

@router.post("/change-password")
def change_password(
    data: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """修改当前用户密码"""
    current = db.query(User).filter(User.id == user.id).first()
    # 兼容 scrypt (Flask 旧版) 和 bcrypt (FastAPI 新版) 两种哈希格式
    if current.password_hash.startswith("scrypt:"):
        if not check_password_hash(current.password_hash, data.old_password):
            raise HTTPException(status_code=400, detail="Old password is incorrect")
    else:
        if not bcrypt_lib.checkpw(data.old_password.encode('utf-8'), current.password_hash.encode('utf-8')):
            raise HTTPException(status_code=400, detail="Old password is incorrect")

    current.password_hash = bcrypt_lib.hashpw(data.new_password.encode('utf-8'), bcrypt_lib.gensalt()).decode('utf-8')
    try:
        db.commit()
        return {"message": "Password changed successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# #10  GET /auth/users/{user_id}  公开用户信息
# ============================================================

@router.get("/users/{user_id}")
def get_user_info(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_optional_user),
):
    """获取用户公开信息（可选认证）"""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    return _build_public_user_response(target)


# ============================================================
# #11  DELETE /auth/account  注销账号
# ============================================================

@router.delete("/account")
def delete_account(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """注销当前用户账号及关联数据"""
    current = db.query(User).filter(User.id == user.id).first()
    try:
        db.delete(current)
        db.commit()
        logger.info(f"User account deleted: {user.username} (ID: {user.id})")
        return {"message": "Account deleted successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# #12  POST /auth/forgot-password  忘记密码
# ============================================================

@router.post("/forgot-password")
def forgot_password(
    data: ForgotPasswordRequest,
    db: Session = Depends(get_db),
):
    """发送密码重置令牌（生成6位验证码，实际应发邮件）"""
    user = db.query(User).filter(User.email == data.email.strip().lower()).first()
    if not user:
        # 出于安全考虑，即使邮箱不存在也返回成功
        return {"message": "If the email exists, a reset code has been sent"}

    code = str(random.randint(100000, 999999))
    expires = datetime.utcnow() + timedelta(minutes=15)
    _reset_tokens[code] = {"user_id": user.id, "expires_at": expires}

    logger.info(f"Password reset code generated for {user.email}: {code}")
    return {"message": "Reset code generated", "code": code}  # 简化：直接返回 code


# ============================================================
# #13  POST /auth/reset-password  重置密码
# ============================================================

@router.post("/reset-password")
def reset_password(
    data: ResetPasswordRequest,
    db: Session = Depends(get_db),
):
    """验证令牌并重置密码"""
    token_data = _reset_tokens.get(data.token)
    if not token_data:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    if datetime.utcnow() > token_data["expires_at"]:
        del _reset_tokens[data.token]
        raise HTTPException(status_code=400, detail="Token has expired")

    user = db.query(User).filter(User.id == token_data["user_id"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.password_hash = bcrypt_lib.hashpw(data.new_password.encode('utf-8'), bcrypt_lib.gensalt()).decode('utf-8')
    db.commit()
    del _reset_tokens[data.token]
    return {"message": "Password reset successfully"}


# ============================================================
# #14  GET/PUT /auth/privacy  隐私设置
# ============================================================

@router.get("/privacy")
def get_privacy_settings(user: User = Depends(get_current_user)):
    """获取当前用户隐私设置"""
    data = {
        "profile_visibility": user.profile_visibility or "public",
        "post_default_visibility": user.post_default_visibility or "public",
        "show_email": user.show_email if user.show_email is not None else False,
        "allow_search": user.allow_search if user.allow_search is not None else True,
        "allow_friend_requests": user.allow_friend_requests or "everyone",
        "notify_push": user.notify_push if user.notify_push is not None else True,
        "notify_message": user.notify_message if user.notify_message is not None else True,
        "notify_sound": user.notify_sound if user.notify_sound is not None else True,
    }
    return data


@router.put("/privacy")
def update_privacy_settings(
    data: PrivacySettingsUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新当前用户隐私设置"""
    current = db.query(User).filter(User.id == user.id).first()
    updates = {
        "profile_visibility": data.profile_visibility,
        "post_default_visibility": data.post_default_visibility,
        "show_email": data.show_email,
        "allow_search": data.allow_search,
        "allow_friend_requests": data.allow_friend_requests,
    }
    changed = 0
    for field, value in updates.items():
        if value is not None:
            setattr(current, field, value)
            changed += 1
    if changed == 0:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    try:
        db.commit()
        return {"message": "Privacy settings updated"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Token 刷新
# ============================================================

@router.post("/refresh")
def refresh_token(user: User = Depends(get_current_user)):
    """刷新 Token"""
    access_token = _create_token(user)
    return {"access_token": access_token, "token_type": "bearer"}


# ============================================================
# Helpers
# ============================================================

def _create_token(user: User) -> str:
    """创建 JWT access token"""
    expires = timedelta(days=7)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + expires,
    }
    return jwt.encode(payload, Config.JWT_SECRET_KEY, algorithm="HS256")
