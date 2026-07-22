"""
管理员路由 - FastAPI 重构版
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.models.models import (
    BUSINESS_IDENTITY_ROLES,
    Role,
    RoleApplication,
    SensitiveWord,
    SensitiveWordVersion,
    User,
    UserRole,
)
from app.services.local_text_moderator import normalize_text
from app.services.moderation_snapshot import RegexValidationError, validate_safe_regex
from app.services.moderation_types import RiskCategory

router = APIRouter()
moderation_router = APIRouter(prefix="/api/admin/moderation", tags=["Admin Moderation"])

_RULE_CONFLICT_DETAIL = {
    "code": "MODERATION_RULE_CONFLICT",
    "message": "规则已存在或发生冲突",
}
_INVALID_RULE = "INVALID_MODERATION_RULE"
_ALLOWED_MATCH_TYPES = {"literal", "regex"}
_ALLOWED_SEVERITIES = {"low", "medium", "high"}


class SensitiveWordCreate(BaseModel):
    word: str = Field(min_length=1, max_length=500)
    match_type: str = "literal"
    category: str = "other"
    severity: str = "medium"
    is_active: bool = True


class SensitiveWordPatch(BaseModel):
    word: str | None = Field(default=None, min_length=1, max_length=500)
    match_type: str | None = None
    category: str | None = None
    severity: str | None = None
    is_active: bool | None = None


def _invalid_rule(message: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": _INVALID_RULE, "message": message},
    )


def _validate_metadata(category: str, severity: str) -> None:
    if category not in {item.value for item in RiskCategory}:
        raise _invalid_rule("风险分类无效")
    if severity not in _ALLOWED_SEVERITIES:
        raise _invalid_rule("风险等级无效")


def _validated_expression(word: str, match_type: str) -> str:
    if match_type not in _ALLOWED_MATCH_TYPES:
        raise _invalid_rule("匹配类型无效")
    value = word.strip()
    if not value or not normalize_text(value):
        raise _invalid_rule("规则内容不能为空")
    if match_type == "regex":
        try:
            return validate_safe_regex(value)
        except RegexValidationError:
            raise _invalid_rule("正则规则不安全或无效") from None
    return value


def _advance_rule_version(db: Session) -> int:
    version_row = db.execute(
        select(SensitiveWordVersion)
        .where(SensitiveWordVersion.id == 1)
        .with_for_update()
    ).scalar_one()
    version_row.version += 1
    version_row.updated_at = datetime.utcnow()
    db.flush()
    return version_row.version


def _commit_or_conflict(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=_RULE_CONFLICT_DETAIL) from None


@moderation_router.get("/sensitive-words")
@router.get("/sensitive-words")
def get_sensitive_words(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """获取敏感词列表"""
    del user
    words_query = db.query(SensitiveWord).order_by(SensitiveWord.id.asc())
    total = words_query.count()
    words = words_query.offset((page - 1) * per_page).limit(per_page).all()
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    return {
        "words": [w.to_dict() for w in words],
        "total": total,
        "pages": pages,
        "current_page": page,
        "per_page": per_page,
    }


@moderation_router.get("/sensitive-words/{word_id}")
def get_sensitive_word(
    word_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    del user
    row = db.get(SensitiveWord, word_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Sensitive word not found")
    return {"word": row.to_dict()}


@moderation_router.post("/sensitive-words", status_code=201)
@router.post("/sensitive-words", status_code=201)
def add_sensitive_word(
    payload: SensitiveWordCreate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """添加敏感词"""
    expression_value = _validated_expression(payload.word, payload.match_type)
    _validate_metadata(payload.category, payload.severity)
    version = _advance_rule_version(db)
    row = SensitiveWord(
        word=expression_value,
        match_type=payload.match_type,
        category=payload.category,
        severity=payload.severity,
        is_active=payload.is_active,
        row_version=version,
        created_by=user.id,
    )
    db.add(row)
    _commit_or_conflict(db)
    db.refresh(row)
    return {"message": "Sensitive word added", "word": row.to_dict()}


@moderation_router.patch("/sensitive-words/{word_id}")
def patch_sensitive_word(
    word_id: int,
    payload: SensitiveWordPatch,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    del user
    row = db.get(SensitiveWord, word_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Sensitive word not found")
    values = payload.model_dump(exclude_unset=True)
    next_type = values.get("match_type", row.match_type)
    next_word = values.get("word", row.word)
    next_category = values.get("category", row.category)
    next_severity = values.get("severity", row.severity)
    expression_value = _validated_expression(next_word, next_type)
    _validate_metadata(next_category, next_severity)
    version = _advance_rule_version(db)
    row.word = expression_value
    row.match_type = next_type
    row.category = next_category
    row.severity = next_severity
    if "is_active" in values:
        row.is_active = values["is_active"]
    row.row_version = version
    row.updated_at = datetime.utcnow()
    _commit_or_conflict(db)
    db.refresh(row)
    return {"message": "Sensitive word updated", "word": row.to_dict()}


@moderation_router.delete("/sensitive-words/{word_id}")
@router.delete("/sensitive-words/{word_id}")
def delete_sensitive_word(
    word_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """删除敏感词"""
    del user
    row = db.get(SensitiveWord, word_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Sensitive word not found")
    _advance_rule_version(db)
    db.delete(row)
    _commit_or_conflict(db)
    return {"message": "Sensitive word deleted"}


# ============================================================
# 角色管理（管理员直接授予 / 撤销角色）
# ============================================================

@router.get("/users/{user_id}/roles")
def get_user_roles(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """管理员查看某个用户的角色"""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    user_roles = db.query(UserRole).filter(UserRole.user_id == user_id).all()
    role_ids = [ur.role_id for ur in user_roles]
    roles = db.query(Role).filter(Role.id.in_(role_ids)).all() if role_ids else []

    return {
        "user_id": user_id,
        "username": target.username,
        "roles": [r.to_dict() for r in roles],
    }


@router.post("/users/{user_id}/roles")
def assign_role_to_user(
    user_id: int,
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """管理员直接给用户分配角色"""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    role_name = payload.get("role_name", "").strip().lower()
    if not role_name:
        raise HTTPException(status_code=400, detail="role_name is required")

    if role_name in BUSINESS_IDENTITY_ROLES:
        raise HTTPException(status_code=403, detail="Only system roles can be assigned directly; business identities must use certification review")

    role = db.query(Role).filter(Role.name == role_name).first()
    if not role:
        raise HTTPException(status_code=404, detail=f"Role '{role_name}' not found")

    existing = db.query(UserRole).filter(
        UserRole.user_id == user_id,
        UserRole.role_id == role.id,
    ).first()
    if existing:
        return {"message": f"User already has '{role_name}' role"}

    db.add(UserRole(user_id=user_id, role_id=role.id))
    db.commit()

    logger = __import__('logging').getLogger(__name__)
    logger.info(f"Admin {admin.username} assigned role '{role_name}' to user {target.username}")
    return {"message": f"Role '{role_name}' assigned to user {target.username}"}


@router.delete("/users/{user_id}/roles/{role_name}")
def revoke_role_from_user(
    user_id: int,
    role_name: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """管理员撤销用户的某个角色"""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # 不允许撤销自己的最后一个角色
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot revoke your own roles")

    role = db.query(Role).filter(Role.name == role_name.strip().lower()).first()
    if not role:
        raise HTTPException(status_code=404, detail=f"Role '{role_name}' not found")

    user_role = db.query(UserRole).filter(
        UserRole.user_id == user_id,
        UserRole.role_id == role.id,
    ).first()
    if not user_role:
        raise HTTPException(status_code=404, detail=f"User does not have '{role_name}' role")

    db.delete(user_role)
    db.commit()

    logger = __import__('logging').getLogger(__name__)
    logger.info(f"Admin {admin.username} revoked role '{role_name}' from user {target.username}")
    return {"message": f"Role '{role_name}' revoked from user {target.username}"}


@router.get("/role-applications")
def list_all_applications(
    status: str = Query("pending"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """管理员查看角色申请列表（可按状态筛选）"""
    query = db.query(RoleApplication)
    if status != "all":
        query = query.filter(RoleApplication.status == status)
    query = query.order_by(RoleApplication.created_at.desc())

    total = query.count()
    applications = query.offset((page - 1) * per_page).limit(per_page).all()
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    return {
        "applications": [a.to_dict(include_private_user=True) for a in applications],
        "total": total,
        "pages": pages,
        "current_page": page,
    }


@router.post("/role-applications/{application_id}/approve")
def admin_approve_application(
    application_id: int,
    payload: dict = Body(default_factory=dict),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """管理员通过角色申请"""
    app = db.query(RoleApplication).filter(RoleApplication.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if app.status != "pending":
        raise HTTPException(status_code=409, detail="Application is not pending")

    app.status = "verified"
    app.review_comment = payload.get("review_comment", "")
    app.reviewer_id = admin.id
    app.reviewed_at = datetime.utcnow()

    existing = db.query(UserRole).filter(
        UserRole.user_id == app.user_id,
        UserRole.role_id == app.role_id,
    ).first()
    if not existing:
        db.add(UserRole(user_id=app.user_id, role_id=app.role_id))

    db.commit()

    logger = __import__('logging').getLogger(__name__)
    logger.info(f"Admin {admin.username} approved role application #{application_id}")
    return {"message": "Application approved", "application": app.to_dict(include_private_user=True)}


@router.post("/role-applications/{application_id}/reject")
def admin_reject_application(
    application_id: int,
    payload: dict = Body(default_factory=dict),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """管理员拒绝角色申请"""
    app = db.query(RoleApplication).filter(RoleApplication.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if app.status != "pending":
        raise HTTPException(status_code=409, detail="Application is not pending")

    app.status = "rejected"
    app.review_comment = payload.get("review_comment", "")
    app.reviewer_id = admin.id
    app.reviewed_at = datetime.utcnow()

    db.commit()

    logger = __import__('logging').getLogger(__name__)
    logger.info(f"Admin {admin.username} rejected role application #{application_id}")
    return {"message": "Application rejected", "application": app.to_dict(include_private_user=True)}


@router.post("/role-applications/{application_id}/suspend")
def admin_suspend_application(
    application_id: int,
    payload: dict = Body(default_factory=dict),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """管理员暂停已认证身份展示"""
    app = db.query(RoleApplication).filter(RoleApplication.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    app.status = "suspended"
    app.review_comment = payload.get("review_comment", "")
    app.reviewer_id = admin.id
    app.reviewed_at = datetime.utcnow()

    existing = db.query(UserRole).filter(
        UserRole.user_id == app.user_id,
        UserRole.role_id == app.role_id,
    ).first()
    if existing:
        db.delete(existing)

    db.commit()

    logger = __import__('logging').getLogger(__name__)
    logger.info(f"Admin {admin.username} suspended role application #{application_id}")
    return {"message": "Application suspended", "application": app.to_dict(include_private_user=True)}
