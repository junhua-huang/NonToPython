"""
角色管理路由
- 角色列表查询
- 角色申请（用户自主申请 → 管理员审核）
- 角色资料管理（Coser / 摄影师 / 服务商）
"""
import html
import json
import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Body, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.services.email_service import EmailService

from app.database import get_db
from app.dependencies import get_current_user, require_admin, require_role
from app.models.models import (
    BUSINESS_IDENTITY_ROLES,
    User, Role, UserRole, RoleApplication,
    CoserProfile, PhotographerProfile, ServiceProfile,
)
from app.services.moderation_route_helpers import moderate_route_fields
from app.services.moderation_service import moderation_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/roles", tags=["Roles"])

IDENTITY_APPLICATION_NOTIFY_EMAIL = "2531830689@qq.com"


# ============================================================
# Pydantic Schemas
# ============================================================

class RoleApplyRequest(BaseModel):
    role_name: str
    reason: str = ""
    application_text: str = ""
    proof_images: list[str] = Field(default_factory=list, max_length=9)
    portfolio_links: list[str] = Field(default_factory=list)
    contact_info: str = ""
    extra_note: str = ""


class RoleReviewRequest(BaseModel):
    review_comment: str = ""


class CoserProfileUpdate(BaseModel):
    cosname: str | None = None
    bio: str | None = None
    styles: str | None = None          # 逗号分隔
    city: str | None = None
    is_available: bool | None = None
    price_range_min: int | None = None
    price_range_max: int | None = None
    portfolio_images: str | None = None
    social_links: str | None = None


class PhotographerProfileUpdate(BaseModel):
    equipment: str | None = None
    styles: str | None = None
    city: str | None = None
    is_available: bool | None = None
    price_range_min: int | None = None
    price_range_max: int | None = None
    portfolio_images: str | None = None
    social_links: str | None = None


class ServiceProfileUpdate(BaseModel):
    service_type: str | None = None
    description: str | None = None
    city: str | None = None
    is_available: bool | None = None
    price_info: str | None = None
    portfolio_images: str | None = None


async def _send_role_application_email(
    *,
    application_id: int,
    username: str,
    user_id: int,
    role_label: str,
    role_name: str,
    application_text: str,
    proof_image_count: int,
    contact_info: str,
):
    subject = f"【南图】新的身份认证申请 #{application_id}"
    body = f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:640px;margin:0 auto;padding:24px;">
  <h2 style="color:#1DA1F2;margin:0 0 16px;">新的身份认证申请</h2>
  <p><strong>申请编号：</strong>{application_id}</p>
  <p><strong>用户：</strong>{html.escape(username)} (ID: {user_id})</p>
  <p><strong>申请身份：</strong>{html.escape(role_label)} / {html.escape(role_name)}</p>
  <p><strong>证明图片：</strong>{proof_image_count} 张</p>
  <p><strong>联系方式：</strong>{html.escape(contact_info or '未填写')}</p>
  <p><strong>认证说明：</strong></p>
  <blockquote style="margin:12px 0;padding:12px;background:#f7f9fa;border-left:4px solid #1DA1F2;white-space:pre-wrap;">{html.escape(application_text or '未填写')}</blockquote>
  <p style="color:#8899a6;font-size:12px;margin-top:24px;">请登录后台审核该身份认证申请。</p>
</div>"""
    await EmailService.send_email(IDENTITY_APPLICATION_NOTIFY_EMAIL, subject, body)


# ============================================================
# 角色查询
# ============================================================

@router.get("")
def list_roles(db: Session = Depends(get_db)):
    """获取所有可用角色列表（公开接口）"""
    roles = db.query(Role).filter(Role.name.in_(BUSINESS_IDENTITY_ROLES.keys())).order_by(Role.sort_order).all()
    return {"roles": [r.to_dict() for r in roles]}


@router.get("/my")
def get_my_roles(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取当前用户已拥有的角色"""
    user_roles = db.query(UserRole).filter(UserRole.user_id == user.id).all()
    role_ids = [ur.role_id for ur in user_roles]
    roles = db.query(Role).filter(Role.id.in_(role_ids)).all() if role_ids else []
    return {
        "roles": [r.to_dict() for r in roles],
        "user_id": user.id,
    }


# ============================================================
# 角色申请
# ============================================================

@router.post("/apply")
def apply_role(
    data: RoleApplyRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """用户自主申请角色（管理员角色不可申请）"""
    role_name = data.role_name.strip().lower()
    moderate_route_fields(
        moderation_service,
        "POST /api/roles/apply",
        data.model_dump(),
        actor_user_id=user.id,
        is_public=False,
    )

    if role_name not in BUSINESS_IDENTITY_ROLES:
        raise HTTPException(status_code=403, detail="Only business identities can be applied for")

    role = db.query(Role).filter(Role.name == role_name).first()
    if not role:
        raise HTTPException(status_code=404, detail=f"Role '{role_name}' not found")

    # 检查是否已拥有该角色
    existing = db.query(UserRole).filter(
        UserRole.user_id == user.id,
        UserRole.role_id == role.id,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"You already have the '{role_name}' role")

    # 检查是否有未审核的申请
    pending = db.query(RoleApplication).filter(
        RoleApplication.user_id == user.id,
        RoleApplication.role_id == role.id,
        RoleApplication.status == "pending",
    ).first()
    if pending:
        raise HTTPException(status_code=409, detail="You already have a pending application for this role")

    application_text = (data.application_text or data.reason).strip()
    application = RoleApplication(
        user_id=user.id,
        role_id=role.id,
        status="pending",
        reason=(data.reason or application_text).strip(),
        application_text=application_text,
        proof_images=json.dumps(data.proof_images, ensure_ascii=False),
        portfolio_links=json.dumps(data.portfolio_links, ensure_ascii=False),
        contact_info=data.contact_info.strip(),
        extra_note=data.extra_note.strip(),
    )
    db.add(application)
    db.commit()
    db.refresh(application)

    background_tasks.add_task(
        _send_role_application_email,
        application_id=application.id,
        username=user.username,
        user_id=user.id,
        role_label=role.label,
        role_name=role.name,
        application_text=application_text,
        proof_image_count=len(data.proof_images),
        contact_info=data.contact_info.strip(),
    )

    logger.info(f"User {user.username} applied for role: {role_name}")
    return {
        "message": f"Application for '{role.label}' submitted successfully",
        "application": application.to_dict(include_private_user=True),
    }


@router.get("/applications")
def get_my_applications(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """查看当前用户的角色申请记录"""
    query = db.query(RoleApplication).filter(
        RoleApplication.user_id == user.id
    ).order_by(RoleApplication.created_at.desc())

    total = query.count()
    applications = query.offset((page - 1) * per_page).limit(per_page).all()
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    return {
        "applications": [a.to_dict(include_private_user=True) for a in applications],
        "total": total,
        "pages": pages,
        "current_page": page,
    }


# ============================================================
# 管理员审核
# ============================================================

@router.get("/applications/pending")
def list_pending_applications(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """管理员查看所有待审核的角色申请"""
    query = db.query(RoleApplication).filter(
        RoleApplication.status == "pending"
    ).order_by(RoleApplication.created_at.asc())

    total = query.count()
    applications = query.offset((page - 1) * per_page).limit(per_page).all()
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    return {
        "applications": [a.to_dict(include_private_user=True) for a in applications],
        "total": total,
        "pages": pages,
        "current_page": page,
    }


@router.post("/applications/{application_id}/approve")
def approve_application(
    application_id: int,
    data: RoleReviewRequest = Body(default_factory=RoleReviewRequest),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """管理员通过角色申请"""
    app = db.query(RoleApplication).filter(RoleApplication.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if app.status != "pending":
        raise HTTPException(status_code=409, detail="Application is not pending")
    moderate_route_fields(
        moderation_service,
        "POST /api/roles/applications/{application_id}/approve",
        data.model_dump(),
        actor_user_id=user.id,
        is_public=False,
    )

    app.status = "verified"
    app.review_comment = data.review_comment.strip()
    app.reviewer_id = user.id
    app.reviewed_at = __import__('datetime').datetime.utcnow()

    # 授予角色
    existing = db.query(UserRole).filter(
        UserRole.user_id == app.user_id,
        UserRole.role_id == app.role_id,
    ).first()
    if not existing:
        db.add(UserRole(user_id=app.user_id, role_id=app.role_id))

    db.commit()

    logger.info(f"Admin {user.username} approved role application #{application_id}")
    return {"message": "Application approved", "application": app.to_dict(include_private_user=True)}


@router.post("/applications/{application_id}/reject")
def reject_application(
    application_id: int,
    data: RoleReviewRequest = Body(default_factory=RoleReviewRequest),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """管理员拒绝角色申请"""
    app = db.query(RoleApplication).filter(RoleApplication.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if app.status != "pending":
        raise HTTPException(status_code=409, detail="Application is not pending")
    moderate_route_fields(
        moderation_service,
        "POST /api/roles/applications/{application_id}/reject",
        data.model_dump(),
        actor_user_id=user.id,
        is_public=False,
    )

    app.status = "rejected"
    app.review_comment = data.review_comment.strip()
    app.reviewer_id = user.id
    app.reviewed_at = __import__('datetime').datetime.utcnow()

    db.commit()

    logger.info(f"Admin {user.username} rejected role application #{application_id}")
    return {"message": "Application rejected", "application": app.to_dict(include_private_user=True)}


@router.post("/applications/{application_id}/suspend")
def suspend_application(
    application_id: int,
    data: RoleReviewRequest = Body(default_factory=RoleReviewRequest),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """管理员暂停已认证身份展示"""
    app = db.query(RoleApplication).filter(RoleApplication.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    moderate_route_fields(
        moderation_service,
        "POST /api/roles/applications/{application_id}/suspend",
        data.model_dump(),
        actor_user_id=user.id,
        is_public=False,
    )

    app.status = "suspended"
    app.review_comment = data.review_comment.strip()
    app.reviewer_id = user.id
    app.reviewed_at = __import__('datetime').datetime.utcnow()

    existing = db.query(UserRole).filter(
        UserRole.user_id == app.user_id,
        UserRole.role_id == app.role_id,
    ).first()
    if existing:
        db.delete(existing)

    db.commit()

    logger.info(f"Admin {user.username} suspended role application #{application_id}")
    return {"message": "Application suspended", "application": app.to_dict(include_private_user=True)}


# ============================================================
# 角色资料管理
# ============================================================

@router.get("/profiles/{user_id}")
def get_user_role_profiles(
    user_id: int,
    db: Session = Depends(get_db),
):
    """获取用户的角色扩展资料（Coser / 摄影师 / 服务商）"""
    coser = db.query(CoserProfile).filter(CoserProfile.user_id == user_id).first()
    photographer = db.query(PhotographerProfile).filter(PhotographerProfile.user_id == user_id).first()
    services = db.query(ServiceProfile).filter(ServiceProfile.user_id == user_id).all()

    return {
        "user_id": user_id,
        "coser": coser.to_dict() if coser else None,
        "photographer": photographer.to_dict() if photographer else None,
        "services": [s.to_dict() for s in services],
    }


# === Coser 资料 ===

@router.put("/profiles/coser")
def update_coser_profile(
    data: CoserProfileUpdate,
    user: User = Depends(require_role("coser")),
    db: Session = Depends(get_db),
):
    """更新 Coser 专属资料"""
    moderate_route_fields(
        moderation_service,
        "PUT /api/roles/profiles/coser",
        data.model_dump(exclude_none=True),
        actor_user_id=user.id,
        is_public=True,
    )
    profile = db.query(CoserProfile).filter(CoserProfile.user_id == user.id).first()
    if not profile:
        profile = CoserProfile(user_id=user.id)
        db.add(profile)

    _apply_updates(profile, data.model_dump(exclude_none=True))
    db.commit()
    db.refresh(profile)
    return {"message": "Coser profile updated", "profile": profile.to_dict()}


# === 摄影师资料 ===

@router.put("/profiles/photographer")
def update_photographer_profile(
    data: PhotographerProfileUpdate,
    user: User = Depends(require_role("photographer")),
    db: Session = Depends(get_db),
):
    """更新摄影师专属资料"""
    moderate_route_fields(
        moderation_service,
        "PUT /api/roles/profiles/photographer",
        data.model_dump(exclude_none=True),
        actor_user_id=user.id,
        is_public=True,
    )
    profile = db.query(PhotographerProfile).filter(PhotographerProfile.user_id == user.id).first()
    if not profile:
        profile = PhotographerProfile(user_id=user.id)
        db.add(profile)

    _apply_updates(profile, data.model_dump(exclude_none=True))
    db.commit()
    db.refresh(profile)
    return {"message": "Photographer profile updated", "profile": profile.to_dict()}


# === 通用服务商资料 ===

SERVICE_ROLE_NAMES = {"wig_stylist", "makeup_artist", "retoucher", "ticket_agent", "prop_maker", "costume_maker"}

@router.put("/profiles/service")
def update_service_profile(
    data: ServiceProfileUpdate,
    user: User = Depends(require_role(*SERVICE_ROLE_NAMES)),
    db: Session = Depends(get_db),
):
    """更新服务商资料（毛娘 / 妆娘 / 后期师 / 票务代理）"""
    service_type = data.service_type
    if not service_type:
        raise HTTPException(status_code=400, detail="service_type is required")
    if service_type not in SERVICE_ROLE_NAMES:
        raise HTTPException(status_code=400, detail=f"Invalid service_type: {service_type}")
    verified_service_role = (
        db.query(UserRole)
        .join(Role, UserRole.role_id == Role.id)
        .filter(UserRole.user_id == user.id, Role.name == service_type)
        .first()
    )
    if not verified_service_role:
        raise HTTPException(status_code=403, detail="Required verified service identity")
    moderate_route_fields(
        moderation_service,
        "PUT /api/roles/profiles/service",
        data.model_dump(exclude_none=True),
        actor_user_id=user.id,
        is_public=True,
    )

    profile = db.query(ServiceProfile).filter(
        ServiceProfile.user_id == user.id,
        ServiceProfile.service_type == service_type,
    ).first()
    if not profile:
        profile = ServiceProfile(user_id=user.id, service_type=service_type)
        db.add(profile)

    _apply_updates(profile, data.model_dump(exclude_none=True))
    db.commit()
    db.refresh(profile)
    return {"message": f"{service_type} profile updated", "profile": profile.to_dict()}


# ============================================================
# 内部工具
# ============================================================

def _apply_updates(obj, updates: dict):
    """将字典中的字段值写入 SQLAlchemy 对象"""
    for key, value in updates.items():
        if hasattr(obj, key):
            setattr(obj, key, value)
