import hashlib
import json
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import auth_core
from app.core.config import Config
from app.database import Base, get_db
from app.models.models import User, Role, UserRole
from app.models.privacy import AccountErasure
from app.routers import legal, privacy_admin


@pytest.fixture
def privacy_client(monkeypatch):
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread":False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add_all([User(id=1, username="admin", email="admin@example.test", password_hash="x"),
                    User(id=2, username="member", email="member@example.test", password_hash="x"),
                    Role(id=1, name="admin", label="管理员")])
        db.flush()
        db.add(UserRole(user_id=1, role_id=1))
        now = datetime.utcnow()
        db.add(AccountErasure(id="test-erasure", user_id=99, created_at=now, final_sweep_after=now+timedelta(hours=1),
                             next_attempt_at=now, legacy_keys_json='["old/private-path.jpg"]'))
        db.commit()
    app = FastAPI()
    app.include_router(legal.router)
    app.include_router(privacy_admin.router)

    def test_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = test_db
    monkeypatch.setattr(auth_core, "SessionLocal", factory)
    with TestClient(app) as client:
        yield client
    engine.dispose()


def auth(user_id):
    token = jwt.encode({"sub":str(user_id)}, Config.JWT_SECRET_KEY, algorithm="HS256")
    return {"Authorization":f"Bearer {token}"}


def test_erasure_operations_are_admin_only_and_object_keys_are_opt_in(privacy_client):
    client = privacy_client
    for url in ("/api/admin/privacy/status", "/api/admin/privacy/erasures/test-erasure?include_legacy_keys=true"):
        assert client.get(url).status_code == 401
        assert client.get(url, headers=auth(2)).status_code == 403
    response = client.get("/api/admin/privacy/status", headers=auth(1))
    assert response.status_code == 200
    assert response.json()["backup_unverified"] == 1
    result = client.get("/api/admin/privacy/erasures/test-erasure", headers=auth(1)).json()
    assert result["legacy_review_count"] == 1
    assert "private-path" not in json.dumps(result)
    detail = client.get("/api/admin/privacy/erasures/test-erasure?include_legacy_keys=true", headers=auth(1)).json()
    assert detail["legacy_keys_for_ownership_review"] == ["old/private-path.jpg"]


def test_public_legal_pages_are_exact_bundles_and_keep_unverified_release_state(privacy_client):
    manifest = privacy_client.get("/api/legal/manifest.json").json()
    assert manifest["policyVersion"] == "2026-10-23.4"
    assert manifest["releaseReady"] is False
    for name in ("privacy.html", "terms.html", "community.html"):
        response = privacy_client.get("/api/legal/"+name)
        assert response.status_code == 200
        assert response.content == (legal.LEGAL_ROOT/name).read_bytes()
        assert response.headers["etag"] == '"'+hashlib.sha256(response.content).hexdigest()+'"'
        assert response.headers["cache-control"] == "no-cache"
        assert "{{" not in response.text
        assert "<script" not in response.text.lower()
    assert privacy_client.get("/PrivacyPolicy/", follow_redirects=False).headers["location"] == "/api/legal/privacy"
    assert privacy_client.get("/api/legal/other.env").status_code == 404
