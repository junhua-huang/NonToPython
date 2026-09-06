import json
from datetime import datetime
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.dependencies import require_admin
from app.models.models import AdminAuditLog, AppRelease, User
from app.routers import app_updates


PUBLIC_URL = "/api/app/version"
ADMIN_URL = "/api/admin/app-releases"
CONFLICT_DETAIL = {
    "code": "APP_RELEASE_BUILD_CONFLICT",
    "message": "build number 与已有发布冲突或未保持单调递增",
}


@pytest.fixture()
def app_update_db():
    engine = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    User.__table__.create(engine)
    AppRelease.__table__.create(engine)
    AdminAuditLog.__table__.create(engine)
    session = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )()
    session.add(
        User(
            id=1,
            username="release-admin",
            email="release-admin@example.test",
            password_hash="x",
            is_active=True,
            is_admin=True,
        )
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def app_update_app(app_update_db):
    test_app = FastAPI()
    test_app.include_router(app_updates.public_router)
    test_app.include_router(app_updates.admin_router)

    def override_db():
        yield app_update_db

    test_app.dependency_overrides[get_db] = override_db
    test_app.dependency_overrides[require_admin] = lambda: SimpleNamespace(id=1)
    return test_app


@pytest.fixture()
def app_update_client(app_update_app):
    with TestClient(app_update_app) as client:
        yield client


def _add_release(
    db,
    *,
    platform="android",
    channel="stable",
    version_name="1.0.0",
    build_number=100,
    minimum_supported_build_number=0,
    force_update=False,
    enabled=True,
    download_url="https://downloads.example.test/app.apk",
):
    release = AppRelease(
        platform=platform,
        channel=channel,
        version_name=version_name,
        build_number=build_number,
        minimum_supported_build_number=minimum_supported_build_number,
        force_update=force_update,
        update_action="download",
        download_url=download_url,
        release_notes=json.dumps([f"Release {version_name}"]),
        published_at=datetime(2026, 9, 4, 8, 0, 0),
        enabled=enabled,
    )
    db.add(release)
    db.commit()
    return release


def _create_payload(**overrides):
    payload = {
        "platform": "android",
        "channel": "stable",
        "version_name": "1.0.0",
        "build_number": 100,
        "minimum_supported_build_number": 80,
        "force_update": False,
        "update_action": "download",
        "download_url": "https://downloads.example.test/app.apk",
        "release_notes": ["First release"],
        "sha256": "a" * 64,
        "file_size": 1024,
        "enabled": True,
        "reason": "initial publication",
    }
    payload.update(overrides)
    return payload


def test_public_version_is_anonymous_and_returns_empty_result_without_release(
    app_update_client,
):
    response = app_update_client.get(
        PUBLIC_URL,
        params={"platform": "android", "current_build_number": 12},
    )

    assert response.status_code == 200
    assert response.json() == {
        "platform": "android",
        "channel": "stable",
        "release_id": None,
        "latest_version": None,
        "latest_build_number": None,
        "minimum_supported_build_number": None,
        "update_available": False,
        "force_update": False,
        "update_action": None,
        "download_url": None,
        "release_notes": [],
        "published_at": None,
        "sha256": None,
        "file_size": None,
    }


def test_public_version_covers_optional_forced_current_and_newer_clients(
    app_update_client,
    app_update_db,
):
    release = _add_release(
        app_update_db,
        version_name="2.3.0",
        build_number=230,
        minimum_supported_build_number=200,
    )

    optional = app_update_client.get(
        PUBLIC_URL,
        params={
            "platform": "android",
            "current_version": "2.1.0",
            "current_build_number": 210,
        },
    )
    assert optional.status_code == 200
    assert optional.json()["release_id"] == release.id
    assert optional.json()["update_available"] is True
    assert optional.json()["force_update"] is False
    assert optional.json()["download_url"].startswith("https://")

    forced = app_update_client.get(
        PUBLIC_URL,
        params={"platform": "android", "current_build_number": 199},
    )
    assert forced.status_code == 200
    assert forced.json()["update_available"] is True
    assert forced.json()["force_update"] is True

    current = app_update_client.get(
        PUBLIC_URL,
        params={"platform": "android", "current_build_number": 230},
    )
    newer = app_update_client.get(
        PUBLIC_URL,
        params={"platform": "android", "current_build_number": 999},
    )
    for response in (current, newer):
        assert response.status_code == 200
        assert response.json()["update_available"] is False
        assert response.json()["force_update"] is False
        assert response.json()["download_url"] is None
        assert response.json()["release_notes"] == []


def test_public_force_update_flag_forces_any_older_client(
    app_update_client,
    app_update_db,
):
    _add_release(
        app_update_db,
        build_number=300,
        minimum_supported_build_number=0,
        force_update=True,
    )

    response = app_update_client.get(
        PUBLIC_URL,
        params={"platform": "android", "current_build_number": 299},
    )

    assert response.status_code == 200
    assert response.json()["force_update"] is True


def test_public_lookup_isolated_by_platform_channel_and_enabled_state(
    app_update_client,
    app_update_db,
):
    _add_release(app_update_db, platform="android", channel="stable", build_number=100)
    _add_release(app_update_db, platform="android", channel="beta", build_number=200)
    _add_release(app_update_db, platform="ios", channel="stable", build_number=300)
    _add_release(
        app_update_db,
        platform="android",
        channel="stable",
        build_number=400,
        enabled=False,
    )

    cases = [
        ({"platform": "android", "channel": "stable"}, 100),
        ({"platform": "android", "channel": "beta"}, 200),
        ({"platform": "ios", "channel": "stable"}, 300),
    ]
    for params, expected_build in cases:
        response = app_update_client.get(
            PUBLIC_URL,
            params={**params, "current_build_number": 0},
        )
        assert response.status_code == 200
        assert response.json()["latest_build_number"] == expected_build

    missing = app_update_client.get(
        PUBLIC_URL,
        params={"platform": "windows", "channel": "stable"},
    )
    assert missing.status_code == 200
    assert missing.json()["release_id"] is None


@pytest.mark.parametrize(
    "params",
    [
        {"platform": "linux"},
        {"platform": "android", "channel": "Bad Channel"},
        {"platform": "android", "channel": ""},
        {"platform": "android", "current_build_number": -1},
        {"platform": "android", "current_version": ""},
        {"platform": "android", "current_version": "v" * 65},
    ],
)
def test_public_version_rejects_invalid_parameters(app_update_client, params):
    response = app_update_client.get(PUBLIC_URL, params=params)

    assert response.status_code == 422


def test_public_database_failure_returns_fixed_safe_503():
    private_error = "PRIVATE_DATABASE_DSN_mysql_password_8841"

    class BrokenSession:
        def query(self, _model):
            raise RuntimeError(private_error)

    test_app = FastAPI()
    test_app.include_router(app_updates.public_router)

    def override_db():
        yield BrokenSession()

    test_app.dependency_overrides[get_db] = override_db
    with TestClient(test_app, raise_server_exceptions=False) as client:
        response = client.get(PUBLIC_URL, params={"platform": "android"})

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "VERSION_CONFIG_UNAVAILABLE",
            "message": "版本服务暂不可用",
            "retryable": True,
        }
    }
    assert private_error not in response.text


def test_admin_routes_require_authentication_without_override(app_update_db):
    test_app = FastAPI()
    test_app.include_router(app_updates.admin_router)

    def override_db():
        yield app_update_db

    test_app.dependency_overrides[get_db] = override_db
    with TestClient(test_app, raise_server_exceptions=False) as client:
        response = client.get(ADMIN_URL)

    assert response.status_code == 401


def test_admin_crud_persists_required_audit_logs(
    app_update_client,
    app_update_db,
):
    created = app_update_client.post(
        ADMIN_URL,
        json=_create_payload(sha256="A" * 64),
        headers={"user-agent": "release-test-agent"},
    )
    assert created.status_code == 201
    release_id = created.json()["id"]
    assert created.json()["sha256"] == "a" * 64
    assert created.json()["release_notes"] == ["First release"]

    listed = app_update_client.get(ADMIN_URL)
    detail = app_update_client.get(f"{ADMIN_URL}/{release_id}")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == release_id
    assert detail.status_code == 200
    assert detail.json()["id"] == release_id

    missing_patch_reason = app_update_client.patch(
        f"{ADMIN_URL}/{release_id}",
        json={"version_name": "1.0.1"},
    )
    assert missing_patch_reason.status_code == 422

    patched = app_update_client.patch(
        f"{ADMIN_URL}/{release_id}",
        json={
            "version_name": "1.1.0",
            "build_number": 110,
            "minimum_supported_build_number": 90,
            "download_url": "https://downloads.example.test/app-1.1.apk",
            "release_notes": ["Updated release"],
            "reason": "publish replacement build",
        },
    )
    assert patched.status_code == 200
    assert patched.json()["build_number"] == 110
    assert patched.json()["release_notes"] == ["Updated release"]

    missing_delete_reason = app_update_client.request(
        "DELETE",
        f"{ADMIN_URL}/{release_id}",
        json={},
    )
    assert missing_delete_reason.status_code == 422

    deleted = app_update_client.request(
        "DELETE",
        f"{ADMIN_URL}/{release_id}",
        json={"reason": "withdraw release"},
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": release_id}
    assert app_update_db.get(AppRelease, release_id) is None

    audits = (
        app_update_db.query(AdminAuditLog)
        .filter(AdminAuditLog.target_type == "app_release")
        .order_by(AdminAuditLog.id)
        .all()
    )
    assert [audit.action for audit in audits] == [
        "create_app_release",
        "update_app_release",
        "delete_app_release",
    ]
    assert [audit.reason for audit in audits] == [
        "initial publication",
        "publish replacement build",
        "withdraw release",
    ]
    assert all(audit.target_id == str(release_id) for audit in audits)
    assert audits[0].user_agent == "release-test-agent"


def test_admin_rejects_non_https_create_and_patch_urls(
    app_update_client,
    app_update_db,
):
    invalid_create = app_update_client.post(
        ADMIN_URL,
        json=_create_payload(download_url="http://downloads.example.test/app.apk"),
    )
    assert invalid_create.status_code == 422
    assert app_update_db.query(AppRelease).count() == 0
    assert app_update_db.query(AdminAuditLog).count() == 0

    release = _add_release(app_update_db)
    invalid_patch = app_update_client.patch(
        f"{ADMIN_URL}/{release.id}",
        json={
            "download_url": "ftp://downloads.example.test/app.apk",
            "reason": "invalid URL test",
        },
    )
    assert invalid_patch.status_code == 422
    app_update_db.refresh(release)
    assert release.download_url.startswith("https://")


def test_admin_enforces_unique_monotonic_builds_per_platform_and_channel(
    app_update_client,
    app_update_db,
):
    first = app_update_client.post(ADMIN_URL, json=_create_payload())
    assert first.status_code == 201

    for build_number in (100, 99):
        conflict = app_update_client.post(
            ADMIN_URL,
            json=_create_payload(
                build_number=build_number,
                minimum_supported_build_number=0,
                enabled=False,
                reason=f"attempt build {build_number}",
            ),
        )
        assert conflict.status_code == 409
        assert conflict.json() == {"detail": CONFLICT_DETAIL}

    draft = app_update_client.post(
        ADMIN_URL,
        json=_create_payload(
            version_name="1.0.1",
            build_number=101,
            enabled=False,
            reason="prepare next build",
        ),
    )
    assert draft.status_code == 201

    promoted_first = app_update_client.patch(
        f"{ADMIN_URL}/{first.json()['id']}",
        json={
            "build_number": 102,
            "minimum_supported_build_number": 80,
            "reason": "promote current release",
        },
    )
    assert promoted_first.status_code == 200

    decreased = app_update_client.patch(
        f"{ADMIN_URL}/{first.json()['id']}",
        json={
            "build_number": 101,
            "minimum_supported_build_number": 80,
            "reason": "attempt build rollback",
        },
    )
    assert decreased.status_code == 409
    assert decreased.json() == {"detail": CONFLICT_DETAIL}

    stale_draft = app_update_client.patch(
        f"{ADMIN_URL}/{draft.json()['id']}",
        json={"enabled": True, "reason": "attempt stale promotion"},
    )
    assert stale_draft.status_code == 409
    assert stale_draft.json() == {"detail": CONFLICT_DETAIL}

    ios_same_build = app_update_client.post(
        ADMIN_URL,
        json=_create_payload(
            platform="ios",
            build_number=100,
            download_url="https://apps.example.test/ios",
            reason="ios release",
        ),
    )
    beta_same_build = app_update_client.post(
        ADMIN_URL,
        json=_create_payload(
            channel="beta",
            build_number=100,
            reason="beta release",
        ),
    )
    assert ios_same_build.status_code == 201
    assert beta_same_build.status_code == 201

    app_update_db.expire_all()
    assert app_update_db.get(AppRelease, draft.json()["id"]).enabled is False
    successful_audits = app_update_db.query(AdminAuditLog).count()
    assert successful_audits == 5


def test_admin_validates_minimum_build_and_not_found_contracts(app_update_client):
    invalid_minimum = app_update_client.post(
        ADMIN_URL,
        json=_create_payload(
            build_number=10,
            minimum_supported_build_number=11,
        ),
    )
    assert invalid_minimum.status_code == 422

    assert app_update_client.get(f"{ADMIN_URL}/999999").status_code == 404
    assert app_update_client.patch(
        f"{ADMIN_URL}/999999",
        json={"enabled": False, "reason": "missing release"},
    ).status_code == 404
    assert app_update_client.request(
        "DELETE",
        f"{ADMIN_URL}/999999",
        json={"reason": "missing release"},
    ).status_code == 404
