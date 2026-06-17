"""
管理员路由 - FastAPI 重构版
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_admin
from app.models.models import User, SensitiveWord, UserRole, Role, RoleApplication

router = APIRouter()


@router.get("/sensitive-words")
def get_sensitive_words(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """获取敏感词列表"""
    words_query = db.query(SensitiveWord).order_by(SensitiveWord.created_at.desc())
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


@router.post("/sensitive-words")
def add_sensitive_word(
    payload: dict = Body(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """添加敏感词"""
    word = payload.get("word", "").strip()
    if not word:
        raise HTTPException(status_code=400, detail="Word is required")

    existing = db.query(SensitiveWord).filter(SensitiveWord.word == word).first()
    if existing:
        return {"message": "Word already exists", "word": existing.to_dict()}

    sensitive = SensitiveWord(word=word)
    try:
        db.add(sensitive)
        db.commit()
        return {"message": "Sensitive word added", "word": sensitive.to_dict()}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sensitive-words/{word_id}")
def delete_sensitive_word(
    word_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """删除敏感词"""
    word = db.query(SensitiveWord).filter(SensitiveWord.id == word_id).first()
    if not word:
        raise HTTPException(status_code=404, detail="Sensitive word not found")

    try:
        db.delete(word)
        db.commit()
        return {"message": "Sensitive word deleted"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


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
        "applications": [a.to_dict() for a in applications],
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

    app.status = "approved"
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
    return {"message": "Application approved", "application": app.to_dict()}


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
    return {"message": "Application rejected", "application": app.to_dict()}