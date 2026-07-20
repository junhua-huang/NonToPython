"""
认证路由 - FastAPI 版本
注册 / 登录 / Token 刷新 / 密码重置 / 个人资料 / 隐私设置
"""
import re
import logging
import html
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Body, Query, Request
from fastapi.security import HTTPBearer
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session
import bcrypt as bcrypt_lib
from jose import jwt, JWTError

from app.database import get_db
from app.models.models import User, Role, UserRole
from app.core.config import Config
from werkzeug.security import check_password_hash
from app.dependencies import get_current_user, get_optional_user
from app.services.email_service import EmailService
from app.services.otp_service import OtpService
from app.services.block_service import has_block_between
from app.serializers.user import serialize_user_profile, serialize_user_self

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Auth"])
security = HTTPBearer()


def _validate_avatar_url(url: str) -> str:
    """校验头像/封面 URL：只允许 https:// 且域名在 COS 白名单内。

    防止 file:///、http:// 内网地址（如 169.254.169.254）、javascript: 等
    协议被写入数据库。
    """
    if not url:
        return url
    u = url.strip()
    # 必须是 https:// 开头
    if not u.startswith('https://'):
        raise HTTPException(
            status_code=400,
            detail='头像/封面 URL 必须使用 https:// 协议'
        )
    # 白名单域名：COS 桶域名 + dicebear 默认头像（注册时生成）
    allowed_hosts = []
    if Config.COS_DOMAIN:
        allowed_hosts.append(Config.COS_DOMAIN)
    if Config.COS_BUCKET_NAME:
        allowed_hosts.append(f'{Config.COS_BUCKET_NAME}.cos.{Config.COS_REGION}.myqcloud.com')
    allowed_hosts.append('api.dicebear.com')  # 默认头像
    # 提取 host（去掉 https:// 后到第一个 / 或结尾）
    host_part = u[len('https://'):].split('/', 1)[0].lower()
    if not any(host_part == h.lower() or host_part.endswith('.' + h.lower()) for h in allowed_hosts):
        raise HTTPException(
            status_code=400,
            detail='头像/封面 URL 域名不在允许列表内'
        )
    return u


def _sanitize_bio(bio: str) -> str:
    """对个人简介做 HTML 实体转义，防御存储型 XSS。

    即使 Flutter 端用 Text 组件不会执行脚本，Web 端或未来换渲染组件时
    仍可能中招，服务端统一转义做纵深防御。
    """
    if not bio:
        return bio
    return html.escape(bio, quote=True)


# ============================================================
# Pydantic Schemas
# ============================================================

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    # 邮箱验证码（必填）。前端先调 /auth/send-otp purpose=register 拿到，再调 /auth/register 提交。
    email_code: str

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
    # 邮箱验证码（连续登录失败 N 次后必填）。
    email_code: str = ""

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
    """重置密码：用邮箱 + 验证码（替代旧版基于 token 的方案）"""
    email: str
    code: str
    new_password: str


class SendOtpRequest(BaseModel):
    """发送邮箱验证码"""
    email: str
    purpose: str  # register / reset_password / login

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError("邮箱格式不正确")
        return v

    @field_validator("purpose")
    @classmethod
    def validate_purpose(cls, v: str) -> str:
        if v not in ("register", "reset_password", "login"):
            raise ValueError("purpose 必须是 register / reset_password / login")
        return v


class VerifyOtpRequest(BaseModel):
    """单独校验验证码（仅用于前端预校验，最终消费仍在业务接口里）"""
    email: str
    code: str
    purpose: str


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
    return serialize_user_self(user)


def _build_public_user_response(user: User, viewer_user_id: int | None = None) -> dict:
    return serialize_user_profile(user, viewer_user_id=viewer_user_id)


# ============================================================
# Endpoints — 注册/登录
# ============================================================

@router.post("/send-otp")
async def send_otp(data: SendOtpRequest, request: Request, db: Session = Depends(get_db)):
    """
    发送邮箱验证码。

    purpose:
      - register: 注册验证（要求邮箱未被占用）
      - reset_password: 重置密码（要求邮箱已注册；不存在也返回成功，防枚举）
      - login: 登录验证（连续失败 N 次后用）

    限流：同邮箱 60s/1次、1h/5次；同 IP 1h/10次。
    """
    email = data.email
    purpose = data.purpose
    ip = request.client.host if request.client else None

    # 注册场景：邮箱已被占用 → 直接拒绝（其它 purpose 不暴露邮箱是否存在）
    if purpose == "register":
        if db.query(User).filter(User.email == email).first():
            raise HTTPException(status_code=409, detail="该邮箱已被注册")
    elif purpose == "reset_password":
        # 不存在也返回成功，防枚举
        if not db.query(User).filter(User.email == email).first():
            return {"message": "若该邮箱已注册，验证码已发送"}

    # 限流
    ok, reason = OtpService.check_rate_limit(email, ip, db)
    if not ok:
        raise HTTPException(status_code=429, detail=reason)

    code = OtpService.generate_and_store(email, purpose, ip, db)
    # 异步发邮件，不阻塞响应；失败仅记日志
    sent = await EmailService.send_otp_email(email, code, purpose)
    if not sent and Config.DEBUG:
        # 开发模式：邮件发不出时把验证码打到日志，方便本地测试
        logger.info("[OTP DEV] email=%s purpose=%s code=%s", email, purpose, code)

    return {"message": "验证码已发送，请查收邮箱"}


@router.post("/verify-otp")
def verify_otp(data: VerifyOtpRequest, db: Session = Depends(get_db)):
    """
    单独校验验证码（前端预校验用，不消费）。

    注意：这里不标记 is_used——避免前端预校验后真正业务接口拿不到。
    所以本接口仅查存在性 + 未过期 + 未使用，不修改状态。
    业务接口（register/reset_password/login）真正消费 OTP。
    """
    now = datetime.utcnow()
    from app.models.models import EmailOtp
    otp = (
        db.query(EmailOtp)
        .filter(
            EmailOtp.email == data.email.strip().lower(),
            EmailOtp.code == data.code,
            EmailOtp.purpose == data.purpose,
            EmailOtp.is_used == False,
            EmailOtp.expires_at > now,
        )
        .order_by(EmailOtp.created_at.desc())
        .first()
    )
    if not otp:
        raise HTTPException(status_code=400, detail="验证码无效或已过期")
    return {"valid": True}


@router.post("/register", response_model=AuthResponse)
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    """用户注册（必须先通过 /auth/send-otp purpose=register 拿到邮箱验证码）"""
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(status_code=409, detail="Email already exists")

    # 校验邮箱验证码（消费 OTP，标记 is_used）
    valid, reason = OtpService.verify(data.email, data.email_code, "register", db)
    if not valid:
        raise HTTPException(status_code=400, detail=reason)

    user = User(
        username=data.username,
        email=data.email,
        password_hash=bcrypt_lib.hashpw(data.password.encode('utf-8'), bcrypt_lib.gensalt()).decode('utf-8'),
        bio="",
        avatar_url=f"https://api.dicebear.com/7.x/initials/svg?seed={data.username}",
        cover_photo_url="",
        is_email_verified=True,  # 通过 OTP 验证 → 标记邮箱已验证
    )
    db.add(user)
    db.flush()  # 获取 user.id

    # 自动分配 "user" 角色
    default_role = db.query(Role).filter(Role.name == "user").first()
    if default_role:
        db.add(UserRole(user_id=user.id, role_id=default_role.id))

    db.commit()
    db.refresh(user)

    access_token = _create_token(user, db)
    logger.info(f"New user registered: {user.username} (ID: {user.id})")
    return AuthResponse(
        message="Registration successful",
        access_token=access_token,
        user_id=user.id,
        username=user.username,
        user=_build_user_response(user),
    )


@router.post("/login", response_model=AuthResponse)
def login(data: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """
    用户登录（支持用户名或邮箱）。

    连续失败 ≥ Config.LOGIN_FAIL_THRESHOLD 次（15 分钟窗口）后，
    要求带邮箱验证码（email_code）才能继续尝试。
    """
    login_value = data.get_login()
    if not login_value:
        raise HTTPException(status_code=400, detail="email or login is required")
    ip = request.client.host if request.client else "unknown"

    user = (
        db.query(User)
        .filter((User.username == login_value) | (User.email == login_value))
        .first()
    )

    # 限流：连续失败超阈值则强制要求 OTP
    if OtpService.requires_login_otp(login_value, db):
        if not data.email_code:
            raise HTTPException(
                status_code=429,
                detail="登录失败次数过多，请通过邮箱验证码继续登录",
            )
        # 该账号必须有用户且邮箱可校验
        if user is None:
            OtpService.record_login_attempt(login_value, ip, success=False, db=db)
            raise HTTPException(status_code=401, detail="Invalid username/email or password")
        valid, reason = OtpService.verify(user.email, data.email_code, "login", db)
        if not valid:
            OtpService.record_login_attempt(login_value, ip, success=False, db=db)
            raise HTTPException(status_code=400, detail=reason)
        # OTP 校验通过后继续走密码校验

    if not user:
        OtpService.record_login_attempt(login_value, ip, success=False, db=db)
        raise HTTPException(status_code=401, detail="Invalid username/email or password")

    # 兼容 scrypt (Flask 旧版) 和 bcrypt (FastAPI 新版) 两种哈希格式
    pwd_ok = False
    if user.password_hash.startswith("scrypt:"):
        pwd_ok = check_password_hash(user.password_hash, data.password)
    else:
        pwd_ok = bcrypt_lib.checkpw(data.password.encode('utf-8'), user.password_hash.encode('utf-8'))

    if not pwd_ok:
        OtpService.record_login_attempt(login_value, ip, success=False, db=db)
        raise HTTPException(status_code=401, detail="Invalid username/email or password")

    # 登录成功 → 记录成功（清空"连续失败"计数）
    OtpService.record_login_attempt(login_value, ip, success=True, db=db)

    access_token = _create_token(user, db)
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
        current.bio = _sanitize_bio(data.bio)
    if data.avatar_url is not None:
        current.avatar_url = _validate_avatar_url(data.avatar_url)
    if data.cover_photo_url is not None:
        current.cover_photo_url = _validate_avatar_url(data.cover_photo_url)
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
    current_user: User = Depends(get_current_user),
):
    """获取用户公开信息（需登录，防止未授权枚举用户）"""
    target = db.query(User).filter(User.id == user_id).first()
    if not target or has_block_between(db, current_user.id, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    return _build_public_user_response(target, viewer_user_id=current_user.id)


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
async def forgot_password(
    data: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    发送密码重置验证码邮件。

    无论邮箱是否存在都返回相同提示，避免账号枚举。
    实际只在邮箱已注册时发送验证码（purpose=reset_password）。
    """
    email = data.email.strip().lower()
    ip = request.client.host if request.client else None
    user = db.query(User).filter(User.email == email).first()
    if user:
        ok, reason = OtpService.check_rate_limit(email, ip, db)
        if not ok:
            raise HTTPException(status_code=429, detail=reason)
        code = OtpService.generate_and_store(email, "reset_password", ip, db)
        sent = await EmailService.send_otp_email(email, code, "reset_password")
        if not sent and Config.DEBUG:
            logger.info("[OTP DEV] email=%s purpose=reset_password code=%s", email, code)
    return {"message": "若该邮箱已注册，验证码已发送"}


# ============================================================
# #13  POST /auth/reset-password  重置密码
# ============================================================

@router.post("/reset-password")
def reset_password(
    data: ResetPasswordRequest,
    db: Session = Depends(get_db),
):
    """
    通过邮箱验证码重置密码。

    body: { email, code, new_password }
    """
    if len(data.new_password) < 8:
        raise HTTPException(status_code=400, detail="新密码至少 8 位")

    email = data.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if not user:
        # 不暴露邮箱是否存在
        raise HTTPException(status_code=400, detail="验证码无效或已过期")

    valid, reason = OtpService.verify(email, data.code, "reset_password", db)
    if not valid:
        raise HTTPException(status_code=400, detail=reason)

    user.password_hash = bcrypt_lib.hashpw(data.new_password.encode('utf-8'), bcrypt_lib.gensalt()).decode('utf-8')
    db.commit()
    logger.info(f"Password reset by OTP: user_id={user.id} email={user.email}")
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
def refresh_token(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """刷新 Token"""
    access_token = _create_token(user, db)
    return {"access_token": access_token, "token_type": "bearer"}


# ============================================================
# Helpers
# ============================================================

def _create_token(user: User, db: Session = None) -> str:
    """创建 JWT access token（含角色列表）"""
    expires = timedelta(days=7)
    if db:
        user_roles = db.query(UserRole).filter(UserRole.user_id == user.id).all()
        role_names = []
        for ur in user_roles:
            role = db.query(Role).filter(Role.id == ur.role_id).first()
            if role:
                role_names.append(role.name)
    else:
        role_names = user.get_role_names() if hasattr(user, 'get_role_names') else []
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "roles": role_names,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + expires,
    }
    return jwt.encode(payload, Config.JWT_SECRET_KEY, algorithm="HS256")
