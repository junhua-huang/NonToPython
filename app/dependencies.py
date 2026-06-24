"""
FastAPI 依赖注入 — 统一 Query 参数鉴权

Token 传参方式: ?access_token=<jwt>

核心校验逻辑统一走 app.core.auth_core.verify_token()，
HTTP / WebSocket 完全复用同一套规则。
"""
from fastapi import Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.core.auth_core import verify_token, AuthError
from app.models.models import User


def get_current_user(
    request: Request,
    access_token: str = Query("", alias="access_token"),
    db: Session = Depends(get_db),
) -> User:
    """从 Query 参数 access_token 获取当前登录用户"""
    try:
        return verify_token(access_token)
    except AuthError as e:
        raise HTTPException(status_code=e.code, detail=e.message)


def get_optional_user(
    request: Request,
    access_token: str = Query("", alias="access_token"),
    db: Session = Depends(get_db),
):
    """可选认证 — 未传 access_token 返回 None"""
    if not access_token:
        return None
    try:
        return verify_token(access_token)
    except AuthError:
        return None


# ============================================================
# 角色权限依赖（核心）
# ============================================================

def require_role(*role_names: str):
    """
    工厂函数：生成要求用户拥有指定角色（至少一个）的依赖

    用法:
        @router.get("/admin-only")
        def admin_endpoint(user = Depends(require_role("admin"))):
            ...
    """

    def dependency(
        request: Request,
        access_token: str = Query("", alias="access_token"),
        db: Session = Depends(get_db),
    ) -> User:
        try:
            user = verify_token(access_token)
        except AuthError as e:
            raise HTTPException(status_code=e.code, detail=e.message)

        # 权限以数据库为准，避免 JWT 内旧 roles 在撤销/暂停后继续生效。
        from app.models.models import UserRole, Role
        ur_rows = db.query(UserRole).filter(UserRole.user_id == user.id).all()
        role_ids = [ur.role_id for ur in ur_rows]
        roles = db.query(Role).filter(Role.id.in_(role_ids)).all() if role_ids else []
        user_roles = {r.name for r in roles}

        if not any(r in user_roles for r in role_names):
            raise HTTPException(status_code=403, detail=f"Required role(s): {', '.join(role_names)}")

        return user

    return dependency


# ============================================================
# 快捷别名
# ============================================================

require_admin     = require_role("admin")
require_organizer = require_role("event_organizer")
require_coser     = require_role("coser")

require_service_provider = require_role(
    "coser", "wig_stylist", "makeup_artist",
    "photographer", "retoucher", "ticket_agent", "prop_maker", "costume_maker"
)

require_content_manager = require_role("admin", "event_organizer")


def admin_required(user: User = Depends(require_admin)):
    """[兼容] 管理员权限校验，等效于 require_role("admin")"""
    return user
