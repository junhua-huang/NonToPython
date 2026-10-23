"""Read-only operational status. Never expose deletion details through public routes."""
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.models.privacy import AccountErasure

router = APIRouter(prefix="/api/admin/privacy", tags=["Privacy operations"], dependencies=[Depends(require_admin)])


@router.get("/status")
def privacy_status(db: Session = Depends(get_db)):
    counts = dict(db.execute(select(AccountErasure.media_status, func.count())
                            .group_by(AccountErasure.media_status)).all())
    backups = db.execute(select(func.count()).select_from(AccountErasure)
                         .where(AccountErasure.backup_status == "unverified")).scalar_one()
    return {"media_jobs":counts, "backup_unverified":backups,
            "cleanup_interval_seconds":300, "limits":{"otp_hours_after_expiry":24,
            "login_attempt_days":30, "push_log_days":30, "ws_replay_days":7, "ws_ack_days":1},
            "note":"Queue status does not verify cloud snapshots, server logs or backup retention."}


@router.get("/erasures/{erasure_id}")
def erasure_status(erasure_id: str, include_legacy_keys: bool = Query(False), db: Session = Depends(get_db)):
    row = db.get(AccountErasure, erasure_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Erasure request not found")
    keys = json.loads(row.legacy_keys_json)
    result = {"id":row.id, "user_id":row.user_id, "media_status":row.media_status,
              "attempts":row.attempts, "last_error_code":row.last_error_code,
              "legacy_review_count":len(keys), "backup_status":row.backup_status,
              "created_at":row.created_at, "next_attempt_at":row.next_attempt_at,
              "media_completed_at":row.media_completed_at}
    if include_legacy_keys:
        result["legacy_keys_for_ownership_review"] = keys
    return result
