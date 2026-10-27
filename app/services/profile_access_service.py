"""Enforce profile audiences at every profile-detail entry point."""
from fastapi import HTTPException
from sqlalchemy import and_, or_

from app.models.models import Friendship, User
from app.services.block_service import has_block_between


def require_profile_access(db, target_id: int, viewer_id: int) -> User:
    target = db.query(User).filter(User.id == target_id).first()
    if target is None or target.is_active is not True or has_block_between(db, viewer_id, target_id):
        raise HTTPException(status_code=404, detail="User not found")
    if target_id == viewer_id:
        return target
    audience = target.profile_visibility or "public"
    if audience == "public":
        return target
    if audience == "friends_only":
        friend = db.query(Friendship.id).filter(
            Friendship.status == "accepted",
            or_(and_(Friendship.sender_id == viewer_id, Friendship.receiver_id == target_id),
                and_(Friendship.sender_id == target_id, Friendship.receiver_id == viewer_id)),
        ).first()
        if friend is not None:
            return target
    # Unknown legacy audiences are restricted, never silently made public.
    raise HTTPException(status_code=403, detail={
        "code": "PROFILE_FRIENDS_ONLY" if audience == "friends_only" else "PROFILE_PRIVATE",
        "message": "该用户仅向好友开放主页" if audience == "friends_only" else "该用户暂未向你开放主页",
    })
