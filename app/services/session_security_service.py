"""Password changes revoke HTTP/WS sessions and their push bindings together."""
import logging

from app.models.models import PushDevice

logger = logging.getLogger(__name__)


def revoke_account_sessions(db, user):
    # Caller locks the user row and commits this alongside the password hash.
    user.auth_version = (user.auth_version or 0) + 1
    db.query(PushDevice).filter(PushDevice.user_id == user.id).delete(synchronize_session=False)


def disconnect_revoked_sessions(user_id):
    try:
        from app.ws_manager import ws_manager
        ws_manager.schedule_push(ws_manager.disconnect(user_id))
    except Exception:
        # Other workers still reject revoked tokens on each authenticated frame.
        logger.warning("revoked_session_socket_cleanup_failed user_id=%s", user_id)
