"""Administrative deployment control plane. Never executes release code."""
from datetime import datetime
import hashlib
import json
import shutil
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.models.deployment import DeploymentArtifact, DeploymentJob
from app.services.admin_audit_service import record_required_admin_audit
from app.services.deployment_artifacts import MAX_MANIFEST_BYTES, parse_manifest, validate_bundle
from app.services.deployment_service import (
    artifact_directory, get_deployment_config, serialize_artifact, serialize_job, timestamp,
)


def configuration():
    try:
        return get_deployment_config()
    except ValueError:
        raise HTTPException(503, "deployment_configuration_invalid") from None


class BoundedRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request):
            config = configuration()
            maximum = config.max_bytes + MAX_MANIFEST_BYTES + 65536 if request.url.path.endswith("/artifacts") else 16384
            size = request.headers.get("content-length")
            if size is not None and (not size.isdigit() or int(size) > maximum):
                raise HTTPException(413, "request_quota_exceeded")
            receive, count = request._receive, 0
            async def bounded_receive():
                nonlocal count
                message = await receive()
                count += len(message.get("body", b""))
                if count > maximum:
                    raise HTTPException(413, "request_quota_exceeded")
                return message
            request._receive = bounded_receive
            return await original(request)
        return handler


router = APIRouter(prefix="/api/admin/deployments", tags=["Deployments"], route_class=BoundedRoute)


def operator(user=Depends(require_admin)):
    config = configuration()
    if not config.enabled or user.id not in config.operator_ids:
        raise HTTPException(403, "deployment_not_authorized")
    return user


class ReasonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("reason is required")
        return value.strip()


class CreateRequest(ReasonRequest):
    artifact_id: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    operation: Literal["preflight", "deploy", "register", "restore"]
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")


class ApproveRequest(ReasonRequest):
    migration_confirmed: StrictBool = False
    schema_compatible: StrictBool = False


def audit(db, user, request, action, target, reason, **metadata):
    record_required_admin_audit(db, admin_user_id=user.id, action="deployment." + action,
                               target_type="deployment", target_id=target, reason=reason,
                               metadata=metadata, request=request)


def artifact_or_404(db, artifact_id):
    artifact = db.get(DeploymentArtifact, artifact_id)
    if artifact is None:
        raise HTTPException(404, "artifact_not_found")
    return artifact


def job_or_404(db, job_id):
    job = db.get(DeploymentJob, job_id)
    if job is None:
        raise HTTPException(404, "job_not_found")
    return job


def job_dto(db, job):
    return serialize_job(job, artifact_or_404(db, job.artifact_id))


@router.get("/capabilities")
def capabilities(user=Depends(require_admin)):
    config = configuration()
    return {"enabled": config.enabled, "can_deploy": config.enabled and user.id in config.operator_ids}


@router.get("/artifacts")
def artifacts(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
              user=Depends(operator), db: Session = Depends(get_db)):
    query = db.query(DeploymentArtifact)
    return {"items": [serialize_artifact(row) for row in query.order_by(DeploymentArtifact.created_at.desc()).offset((page - 1) * page_size).limit(page_size)],
            "total": query.count(), "page": page, "page_size": page_size}


async def read_limited(upload, maximum, destination=None):
    size, chunks = 0, []
    while chunk := await upload.read(65536):
        size += len(chunk)
        if size > maximum:
            raise HTTPException(413, "upload_quota_exceeded")
        if destination is None:
            chunks.append(chunk)
        else:
            destination.write(chunk)
    return b"".join(chunks) if destination is None else size


@router.post("/artifacts", status_code=201)
async def upload_artifact(request: Request, manifest: UploadFile = File(...), bundle: UploadFile = File(...),
                          reason: str = Form(..., min_length=1, max_length=500),
                          user=Depends(operator), db: Session = Depends(get_db)):
    form = await request.form()
    if sorted(key for key, _ in form.multi_items()) != ["bundle", "manifest", "reason"] or not reason.strip():
        raise HTTPException(422, "invalid_upload_fields")
    config = configuration()
    identifier = str(uuid.uuid4())
    directory = artifact_directory(identifier, config)
    try:
        raw = await read_limited(manifest, MAX_MANIFEST_BYTES)
        parsed = parse_manifest(raw, max_expanded_bytes=config.max_expanded_bytes, max_files=config.max_files)
        directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        with (directory / "bundle.tar.gz").open("xb") as output:
            await read_limited(bundle, config.max_bytes, output)
        validate_bundle(directory / "bundle.tar.gz", parsed, max_bytes=config.max_bytes,
                        max_expanded_bytes=config.max_expanded_bytes, max_files=config.max_files)
        serialized = json.dumps(parsed, separators=(",", ":"), sort_keys=True)
        with (directory / "manifest.json").open("x", encoding="utf-8") as output:
            output.write(serialized)
        artifact = DeploymentArtifact(id=identifier, manifest_json=serialized,
                                      bundle_sha256=parsed["bundle_sha256"].lower(), uploaded_by=user.id)
        db.add(artifact)
        audit(db, user, request, "upload", identifier, reason.strip())
        db.flush()
        response = serialize_artifact(artifact)
        db.commit()
        return response
    except Exception as error:
        db.rollback()
        if directory.exists():
            shutil.rmtree(directory)
        if isinstance(error, ValueError):
            raise HTTPException(422, "artifact_validation_failed") from None
        if isinstance(error, OSError):
            raise HTTPException(503, "artifact_storage_unavailable") from None
        raise
    finally:
        await manifest.close()
        await bundle.close()


@router.post("", status_code=201)
def create_job(payload: CreateRequest, request: Request, user=Depends(operator), db: Session = Depends(get_db)):
    request_hash = hashlib.sha256(json.dumps(payload.model_dump(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    def existing_result():
        existing = db.query(DeploymentJob).filter_by(idempotency_key=payload.idempotency_key).first()
        if existing is None or existing.created_by != user.id or existing.request_hash != request_hash:
            raise HTTPException(409, "idempotency_conflict")
        return job_dto(db, existing)
    if db.query(DeploymentJob.id).filter_by(idempotency_key=payload.idempotency_key).first():
        return existing_result()
    artifact_or_404(db, payload.artifact_id)
    status = "queued" if payload.operation == "preflight" else "awaiting_approval"
    job = DeploymentJob(**payload.model_dump(), created_by=user.id, request_hash=request_hash,
                        status=status, phase=status, events_json=json.dumps([{"status": status, "at": timestamp(datetime.utcnow())}]))
    db.add(job)
    try:
        db.flush()
        audit(db, user, request, "create", job.id, payload.reason, status_after=status)
        db.commit()
    except IntegrityError:
        db.rollback()
        return existing_result()
    db.refresh(job)
    return job_dto(db, job)


@router.get("")
def jobs(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
         user=Depends(operator), db: Session = Depends(get_db)):
    query = db.query(DeploymentJob)
    return {"items": [job_dto(db, row) for row in query.order_by(DeploymentJob.created_at.desc()).offset((page - 1) * page_size).limit(page_size)],
            "total": query.count(), "page": page, "page_size": page_size}


@router.get("/active")
def active(user=Depends(operator), db: Session = Depends(get_db)):
    running = db.query(DeploymentJob).filter(DeploymentJob.status.in_(["running", "verifying", "manual_recovery"])).order_by(DeploymentJob.created_at.desc()).first()
    latest = db.query(DeploymentJob).filter(DeploymentJob.status.in_(["succeeded", "failed", "manual_recovery"]), DeploymentJob.operation.in_(["deploy", "register", "restore"])).order_by(DeploymentJob.finished_at.desc()).first()
    known = latest and latest.status == 'succeeded' and latest.operation != 'restore' and running is None
    release = serialize_artifact(artifact_or_404(db, latest.artifact_id)) if known else None
    if release:
        release["verified_at"] = timestamp(latest.finished_at)
    return {"job": job_dto(db, running) if running else None, "release": release}


@router.get("/{job_id}")
def get_job(job_id: str, user=Depends(operator), db: Session = Depends(get_db)):
    return job_dto(db, job_or_404(db, job_id))


def transition(db, job, expected, status, user, request, reason, **values):
    if job.status not in expected:
        raise HTTPException(409, "job_status_conflict")
    events = json.loads(job.events_json or "[]")
    events.append({"status": status, "at": timestamp(datetime.utcnow())})
    changed = db.query(DeploymentJob).filter(DeploymentJob.id == job.id, DeploymentJob.status == job.status).update(
        dict(status=status, phase=status, events_json=json.dumps(events), **values), synchronize_session=False)
    if changed != 1:
        db.rollback()
        raise HTTPException(409, "job_status_conflict")
    audit(db, user, request, "approve" if status == "queued" else "cancel", job.id, reason,
          status_before=job.status, status_after=status)
    db.commit()
    db.refresh(job)
    return job_dto(db, job)


@router.post("/{job_id}/approve")
def approve(job_id: str, payload: ApproveRequest, request: Request, user=Depends(operator), db: Session = Depends(get_db)):
    job = job_or_404(db, job_id)
    if job.status != "awaiting_approval":
        raise HTTPException(409, "job_status_conflict")
    manifest = json.loads(artifact_or_404(db, job.artifact_id).manifest_json)
    if job.operation == "deploy" and "backend" in manifest["components"] and not payload.migration_confirmed:
        raise HTTPException(422, "migration_confirmation_required")
    if job.operation == "restore" and not payload.schema_compatible:
        raise HTTPException(422, "schema_confirmation_required")
    return transition(db, job, ["awaiting_approval"], "queued", user, request, payload.reason,
                      approved_by=user.id, migration_confirmed=payload.migration_confirmed,
                      schema_compatible=payload.schema_compatible)


@router.post("/{job_id}/cancel")
def cancel(job_id: str, payload: ReasonRequest, request: Request, user=Depends(operator), db: Session = Depends(get_db)):
    return transition(db, job_or_404(db, job_id), ["awaiting_approval", "queued"], "cancelled", user, request,
                      payload.reason, finished_at=datetime.utcnow())
