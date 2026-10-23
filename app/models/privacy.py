"""Minimal deletion ledger and durable media cleanup queue; no email or content."""
from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, Integer, String, Text
from app.database import Base


class AccountErasure(Base):
    __tablename__ = "account_erasures"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Deliberately no FK: survives hard deletion and is needed for restore checks.
    user_id = Column(Integer, nullable=False, unique=True)
    media_status = Column(String(32), nullable=False, default="pending", index=True)
    legacy_keys_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    # Previously issued presigned PUT URLs expire after one hour. Sweep again after it.
    final_sweep_after = Column(DateTime, nullable=False)
    next_attempt_at = Column(DateTime, nullable=False, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error_code = Column(String(64), nullable=True)
    media_completed_at = Column(DateTime, nullable=True)
    backup_status = Column(String(32), nullable=False, default="unverified")
