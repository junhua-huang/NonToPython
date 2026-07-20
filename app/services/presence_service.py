"""Product-level presence helpers based on WebSocket and app lifecycle state."""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.models import PushDevice


BACKGROUND_ACTIVE_SECONDS = 300
ACTIVE_APP_STATES = {"foreground", "background"}


def has_recent_active_device(db: Session, user_id: int) -> bool:
    """Return True when the user has a recently seen foreground/background device."""
    cutoff = datetime.utcnow() - timedelta(seconds=BACKGROUND_ACTIVE_SECONDS)
    return db.query(PushDevice.id).filter(
        PushDevice.user_id == user_id,
        PushDevice.enabled == True,
        PushDevice.app_state.in_(ACTIVE_APP_STATES),
        PushDevice.last_seen_at.isnot(None),
        PushDevice.last_seen_at >= cutoff,
    ).first() is not None


def is_user_product_online(db: Session, user_id: int, raw_ws_online: bool = False) -> bool:
    """Return product-level online status: raw WS online or recent active app device."""
    if raw_ws_online:
        return True
    return has_recent_active_device(db, user_id)
