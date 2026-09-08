"""Configuration, permissions and public DTOs shared with the deployment worker."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from uuid import UUID

from app.models.models import Role, User, UserRole
from app.models.deployment import DeploymentSettings
from app.services.deployment_artifacts import DEFAULT_MAX_BYTES, DEFAULT_MAX_EXPANDED_BYTES, DEFAULT_MAX_FILES


@dataclass(frozen=True)
class DeploymentConfig:
    enabled: bool
    operator_ids: frozenset[int]
    artifact_dir: Path
    max_bytes: int = DEFAULT_MAX_BYTES
    max_expanded_bytes: int = DEFAULT_MAX_EXPANDED_BYTES
    max_files: int = DEFAULT_MAX_FILES


def get_deployment_config(db=None):
    backend = Path(__file__).resolve().parents[2]
    configured_root = Path(os.environ.get("DEPLOYMENT_ARTIFACT_DIR", str(backend.parent / "deployment-artifacts"))).expanduser().absolute()
    if any(part.is_symlink() for part in (configured_root, *configured_root.parents)):
        raise ValueError("unsafe_artifact_directory")
    root = configured_root.resolve()
    protected = [backend, Path(os.environ.get("DEPLOYMENT_SITE_DIR", "/www/wwwroot/nonto.online")).resolve()]
    if any(root == p or root.is_relative_to(p) or p.is_relative_to(root) for p in protected):
        raise ValueError("invalid_artifact_directory")
    ids = os.environ.get("DEPLOYMENT_OPERATOR_IDS", "").strip()
    if ids and any(not re.fullmatch(r"[1-9][0-9]*", value.strip()) for value in ids.split(",")):
        raise ValueError("invalid_operator_ids")
    def limit(name, default):
        raw = os.environ.get(name, str(default))
        if not raw.isdigit() or not 1 <= int(raw) <= default:
            raise ValueError("invalid_deployment_limit")
        return int(raw)
    config = DeploymentConfig(
        enabled=os.environ.get("DEPLOYMENT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        operator_ids=frozenset(int(value.strip()) for value in ids.split(",") if value.strip()),
        artifact_dir=root,
        max_bytes=limit("DEPLOYMENT_MAX_BYTES", DEFAULT_MAX_BYTES),
        max_expanded_bytes=limit("DEPLOYMENT_MAX_EXPANDED_BYTES", DEFAULT_MAX_EXPANDED_BYTES),
        max_files=limit("DEPLOYMENT_MAX_FILES", DEFAULT_MAX_FILES),
    )
    stored = _stored_settings(db) if db is not None else None
    if stored is None:
        return config
    ids = _json(stored.operator_ids_json, [])
    if not isinstance(ids, list) or any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in ids):
        raise ValueError("invalid_stored_operator_ids")
    values = (stored.max_bytes, stored.max_expanded_bytes, stored.max_files)
    if any(not isinstance(value, int) or value < 1 for value in values):
        raise ValueError("invalid_stored_deployment_limits")
    return DeploymentConfig(bool(stored.enabled), frozenset(ids), config.artifact_dir, *values)


def _stored_settings(db):
    return db.get(DeploymentSettings, 1)


def worker_heartbeat(db, status='online', error_code=None):
    row = _stored_settings(db)
    if row is None:
        row = DeploymentSettings(id=1)
        db.add(row)
    row.worker_status = status if status in {'idle', 'disabled', 'running', 'error', 'online', 'offline'} else 'error'
    row.worker_heartbeat_at = datetime.utcnow()
    row.last_error_code = error_code if isinstance(error_code, str) and re.fullmatch(r'[A-Z0-9_:-]{1,64}', error_code) else None


def worker_authorized(db, user_id):
    try:
        config = get_deployment_config(db)
    except ValueError:
        return False
    if not config.enabled or user_id not in config.operator_ids:
        return False
    return db.query(User.id).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id).filter(
        User.id == user_id, User.is_active.is_(True), Role.name == "admin"
    ).first() is not None


def artifact_directory(artifact_id, config=None):
    identifier = str(UUID(str(artifact_id)))
    root = (config or get_deployment_config()).artifact_dir
    directory = root / identifier
    if root.is_symlink() or directory.is_symlink() or directory.resolve().parent != root.resolve():
        raise ValueError("unsafe_artifact_directory")
    return directory


def artifact_bundle_path(artifact_id, config=None):
    return artifact_directory(artifact_id, config) / "bundle.tar.gz"


def artifact_manifest_path(artifact_id, config=None):
    return artifact_directory(artifact_id, config) / "manifest.json"


def timestamp(value):
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _json(value, default):
    try:
        result = json.loads(value or "null")
        return result if isinstance(result, type(default)) else default
    except (ValueError, TypeError):
        return default


def serialize_settings(db):
    config = get_deployment_config(db)
    stored = _stored_settings(db)
    return {
        "enabled": config.enabled,
        "operator_ids": sorted(config.operator_ids),
        "max_bytes": config.max_bytes,
        "max_expanded_bytes": config.max_expanded_bytes,
        "max_files": config.max_files,
        "worker_status": stored.worker_status if stored else "offline",
        "worker_heartbeat_at": timestamp(stored.worker_heartbeat_at) if stored else None,
        "last_error_code": _code(stored.last_error_code) if stored else None,
        "readiness": {
            "artifact_storage": True,
            "server_configuration": True,
            "database": True,
            "worker_configuration": stored is not None,
        },
    }


def serialize_settings_health(db):
    settings = serialize_settings(db)
    stored = _stored_settings(db)
    age = None if not stored or not stored.worker_heartbeat_at else (datetime.utcnow() - stored.worker_heartbeat_at).total_seconds()
    from app.models.deployment import DeploymentJob
    settings.update({
        "worker_online": bool(age is not None and age <= 30 and stored.worker_status not in {"offline", "error"}),
        "heartbeat_age_seconds": int(age) if age is not None and age >= 0 else None,
        "queue": {
            "queued": db.query(DeploymentJob).filter_by(status="queued").count(),
            "running": db.query(DeploymentJob).filter(DeploymentJob.status.in_(["running", "verifying"])).count(),
        },
    })
    return settings


def serialize_artifact(artifact):
    manifest = _json(artifact.manifest_json, {})
    return dict(id=artifact.id, release_id=manifest.get("release_id"), version=manifest.get("version"),
                build_number=manifest.get("build_number"), components=manifest.get("components", []),
                bundle_sha256=artifact.bundle_sha256, uploaded_by=artifact.uploaded_by,
                created_at=timestamp(artifact.created_at), file_count=len(manifest.get("files", [])))


def _code(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_:-]{1,64}", value) else None


def serialize_job(job, artifact=None):
    result = {key: getattr(job, key) for key in (
        "id", "artifact_id", "operation", "reason", "status", "created_by", "approved_by",
        "migration_confirmed", "schema_compatible",
    )}
    result.update(phase=_code(job.phase), error_code=_code(job.error_code))
    result.update({key: timestamp(getattr(job, key)) for key in ("created_at", "started_at", "finished_at")})
    # Events deliberately exclude worker stdout, exception strings, paths and credentials.
    result["events"] = [
        {key: value for key, value in event.items() if
         (key in {"phase", "status", "error_code", "code"} and _code(value)) or
         (key in {"at", "timestamp"} and isinstance(value, str) and re.fullmatch(r"[0-9T:.+Z-]{10,40}", value))}
        for event in _json(job.events_json, [])[-200:] if isinstance(event, dict)
    ]
    result["artifact"] = serialize_artifact(artifact) if artifact else None
    if artifact:
        result.update({key: result["artifact"][key] for key in ("release_id", "version", "build_number", "components")})
    stored = _json(job.result_json, {})
    result["result"] = {key: value for key, value in stored.items() if key in {"verified", "registered", "restored"} and type(value) is bool}
    return result
