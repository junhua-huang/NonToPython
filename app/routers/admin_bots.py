"""
管理员批量供应机器人/测试账号。

用途：课程作业 / 自运营测试社区中，需要大量平台内部的受控机器人身份。
身份由平台生成（唯一用户名 + 合成内部邮箱 + 随机唯一密码），并标记 is_bot=True、
分配 bot 角色，可正常登录参与社区。

本模块不使用任何第三方账号凭据，也不绕过公开注册的邮箱验证；它是管理员专属、
有配额上限和审计的受控旁路。明文密码仅在创建响应中一次性返回，绝不写入审计或日志。
"""
import random
import secrets
import string

import bcrypt as bcrypt_lib
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Config
from app.database import get_db
from app.dependencies import require_admin
from app.models.models import Role, User, UserRole
from app.routers.auth import normalize_username
from app.serializers.user import serialize_user_admin
from app.services.admin_audit_service import record_required_admin_audit

router = APIRouter(prefix="/api/admin/bots", tags=["Admin Bots"])

# 机器人默认头像（与公开注册一致）。
_DEFAULT_AVATAR = "https://api.dicebear.com/7.x/initials/svg?seed={seed}"

# 密码：保证同时满足长度 >=8 且含字母数字。自运营测试身份，非真实安全凭据。
def _generate_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.islower() for c in candidate) and any(c.isdigit() for c in candidate):
            return candidate


def _random_avatar(seed: str) -> str:
    return _DEFAULT_AVATAR.format(seed=seed or random.randint(10_000, 99_999))


class ProvisionBotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    count: int = Field(default=1, ge=1, le=5000)
    prefix: str = Field(default="bot", max_length=40)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("prefix")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("prefix is required")
        if any(ch not in string.ascii_lowercase + string.digits + "_" for ch in v):
            raise ValueError("prefix may only contain lowercase letters, digits, underscore")
        if v[0].isdigit():
            raise ValueError("prefix must start with a letter or underscore")
        return v


def _work_prefix(prefix: str) -> str:
    """把前缀转成平台内部可以用作 username/email 的形式。"""
    p = prefix.strip().lower()
    # 中文前缀无法放进 email/username，做安全回退
    if not p or not all(ch in string.ascii_lowercase + "_" for ch in p):
        return "bot"
    return p


@router.post("/provision-batch", status_code=201)
def provision_bots(
    payload: ProvisionBotRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """批量生成 N 个可登录的机器人账号。返回一次性凭据清单。"""
    prefix = _work_prefix(payload.prefix)
    count = payload.count

    # 配额：已存在的 bot 账号 + 本批次不得超过上限
    lower = Config.get_max_bot_accounts()
    existing = db.execute(select(func.count()).select_from(User).where(User.is_bot == True)).scalar_one()
    if existing + count > lower:
        raise HTTPException(status_code=409, detail="bot_account_limit_reached")

    # 找到起始序号，避免与已存在的同名 bot 冲突
    base = "user"
    role = db.execute(select(Role).where(Role.name == "bot")).scalar_one_or_none()

    emails_seen = set()
    created = []
    seq_start = existing + 1
    seq = seq_start

    while len(created) < count:
        candidate_username = f"{prefix}_{seq}"
        candidate_email = f"{prefix}_{seq}@bot.internal".lower()
        seq += 1

        # 避免单批次内重复（seq 递增本来唯一，但防御性检查也无妨）
        if candidate_email in emails_seen:
            continue
        emails_seen.add(candidate_email)

        username_taken = db.execute(select(User.id).where(User.username == candidate_username)).scalars().first()
        email_taken = db.execute(select(User.id).where(User.email == candidate_email)).scalars().first()
        if username_taken or email_taken:
            continue

        password = _generate_password()
        new_user = User(
            username=candidate_username,
            email=candidate_email,
            password_hash=bcrypt_lib.hashpw(
                password.encode("utf-8"),
                bcrypt_lib.gensalt(),
            ).decode("utf-8"),
            bio="",
            avatar_url=_random_avatar(candidate_username),
            cover_photo_url="",
            is_email_verified=True,   # 管理员审核替代 OTP
            is_bot=True,
            is_active=True,
        )
        db.add(new_user)
        db.flush()
        if role is not None:
            db.add(UserRole(user_id=new_user.id, role_id=role.id))
            db.flush()

        # 邮箱统一经管理员序列化器投影，满足项目邮箱隐私契约；
        # 密码仅在本响应一次性返回，不写入审计/日志/存储。
        cred = serialize_user_admin(new_user)
        cred.update({"password": password, "is_bot": True})
        created.append(cred)

    # 审计：只记录数量，不记录明文密码
    record_required_admin_audit(
        db,
        admin_user_id=admin.id,
        action="provision_bots",
        target_type="bot_batch",
        reason=payload.reason,
        metadata={"counts": len(created), "target_label": prefix},
        request=request,
    )
    db.commit()

    return {
        "created": len(created),
        "total_bots": existing + len(created),
        "note": "Passwords are returned once. Treat them as test credentials.",
        "credentials": created,
    }


@router.get("", response_model=None)
def list_bots(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """列出机器人账号（不含密码）。"""
    from app.serializers.user import serialize_user_admin

    rows = db.execute(
        select(User).where(User.is_bot == True).order_by(User.id.asc()).limit(limit).offset(offset)  # noqa: E712
    ).scalars().unique().all()
    return [
        {**serialize_user_admin(u), "is_bot": u.is_bot}
        for u in rows
    ]