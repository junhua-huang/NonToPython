"""Configuration, permissions and public DTOs shared with the deployment worker."""
from dataclasses import dataclass
from datetime import timezone
import json
import os
from pathlib import Path
import re
from uuid import UUID

from app.models.models import Role, User, UserRole
from app.services.deployment_artifacts import DEFAULT_MAX_BYTES, DEFAULT_MAX_EXPANDED_BYTES, DEFAULT_MAX_FILES


@dataclass(frozen=True)
class DeploymentConfig:
    enabled: bool
    operator_ids: frozenset[int]
    artifact_dir: Path
    max_bytes: int = DEFAULT_MAX_BYTES
    max_expanded_bytes: int = DEFAULT_MAX_EXPANDED_BYTES
    max_files: int = DEFAULT_MAX_FILES


def get_deployment_config():
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
    return DeploymentConfig(
        enabled=os.environ.get("DEPLOYMENT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        operator_ids=frozenset(int(value.strip()) for value in ids.split(",") if value.strip()),
        artifact_dir=root,
        max_bytes=limit("DEPLOYMENT_MAX_BYTES", DEFAULT_MAX_BYTES),
        max_expanded_bytes=limit("DEPLOYMENT_MAX_EXPANDED_BYTES", DEFAULT_MAX_EXPANDED_BYTES),
        max_files=limit("DEPLOYMENT_MAX_FILES", DEFAULT_MAX_FILES),
    )


def worker_authorized(db, user_id):
    try:
        config = get_deployment_config()
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
