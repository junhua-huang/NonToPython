"""Scope-specific user serializers with a fail-closed card default."""


def _bool_show_email(user) -> bool:
    return user.show_email is True


def _created_at(user):
    return user.created_at.isoformat() if user.created_at else None


def serialize_user_card(user) -> dict:
    """Serialize a user for lists and nested payloads without private fields."""
    from app.ws_manager import ws_manager

    is_online = ws_manager.is_connected(user.id) if user.id else False
    verified_roles = user.get_verified_identity_roles()
    verified_role_labels = user.get_verified_identity_labels()
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.username,
        "bio": user.bio,
        "avatar": user.avatar_url,
        "avatar_url": user.avatar_url,
        "cover_photo_url": user.cover_photo_url,
        "is_online": is_online,
        "created_at": _created_at(user),
        "roles": verified_roles,
        "role_labels": verified_role_labels,
        "verified_roles": verified_roles,
        "verified_role_labels": verified_role_labels,
    }


def serialize_user_self(user) -> dict:
    """Serialize the authenticated user's own profile and privacy state."""
    roles = user.get_role_names() if hasattr(user, "get_role_names") else []
    role_labels = user.get_role_labels() if hasattr(user, "get_role_labels") else []
    verified_roles = user.get_verified_identity_roles() if hasattr(user, "get_verified_identity_roles") else []
    verified_role_labels = user.get_verified_identity_labels() if hasattr(user, "get_verified_identity_labels") else []
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "show_email": _bool_show_email(user),
        "display_name": user.username,
        "bio": user.bio,
        "avatar_url": user.avatar_url,
        "cover_photo_url": user.cover_photo_url,
        "created_at": _created_at(user),
        "roles": roles,
        "role_labels": role_labels,
        "verified_roles": verified_roles,
        "verified_role_labels": verified_role_labels,
    }


def serialize_user_admin(user) -> dict:
    """Serialize a user for administrator-only governance views."""
    roles = user.get_role_names() if hasattr(user, "get_role_names") else []
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "nickname": user.username,
        "is_active": bool(user.is_active),
        "created_at": _created_at(user),
        "roles": roles,
    }



def serialize_user_profile(user, viewer_user_id: int | None) -> dict:
    """Serialize profile detail, revealing email only to self or by opt-in."""
    verified_roles = user.get_verified_identity_roles() if hasattr(user, "get_verified_identity_roles") else []
    verified_role_labels = user.get_verified_identity_labels() if hasattr(user, "get_verified_identity_labels") else []
    payload = {
        "id": user.id,
        "username": user.username,
        "display_name": user.username,
        "bio": user.bio,
        "avatar_url": user.avatar_url,
        "cover_photo_url": user.cover_photo_url,
        "created_at": _created_at(user),
        "show_email": _bool_show_email(user),
        "verified_roles": verified_roles,
        "verified_role_labels": verified_role_labels,
    }
    if viewer_user_id == user.id or user.show_email is True:
        payload["email"] = user.email
    return payload
