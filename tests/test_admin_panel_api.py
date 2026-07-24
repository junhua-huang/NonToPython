import json
from datetime import datetime

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.dependencies import require_admin
from app.models.models import (
    AdminAuditLog,
    Base,
    Comment,
    ModerationEvent,
    Post,
    Report,
    Role,
    RoleApplication,
    SensitiveWord,
    SensitiveWordVersion,
    User,
    UserRole,
)
from app.routers import admin, admin_panel, interactions


@pytest.fixture()
def admin_panel_db():
    engine = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = Session()

    admin_role = Role(id=1, name="admin", label="管理员")
    user_role = Role(id=2, name="user", label="用户")
    admin = User(
        id=1,
        username="admin",
        email="admin@example.com",
        password_hash="x",
        is_active=True,
        is_admin=True,
        created_at=datetime.utcnow(),
    )
    target = User(
        id=2,
        username="target",
        email="target@example.com",
        password_hash="x",
        is_active=True,
        created_at=datetime.utcnow(),
    )
    reporter = User(
        id=3,
        username="reporter",
        email="reporter@example.com",
        password_hash="x",
        is_active=True,
        created_at=datetime.utcnow(),
    )
    db.add_all([admin_role, user_role, admin, target, reporter])
    db.flush()
    db.add_all([
        UserRole(user_id=1, role_id=1),
        UserRole(user_id=2, role_id=2),
        UserRole(user_id=3, role_id=2),
    ])
    db.add(SensitiveWordVersion(id=1, version=1))
    db.add(SensitiveWord(id=70, word="spam", match_type="literal", category="spam", severity="medium", is_active=True, row_version=1))
    db.add(Post(id=10, user_id=2, content="public post", images=json.dumps(["https://example.com/image.jpg"]), visibility="public", created_at=datetime.utcnow()))
    db.add(Comment(id=20, post_id=10, user_id=3, content="public comment", created_at=datetime.utcnow()))
    db.add(Report(id=30, reporter_id=3, target_type="post", target_id=10, reason="spam", status="pending", created_at=datetime.utcnow()))
    db.add(RoleApplication(id=40, user_id=2, role_id=2, status="pending", reason="verify me", proof_images=json.dumps(["https://example.com/proof.jpg"]), created_at=datetime.utcnow()))
    db.add(ModerationEvent(id=50, provider="local", content_type="text", route_key="posts.create", target_type="post", target_id="10", actor_user_id=2, decision="reject", error_code="CONTENT_REJECTED", label="spam", category="spam", score=90.0, created_at=datetime.utcnow()))
    db.add(AdminAuditLog(id=60, admin_user_id=1, action="seed", target_type="system", metadata_json=json.dumps({"status_after": "ok"}), created_at=datetime.utcnow()))
    db.commit()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def admin_panel_client(admin_panel_db):
    app = FastAPI()
    app.include_router(admin.moderation_router)
    app.include_router(admin_panel.router)

    def override_db():
        yield admin_panel_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_admin] = lambda: admin_panel_db.get(User, 1)
    with TestClient(app) as client:
        yield client


def test_admin_panel_router_is_registered_in_main_app():
    from app.main import app

    route_paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/admin/auth/me" in route_paths
    assert "/api/admin/dashboard/summary" in route_paths


def test_admin_base_routes_return_safe_dashboard_and_audit_logs(admin_panel_client):
    me = admin_panel_client.get("/api/admin/auth/me")
    assert me.status_code == 200
    assert me.json()["roles"] == ["admin"]

    summary = admin_panel_client.get("/api/admin/dashboard/summary")
    assert summary.status_code == 200
    assert summary.json()["users_total"] == 3
    assert summary.json()["reports_pending"] == 1

    logs = admin_panel_client.get("/api/admin/audit-logs")
    assert logs.status_code == 200
    body = json.dumps(logs.json(), ensure_ascii=False)
    assert "seed" in body
    assert "signed_url" not in body
    assert "SecretKey" not in body
    assert "raw_response" not in body


def test_admin_user_deactivate_and_reactivate_write_audit_logs(admin_panel_client, admin_panel_db):
    missing_reason = admin_panel_client.post("/api/admin/users/2/deactivate", json={})
    assert missing_reason.status_code == 422

    deactivated = admin_panel_client.post("/api/admin/users/2/deactivate", json={"reason": "policy"})
    assert deactivated.status_code == 200
    assert deactivated.json()["user"]["is_active"] is False
    assert admin_panel_db.get(User, 2).is_active is False

    reactivated = admin_panel_client.post("/api/admin/users/2/reactivate", json={"reason": "appeal"})
    assert reactivated.status_code == 200
    assert reactivated.json()["user"]["is_active"] is True
    assert admin_panel_db.query(AdminAuditLog).filter(AdminAuditLog.action == "deactivate_user").count() == 1
    assert admin_panel_db.query(AdminAuditLog).filter(AdminAuditLog.action == "reactivate_user").count() == 1


def test_admin_content_hide_restore_and_report_resolution(admin_panel_client, admin_panel_db):
    hidden_post = admin_panel_client.post("/api/admin/posts/10/hide", json={"reason": "violation"})
    assert hidden_post.status_code == 200
    assert hidden_post.json()["post"]["hidden_by_admin"] is True

    restored_post = admin_panel_client.post("/api/admin/posts/10/restore", json={"reason": "appeal"})
    assert restored_post.status_code == 200
    assert restored_post.json()["post"]["hidden_by_admin"] is False

    hidden_comment = admin_panel_client.post("/api/admin/comments/20/hide", json={"reason": "violation"})
    assert hidden_comment.status_code == 200
    assert hidden_comment.json()["comment"]["hidden_by_admin"] is True

    resolved_report = admin_panel_client.post(
        "/api/admin/reports/30/resolve",
        json={"reason": "confirmed", "action_taken": "hide_post"},
    )
    assert resolved_report.status_code == 200
    assert resolved_report.json()["report"]["status"] == "resolved"
    assert admin_panel_db.get(Post, 10).hidden_by_admin is True
    assert admin_panel_db.query(AdminAuditLog).filter(AdminAuditLog.action == "resolve_report").count() == 1


def test_sensitive_word_mutations_write_admin_audit_logs(admin_panel_client, admin_panel_db):
    created = admin_panel_client.post(
        "/api/admin/moderation/sensitive-words",
        json={"word": "badword", "match_type": "literal", "category": "spam", "severity": "low", "is_active": True},
    )
    assert created.status_code == 201
    created_id = created.json()["word"]["id"]

    patched = admin_panel_client.patch(
        f"/api/admin/moderation/sensitive-words/{created_id}",
        json={"severity": "high", "is_active": False},
    )
    assert patched.status_code == 200

    deleted = admin_panel_client.delete(f"/api/admin/moderation/sensitive-words/{created_id}")
    assert deleted.status_code == 200

    actions = {row.action for row in admin_panel_db.query(AdminAuditLog).all()}
    assert "create_sensitive_word" in actions
    assert "update_sensitive_word" in actions
    assert "delete_sensitive_word" in actions


def test_hidden_admin_comment_is_not_visible_to_public_comment_loader(admin_panel_db):
    comment = admin_panel_db.get(Comment, 20)
    comment.hidden_by_admin = True
    admin_panel_db.commit()

    with pytest.raises(Exception) as exc_info:
        interactions._load_visible_comment(20, admin_panel_db, 3)

    assert getattr(exc_info.value, "status_code", None) == 404


def test_identity_approval_grants_role_and_suspend_revokes_role(admin_panel_client, admin_panel_db):
    assert admin_panel_db.query(UserRole).filter(UserRole.user_id == 2, UserRole.role_id == 2).first() is not None
    admin_panel_db.query(UserRole).filter(UserRole.user_id == 2, UserRole.role_id == 2).delete()
    admin_panel_db.commit()

    approved = admin_panel_client.post("/api/admin/identity-applications/40/approve", json={"reason": "ok"})
    assert approved.status_code == 200
    assert admin_panel_db.query(UserRole).filter(UserRole.user_id == 2, UserRole.role_id == 2).first() is not None

    suspended = admin_panel_client.post("/api/admin/identity-applications/40/suspend", json={"reason": "risk"})
    assert suspended.status_code == 200
    assert admin_panel_db.query(UserRole).filter(UserRole.user_id == 2, UserRole.role_id == 2).first() is None


def test_admin_identity_applications_and_moderation_events(admin_panel_client):
    listed = admin_panel_client.get("/api/admin/identity-applications?status=pending")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    approved = admin_panel_client.post("/api/admin/identity-applications/40/approve", json={"reason": "ok"})
    assert approved.status_code == 200
    assert approved.json()["application"]["status"] == "verified"

    events = admin_panel_client.get("/api/admin/moderation/events")
    assert events.status_code == 200
    body = json.dumps(events.json(), ensure_ascii=False)
    assert "CONTENT_REJECTED" in body
    assert "signed_url" not in body
    assert "cos_key" not in body
    assert "raw_response" not in body
    assert "body" not in body
