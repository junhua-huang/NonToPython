"""Durable deployment queue; execution belongs to the separate worker."""
from datetime import datetime
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.mysql import MEDIUMTEXT

from app.database import Base


class DeploymentArtifact(Base):
    __tablename__ = "deployment_artifacts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    manifest_json = Column(Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False)
    bundle_sha256 = Column(String(64), nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class DeploymentJob(Base):
    __tablename__ = "deployment_jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    artifact_id = Column(String(36), ForeignKey("deployment_artifacts.id"), nullable=False)
    operation = Column(String(16), nullable=False)
    reason = Column(String(500), nullable=False)
    status = Column(String(32), nullable=False, index=True)
    phase = Column(String(64), nullable=False, default="pending")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    request_hash = Column(String(64), nullable=False)
    error_code = Column(String(64), nullable=True)
    events_json = Column(Text, nullable=False, default="[]")
    result_json = Column(Text, nullable=True)
    migration_confirmed = Column(Boolean, nullable=False, default=False)
    schema_compatible = Column(Boolean, nullable=False, default=False)


class DeploymentSettings(Base):
    __tablename__ = "deployment_settings"

    id = Column(Integer, primary_key=True, default=1)
    enabled = Column(Boolean, nullable=False, default=False)
    operator_ids_json = Column(Text, nullable=False, default="[]")
    max_bytes = Column(Integer, nullable=False, default=524288000)
    max_expanded_bytes = Column(Integer, nullable=False, default=1073741824)
    max_files = Column(Integer, nullable=False, default=10000)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    worker_heartbeat_at = Column(DateTime, nullable=True)
    worker_status = Column(String(32), nullable=False, default="offline")
    last_error_code = Column(String(64), nullable=True)
