"""Retryable COS erasure and bounded short-lived security-data cleanup."""
import asyncio
from datetime import datetime, timedelta
import json
import logging
import re
import time

from sqlalchemy import delete, select

from app.database import SessionLocal
from app.models.models import EmailOtp, LoginAttempt, PushLog, WSMessageLog, WSAckDedup
from app.models.privacy import AccountErasure
from app.services.account_erasure_service import UPLOAD_FOLDERS

logger = logging.getLogger(__name__)


class MediaCleanupError(Exception):
    pass


class CleanupBudget:
    """Stop large sweeps between requests; the durable next pass resumes safely."""
    def __init__(self, client):
        self.client = client
        self.deadline = time.monotonic() + 20
        self.remaining = 200

    def __getattr__(self, name):
        method = getattr(self.client, name)

        def call(**kwargs):
            if self.remaining <= 0 or time.monotonic() > self.deadline:
                raise MediaCleanupError("cleanup_budget_exhausted")
            self.remaining -= 1
            return method(**kwargs)

        return call


def _items(response, name):
    rows = response.get(name, [])
    return [rows] if isinstance(rows, dict) else rows


def _truncated(response):
    value = response.get("IsTruncated")
    if value not in ("true", "false", True, False):
        raise MediaCleanupError("invalid_provider_response")
    return value in ("true", True)


def purge_account_prefix(client, bucket, prefix, *, max_pages=1000):
    """Explicit versions/markers; deleting a key alone only creates a delete marker."""
    if not bucket or not re.fullmatch(r"(?:" + "|".join(UPLOAD_FOLDERS) + r")/[1-9][0-9]*/", prefix or ""):
        raise MediaCleanupError("invalid_cleanup_scope")
    marker, version_marker = "", ""
    for _ in range(max_pages):
        response = client.list_objects_versions(Bucket=bucket, Prefix=prefix, KeyMarker=marker,
                                               VersionIdMarker=version_marker, MaxKeys=1000)
        for item in _items(response, "Version") + _items(response, "DeleteMarker"):
            key, version = item.get("Key"), item.get("VersionId")
            if not isinstance(key, str) or not key.startswith(prefix) or version is None:
                raise MediaCleanupError("invalid_provider_scope")
            client.delete_object(Bucket=bucket, Key=key, VersionId=str(version))
        if not _truncated(response):
            break
        next_marker = response.get("NextKeyMarker", "")
        next_version = response.get("NextVersionIdMarker", "")
        if not next_marker.startswith(prefix) or (next_marker, next_version) == (marker, version_marker):
            raise MediaCleanupError("invalid_provider_pagination")
        marker, version_marker = next_marker, next_version
    else:
        raise MediaCleanupError("cleanup_page_limit")

    marker, upload_marker = "", ""
    for _ in range(max_pages):
        response = client.list_multipart_uploads(Bucket=bucket, Prefix=prefix, KeyMarker=marker,
                                                 UploadIdMarker=upload_marker, MaxUploads=1000)
        for item in _items(response, "Upload"):
            key, upload_id = item.get("Key"), item.get("UploadId")
            if not isinstance(key, str) or not key.startswith(prefix) or not upload_id:
                raise MediaCleanupError("invalid_provider_scope")
            client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        if not _truncated(response):
            break
        next_marker = response.get("NextKeyMarker", "")
        next_upload = response.get("NextUploadIdMarker", "")
        if not next_marker.startswith(prefix) or (next_marker, next_upload) == (marker, upload_marker):
            raise MediaCleanupError("invalid_provider_pagination")
        marker, upload_marker = next_marker, next_upload
    else:
        raise MediaCleanupError("cleanup_page_limit")

    # Verify the namespace is empty. A failed verification must keep the durable retry.
    versions = client.list_objects_versions(Bucket=bucket, Prefix=prefix, MaxKeys=1)
    uploads = client.list_multipart_uploads(Bucket=bucket, Prefix=prefix, MaxUploads=1)
    if any(_items(versions, key) for key in ("Version", "DeleteMarker")) or _items(uploads, "Upload"):
        raise MediaCleanupError("objects_remain")
    if _truncated(versions) or _truncated(uploads):
        raise MediaCleanupError("objects_remain")


def process_erasure(db, erasure, *, client=None, bucket=None, now=None):
    """Caller locks the queue row and commits; provider errors never escape as secrets."""
    from app.core.config import Config
    now = now or datetime.utcnow()
    if erasure.media_status == "complete" or erasure.next_attempt_at > now:
        return
    erasure.attempts += 1
    try:
        if client is None:
            from qcloud_cos import CosConfig, CosS3Client
            if Config.COS_ID and Config.COS_KEY and Config.COS_BUCKET_NAME:
                client = CosS3Client(CosConfig(Region=Config.COS_REGION, SecretId=Config.COS_ID,
                                             SecretKey=Config.COS_KEY, Timeout=10), retry=0)
        bucket = bucket or Config.COS_BUCKET_NAME
        if client is None or not bucket:
            raise MediaCleanupError("storage_not_configured")
        client = CleanupBudget(client)
        for folder in UPLOAD_FOLDERS:
            purge_account_prefix(client, bucket, f"{folder}/{erasure.user_id}/")
        erasure.last_error_code = None
        if now < erasure.final_sweep_after:
            erasure.media_status = "waiting_for_upload_expiry"
            erasure.next_attempt_at = erasure.final_sweep_after
        elif json.loads(erasure.legacy_keys_json):
            erasure.media_status = "legacy_review_required"
            erasure.last_error_code = "legacy_ownership_unverified"
            erasure.next_attempt_at = now + timedelta(days=1)
        else:
            erasure.media_status = "complete"
            erasure.media_completed_at = now
        # backup_status deliberately remains unverified. COS completion is not backup deletion.
    except Exception as error:
        erasure.media_status = "retry"
        erasure.last_error_code = str(error) if isinstance(error, MediaCleanupError) else "storage_cleanup_failed"
        erasure.next_attempt_at = now + timedelta(minutes=min(60, 5 * erasure.attempts))
        logger.warning("account_media_cleanup_retry erasure_id=%s code=%s", erasure.id, erasure.last_error_code)
    db.flush()


def prune_transient_records(db, *, now=None, batch_size=500):
    """Fixed implemented limits: OTP +1 day, logins/push 30d, WS replay 7d, ACK 1d.

    OTP records are retained for 24h after expiry to cover the existing hourly rate
    limits; clearing expired codes immediately would bypass those protections.
    """
    now = now or datetime.utcnow()
    policies = (
        (EmailOtp, EmailOtp.expires_at < now - timedelta(days=1)),
        (LoginAttempt, LoginAttempt.created_at < now - timedelta(days=30)),
        (PushLog, (PushLog.created_at < now - timedelta(days=30)) |
            select(AccountErasure.id).where(AccountErasure.user_id == PushLog.user_id).exists()),
        (WSMessageLog, WSMessageLog.created_at < now - timedelta(days=7)),
    )
    counts = {}
    for model, predicate in policies:
        ids = list(db.execute(select(model.id).where(predicate).order_by(model.id).limit(batch_size)).scalars())
        if ids:
            db.execute(delete(model.__table__).where(model.id.in_(ids)))
        counts[model.__tablename__] = len(ids)
    rows = db.execute(select(WSAckDedup.user_id, WSAckDedup.client_msg_id).where(
        WSAckDedup.processed_at < now - timedelta(days=1)).limit(batch_size)).all()
    for user_id, client_msg_id in rows:
        db.execute(delete(WSAckDedup.__table__).where(WSAckDedup.user_id == user_id,
                   WSAckDedup.client_msg_id == client_msg_id))
    counts[WSAckDedup.__tablename__] = len(rows)
    return counts


def run_cleanup_cycle(*, session_factory=SessionLocal, now=None):
    now = now or datetime.utcnow()
    with session_factory() as db:
        prune_transient_records(db, now=now)
        db.commit()
    # Small bounded cycle; SKIP LOCKED lets multiple app workers share the queue.
    for _ in range(5):
        with session_factory() as db:
            erasure = db.execute(select(AccountErasure).where(
                AccountErasure.media_status != "complete", AccountErasure.next_attempt_at <= now)
                .order_by(AccountErasure.next_attempt_at).limit(1).with_for_update(skip_locked=True)).scalar_one_or_none()
            if erasure is None:
                break
            process_erasure(db, erasure, now=now)
            db.commit()


async def poll_privacy_cleanup(stop):
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_cleanup_cycle)
        except Exception:
            # Missing migration/provider/DB failure stays observable and retries.
            logger.error("privacy_cleanup_cycle_failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=300)
        except asyncio.TimeoutError:
            pass
