import hashlib
import io
import json
import tarfile
from datetime import datetime

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import auth_core
from app.core.config import Config
from app.database import Base, get_db
from app.dependencies import get_current_user, get_optional_user
from app.models.deployment import DeploymentArtifact, DeploymentJob
from app.models.models import AdminAuditLog, Role, User, UserRole
from app.routers import deployments
from app.services import deployment_service
from app.services.deployment_artifacts import validate_bundle, validate_manifest

PREFIX = "/api/admin/deployments"


def headers(user=1):
    token = jwt.encode({"sub": str(user)}, Config.JWT_SECRET_KEY, algorithm="HS256")
    return {"Authorization": "Bearer " + token}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(Role(id=1, name="admin", label="Admin"))
        for identifier in range(1, 5):
            db.add(User(id=identifier, username=f"user{identifier}", email=f"user{identifier}@example.com",
                        password_hash="x", is_active=identifier != 4))
        db.flush()
        db.add_all([UserRole(user_id=identifier, role_id=1) for identifier in (1, 2, 4)])
        db.commit()
    monkeypatch.setattr(auth_core, "SessionLocal", factory)
    monkeypatch.setenv("DEPLOYMENT_ENABLED", "true")
    monkeypatch.setenv("DEPLOYMENT_OPERATOR_IDS", "1")
    monkeypatch.setenv("DEPLOYMENT_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    application = FastAPI()
    application.include_router(deployments.router)
    def session():
        with factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
    application.dependency_overrides[get_db] = session
    @application.get("/current")
    def current(user=Depends(get_current_user)):
        return user.id
    @application.get("/optional")
    def optional(user=Depends(get_optional_user)):
        return user.id if user else None
    with TestClient(application, raise_server_exceptions=False) as client:
        yield client, factory, tmp_path
    engine.dispose()


def bundle_data(path="backend/app/main.py", *, kind=None, duplicate=False):
    data = b"print('release')\n"
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        item = tarfile.TarInfo(path)
        item.size = len(data)
        if kind:
            item.type = kind
            item.linkname = "../../outside"
            item.size = 0
        archive.addfile(item, io.BytesIO(data) if not kind else None)
        if duplicate:
            archive.addfile(item, io.BytesIO(data))
    bundle = stream.getvalue()
    manifest = {"release_id": "release-1", "version": "1.0.0", "build_number": 1,
                "components": ["backend"], "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
                "files": [{"path": path, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}]}
    return manifest, bundle


def upload(client, manifest=None, bundle=None, **kwargs):
    if manifest is None:
        manifest, bundle = bundle_data()
    return client.post(PREFIX + "/artifacts", headers=headers(), data={"reason": "Release test"},
                       files={"manifest": ("manifest.json", json.dumps(manifest)), "bundle": ("bundle.tar.gz", bundle)}, **kwargs)


def create(client, artifact, operation="deploy", key="request-1", **extra):
    return client.post(PREFIX, headers=headers(), json=dict(artifact_id=artifact, operation=operation,
                       idempotency_key=key, reason="Release test", **extra))


def test_real_bearer_transport_and_header_precedence(setup):
    client, _, _ = setup
    token = headers()["Authorization"].split()[1]
    for path in ("/current", "/optional", PREFIX + "/capabilities"):
        assert client.get(path, headers=headers()).status_code == 200
        assert client.get(path, params={"access_token": token}).status_code == 200
        for invalid in ("Bearer invalid", "Basic abc", "Bearer", ""):
            assert client.get(path, params={"access_token": token}, headers={"Authorization": invalid}).status_code == 401
    assert client.get("/optional").json() is None
    assert client.get(PREFIX + "/capabilities").status_code == 401


def test_permission_default_disabled_and_origin_not_auth(setup, monkeypatch):
    client, factory, _ = setup
    assert client.get(PREFIX, headers={"Origin": "https://www.nonto.online"}).status_code == 401
    assert client.get(PREFIX, headers=headers(2)).status_code == 403
    assert client.get(PREFIX, headers=headers(3)).status_code == 403
    assert client.get(PREFIX, headers=headers(4)).status_code == 403
    assert client.get(PREFIX + "/capabilities", headers=headers(2)).json() == {"enabled": True, "can_deploy": False}
    monkeypatch.delenv("DEPLOYMENT_ENABLED")
    assert client.get(PREFIX + "/capabilities", headers=headers()).json() == {"enabled": False, "can_deploy": False}
    assert client.get(PREFIX, headers=headers()).status_code == 403
    with factory() as db:
        assert not deployment_service.worker_authorized(db, 1)


def test_upload_create_idempotency_approval_cancel_and_audit(setup, monkeypatch):
    client, factory, root = setup
    response = upload(client)
    assert response.status_code == 201, response.text
    artifact = response.json()["id"]
    assert (root / "artifacts" / artifact / "bundle.tar.gz").is_file()
    assert "manifest_json" not in response.json()
    first = create(client, artifact)
    assert first.status_code == 201, first.text
    job = first.json()
    assert job["status"] == "awaiting_approval"
    assert create(client, artifact).json()["id"] == job["id"]
    assert create(client, artifact, operation="register").status_code == 409
    monkeypatch.setenv("DEPLOYMENT_OPERATOR_IDS", "1,2")
    replay = client.post(PREFIX, headers=headers(2), json=dict(artifact_id=artifact, operation="deploy", idempotency_key="request-1", reason="Release test"))
    assert replay.status_code == 409
    assert create(client, artifact, key="extra", evil=True).status_code == 422
    endpoint = PREFIX + "/" + job["id"]
    assert client.post(endpoint + "/approve", headers=headers(), json={"reason": "Approve"}).status_code == 422
    approved = client.post(endpoint + "/approve", headers=headers(), json={"reason": "Approve", "migration_confirmed": True})
    assert approved.json()["status"] == "queued"
    assert approved.json()["approved_by"] == 1
    assert client.post(endpoint + "/approve", headers=headers(), json={"reason": "Again", "migration_confirmed": True}).status_code == 409
    assert client.post(endpoint + "/cancel", headers=headers(), json={"reason": "Stop"}).json()["status"] == "cancelled"
    assert client.post(endpoint + "/cancel", headers=headers(), json={"reason": "Stop"}).status_code == 409
    assert create(client, artifact, "preflight", "preflight").json()["status"] == "queued"
    assert client.get(PREFIX, headers=headers()).json()["total"] == 2
    assert client.get(PREFIX + "/artifacts", headers=headers()).json()["total"] == 1
    with factory() as db:
        assert db.query(AdminAuditLog).count() == 5
        assert deployment_service.worker_authorized(db, 1)
        db.query(UserRole).filter_by(user_id=1).delete()
        db.commit()
        assert not deployment_service.worker_authorized(db, 1)
    assert client.get(PREFIX, headers=headers()).status_code == 403


def test_active_status_and_sanitized_worker_output(setup):
    client, factory, _ = setup
    artifact = upload(client).json()["id"]
    job = create(client, artifact, "restore").json()
    path = PREFIX + "/" + job["id"]
    assert client.post(path + "/approve", headers=headers(), json={"reason": "Restore"}).status_code == 422
    assert client.post(path + "/approve", headers=headers(), json={"reason": "Restore", "schema_compatible": True}).status_code == 200
    with factory() as db:
        row = db.get(DeploymentJob, job["id"])
        row.status, row.phase = "running", "verify"
        row.events_json = json.dumps([{"phase": "verify", "stdout": "/private/secret", "error_code": "password leaked"}])
        row.error_code = "/secret/path"
        row.result_json = json.dumps({"verified": True, "password": "secret"})
        db.commit()
    assert client.get(PREFIX + "/active", headers=headers()).json()["job"]["id"] == job["id"]
    assert client.post(path + "/cancel", headers=headers(), json={"reason": "Stop"}).status_code == 409
    text = client.get(path, headers=headers()).text
    assert "secret" not in text and "stdout" not in text and "password" not in text
    with factory() as db:
        row = db.get(DeploymentJob, job["id"])
        row.status, row.finished_at = "succeeded", datetime.utcnow()
        db.commit()
    active = client.get(PREFIX + "/active", headers=headers()).json()
    assert active["job"] is None and active["release"] is None
    with factory() as db:
        row = db.get(DeploymentJob, job["id"])
        row.operation = "deploy"
        db.commit()
    active = client.get(PREFIX + "/active", headers=headers()).json()
    assert active["release"]["id"] == artifact
    assert active["release"]["verified_at"]
    with factory() as db:
        row = db.get(DeploymentJob, job["id"])
        row.status = "manual_recovery"
        db.commit()
    active = client.get(PREFIX + "/active", headers=headers()).json()
    assert active["job"]["id"] == job["id"] and active["release"] is None


@pytest.mark.parametrize("path", ["../escape.py", "/backend/app/main.py", "backend/app/../main.py", "backend/.env", "backend/app/uploads/secret.py", "backend/app/config.json", "backend/server_deploy.py", "backend/app/main.py:stream", "backend/app/secret.pem", "backend/app\\main.py"])
def test_reject_unsafe_paths(setup, path):
    client, factory, root = setup
    manifest, bundle = bundle_data(path)
    assert upload(client, manifest, bundle).status_code == 422
    with factory() as db:
        assert db.query(DeploymentArtifact).count() == 0
    assert not list((root / "artifacts").glob("*"))


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE])
def test_reject_non_regular_tar_members(setup, kind):
    client, _, _ = setup
    manifest, bundle = bundle_data(kind=kind)
    assert upload(client, manifest, bundle).status_code == 422


def test_hash_duplicate_and_expanded_limits(setup, monkeypatch):
    client, _, _ = setup
    manifest, bundle = bundle_data(duplicate=True)
    assert upload(client, manifest, bundle).status_code == 422
    manifest, bundle = bundle_data()
    manifest["files"][0]["sha256"] = "z" * 64
    assert upload(client, manifest, bundle).status_code == 422
    manifest, bundle = bundle_data()
    manifest["bundle_sha256"] = "a" * 64
    assert upload(client, manifest, bundle).status_code == 422
    monkeypatch.setenv("DEPLOYMENT_MAX_EXPANDED_BYTES", "1")
    assert upload(client).status_code == 422
    monkeypatch.setenv("DEPLOYMENT_MAX_BYTES", "1")
    assert upload(client).status_code in (413, 422)


def test_body_quota_before_parser_even_without_content_length(setup):
    client, _, _ = setup
    assert client.post(PREFIX, headers=headers(), content=b"x" * 16385).status_code == 413
    def chunks():
        yield b"x" * 10000
        yield b"x" * 10000
    response = client.post(PREFIX, headers={**headers(), "Content-Type": "application/json"}, content=chunks())
    assert response.status_code == 413


def test_audit_failure_rolls_back_artifact_and_job(setup, monkeypatch):
    client, factory, root = setup
    artifact = upload(client).json()["id"]
    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(deployments, "record_required_admin_audit", fail)
    assert upload(client).status_code == 500
    assert create(client, artifact).status_code == 500
    with factory() as db:
        assert db.query(DeploymentArtifact).count() == 1
        assert db.query(DeploymentJob).count() == 0
        assert db.query(AdminAuditLog).count() == 1
    assert len(list((root / "artifacts").iterdir())) == 1


def test_config_forbids_site_backend_and_bad_limits(setup, monkeypatch):
    _, _, root = setup
    monkeypatch.setenv("DEPLOYMENT_SITE_DIR", str(root))
    with pytest.raises(ValueError):
        deployment_service.get_deployment_config()


def test_shared_validator_is_standalone_and_bounded(tmp_path):
    manifest, bundle = bundle_data()
    path = tmp_path / "bundle.tar.gz"
    path.write_bytes(bundle)
    assert validate_bundle(path, manifest) == manifest
    with pytest.raises(ValueError):
        validate_bundle(path, manifest, max_files=0)
    manifest["extra"] = "rejected"
    with pytest.raises(ValueError):
        validate_manifest(manifest)
