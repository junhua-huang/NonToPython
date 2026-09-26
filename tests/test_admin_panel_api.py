import json
from datetime import datetime

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
import app.dependencies as dependencies
from app.dependencies import require_admin
from app.models.models import (
    AdminAuditLog,
    AdminSetting,
    Base,
    Comment,
    ModerationEvent,
    Notification,
    NotificationDelivery,
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
    inactive = User(
        id=4,
        username="inactive",
        email="inactive@qq.com",
        password_hash="x",
        is_active=False,
        created_at=datetime.utcnow(),
    )
    db.add_all([admin_role, user_role, admin, target, reporter, inactive])
    db.flush()
    db.add_all([
        UserRole(user_id=1, role_id=1),
        UserRole(user_id=2, role_id=2),
        UserRole(user_id=3, role_id=2),
        UserRole(user_id=4, role_id=2),
    ])
    db.add(SensitiveWordVersion(id=1, version=1))
    db.add(SensitiveWord(id=70, word="spam", match_type="literal", category="spam", severity="medium", is_active=True, row_version=1))
    db.add(Post(id=10, user_id=2, content="public post", images=json.dumps(["https://example.com/image.jpg"]), visibility="public", created_at=datetime.utcnow()))
    db.add(Post(id=11, user_id=3, content="quoted original", images=json.dumps(["https://example.com/quoted.jpg"]), visibility="public", created_at=datetime.utcnow()))
    db.add(Post(id=12, user_id=2, content="quote wrapper", quoted_post_id=11, visibility="public", created_at=datetime.utcnow()))
    db.add(Post(id=13, user_id=4, content="hidden inactive author post", visibility="private", hidden_by_admin=True, created_at=datetime.utcnow()))
    db.add(Comment(id=20, post_id=10, user_id=3, content="public comment", created_at=datetime.utcnow()))
    db.add(Comment(id=21, post_id=10, user_id=4, content="hidden inactive comment", hidden_by_admin=True, created_at=datetime.utcnow()))
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
def admin_panel_client(admin_panel_db, monkeypatch):
    app = FastAPI()
    app.include_router(admin.moderation_router)
    app.include_router(admin_panel.router)

    def override_db():
        yield admin_panel_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_admin] = lambda: admin_panel_db.get(User, 1)
    monkeypatch.setattr(admin_panel, "_dispatch_after_commit", lambda deliveries, notifications: None)
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
    assert summary.json()["users_total"] == 4
    assert summary.json()["reports_pending"] == 1

    logs = admin_panel_client.get("/api/admin/audit-logs")
    assert logs.status_code == 200
    body = json.dumps(logs.json(), ensure_ascii=False)
    assert "seed" in body
    assert "signed_url" not in body
    assert "SecretKey" not in body
    assert "raw_response" not in body


def test_admin_users_search_by_username_email_id_status_and_role(admin_panel_client):
    by_username = admin_panel_client.get("/api/admin/users", params={"q": "target"})
    assert by_username.status_code == 200
    assert [item["id"] for item in by_username.json()["items"]] == [2]

    by_email = admin_panel_client.get("/api/admin/users", params={"q": "inactive@qq.com"})
    assert by_email.status_code == 200
    assert [item["id"] for item in by_email.json()["items"]] == [4]

    by_id = admin_panel_client.get("/api/admin/users", params={"q": "3"})
    assert by_id.status_code == 200
    assert any(item["id"] == 3 for item in by_id.json()["items"])

    inactive = admin_panel_client.get("/api/admin/users", params={"status": "inactive", "role": "user"})
    assert inactive.status_code == 200
    assert inactive.json()["total"] == 1
    assert inactive.json()["items"][0]["email"] == "inactive@qq.com"
    assert inactive.json()["items"][0]["is_active"] is False


def test_admin_posts_search_filters_and_author_snapshot(admin_panel_client):
    by_content = admin_panel_client.get("/api/admin/posts", params={"q": "quoted original"})
    assert by_content.status_code == 200
    assert [item["id"] for item in by_content.json()["items"]] == [11]
    assert by_content.json()["items"][0]["author"]["email"] == "reporter@example.com"

    by_author_email = admin_panel_client.get("/api/admin/posts", params={"q": "inactive@qq.com"})
    assert by_author_email.status_code == 200
    assert [item["id"] for item in by_author_email.json()["items"]] == [13]

    hidden = admin_panel_client.get("/api/admin/posts", params={"hidden": "true", "visibility": "private"})
    assert hidden.status_code == 200
    assert [item["id"] for item in hidden.json()["items"]] == [13]

    quotes = admin_panel_client.get("/api/admin/posts", params={"is_quote": "true"})
    assert quotes.status_code == 200
    assert [item["id"] for item in quotes.json()["items"]] == [12]


def test_admin_comments_search_filters_and_author_snapshot(admin_panel_client):
    by_content = admin_panel_client.get("/api/admin/comments", params={"q": "public comment"})
    assert by_content.status_code == 200
    assert [item["id"] for item in by_content.json()["items"]] == [20]
    assert by_content.json()["items"][0]["author"]["email"] == "reporter@example.com"

    by_author_email = admin_panel_client.get("/api/admin/comments", params={"q": "inactive@qq.com"})
    assert by_author_email.status_code == 200
    assert [item["id"] for item in by_author_email.json()["items"]] == [21]

    hidden = admin_panel_client.get("/api/admin/comments", params={"hidden": "true"})
    assert hidden.status_code == 200
    assert [item["id"] for item in hidden.json()["items"]] == [21]


def test_admin_reports_identity_moderation_and_audit_filters(admin_panel_client):
    reports = admin_panel_client.get("/api/admin/reports", params={"q": "reporter@example.com", "status": "pending", "target_type": "post"})
    assert reports.status_code == 200
    assert reports.json()["total"] == 1
    assert reports.json()["items"][0]["reporter"]["email"] == "reporter@example.com"

    applications = admin_panel_client.get("/api/admin/identity-applications", params={"q": "target@example.com", "status": "pending", "role_id": "2"})
    assert applications.status_code == 200
    assert applications.json()["total"] == 1
    assert applications.json()["items"][0]["user"]["email"] == "target@example.com"

    events = admin_panel_client.get("/api/admin/moderation/events", params={"q": "CONTENT", "decision": "reject", "content_type": "text", "provider": "local", "target_type": "post"})
    assert events.status_code == 200
    assert events.json()["total"] == 1
    assert events.json()["items"][0]["error_code"] == "CONTENT_REJECTED"

    audit_logs = admin_panel_client.get("/api/admin/audit-logs", params={"q": "system", "result": "success", "target_type": "system"})
    assert audit_logs.status_code == 200
    assert audit_logs.json()["total"] == 1
    assert audit_logs.json()["items"][0]["action"] == "seed"


def test_admin_sensitive_words_search_and_filter(admin_panel_client):
    response = admin_panel_client.get(
        "/api/admin/moderation/sensitive-words",
        params={"q": "spa", "is_active": "true", "match_type": "literal", "severity": "medium"},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["words"][0]["word"] == "spam"


def test_admin_me_serializes_roles_from_request_db_when_token_user_is_detached(
    admin_panel_db,
    monkeypatch,
):
    app = FastAPI()
    app.include_router(admin_panel.router)

    def override_db():
        yield admin_panel_db

    app.dependency_overrides[get_db] = override_db

    detached_admin = admin_panel_db.get(User, 1)
    admin_panel_db.expunge(detached_admin)
    monkeypatch.setattr(dependencies, "verify_token", lambda token: detached_admin)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/admin/auth/me?access_token=test-token")

    assert response.status_code == 200
    assert response.json()["roles"] == ["admin"]


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


def test_account_governance_creates_user_notice_and_required_email_delivery(admin_panel_client, admin_panel_db):
    response = admin_panel_client.post(
        "/api/admin/users/2/deactivate",
        json={
            "admin_reason": "internal policy note",
            "user_notice": "你的账号因违反社区规则已被停用。",
            "notify_channels": ["in_app", "push", "email"],
        },
    )

    assert response.status_code == 200
    notice = admin_panel_db.query(Notification).filter(
        Notification.user_id == 2,
        Notification.notification_type == "account_deactivated",
    ).one()
    assert notice.content == "你的账号因违反社区规则已被停用。"
    assert "internal policy note" not in notice.content

    delivery = admin_panel_db.query(NotificationDelivery).filter(
        NotificationDelivery.user_id == 2,
        NotificationDelivery.channel == "email",
        NotificationDelivery.event_type == "account_deactivated",
    ).one()
    assert delivery.recipient_email == "target@example.com"
    assert delivery.status in {"pending", "sent", "failed"}
    assert "你的账号因违反社区规则已被停用。" in delivery.body
    assert "internal policy note" not in delivery.body


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


def test_admin_post_detail_includes_quoted_post_snapshot(admin_panel_client):
    detail = admin_panel_client.get("/api/admin/posts/12")

    assert detail.status_code == 200
    assert detail.json()["quoted_post_id"] == 11
    assert detail.json()["quoted_post"]["id"] == 11
    assert detail.json()["quoted_post"]["content"] == "quoted original"
    assert detail.json()["quoted_post"]["author"]["id"] == 3
    body = json.dumps(detail.json(), ensure_ascii=False)
    assert "signed_url" not in body
    assert "SecretKey" not in body


def test_admin_comment_detail_and_content_governance_notice(admin_panel_client, admin_panel_db):
    detail = admin_panel_client.get("/api/admin/comments/20")
    assert detail.status_code == 200
    assert detail.json()["content"] == "public comment"
    assert detail.json()["author"]["id"] == 3
    assert detail.json()["post"]["id"] == 10

    response = admin_panel_client.post(
        "/api/admin/posts/10/hide",
        json={
            "admin_reason": "internal content review",
            "user_notice": "你的帖子因违反社区规则已被隐藏。",
            "notify_channels": ["in_app", "push"],
        },
    )
    assert response.status_code == 200
    notice = admin_panel_db.query(Notification).filter(
        Notification.user_id == 2,
        Notification.notification_type == "post_hidden",
    ).one()
    assert notice.content == "你的帖子因违反社区规则已被隐藏。"
    assert "internal content review" not in notice.content
    assert admin_panel_db.query(NotificationDelivery).filter(
        NotificationDelivery.event_type == "post_hidden",
        NotificationDelivery.channel == "email",
    ).count() == 0


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


def test_sensitive_word_mutations_attach_required_audit_before_commit(
    admin_panel_client,
    monkeypatch,
):
    commit_observations = []
    real_commit_or_conflict = admin._commit_or_conflict

    def observe_commit_boundary(db):
        commit_observations.append([
            row.action for row in db.new if isinstance(row, AdminAuditLog)
        ])
        real_commit_or_conflict(db)

    monkeypatch.setattr(admin, "_commit_or_conflict", observe_commit_boundary)

    created = admin_panel_client.post(
        "/api/admin/moderation/sensitive-words",
        json={"word": "atomic-audit", "match_type": "literal", "category": "spam", "severity": "low", "is_active": True},
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

    assert commit_observations == [
        ["create_sensitive_word"],
        ["update_sensitive_word"],
        ["delete_sensitive_word"],
    ]


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


def test_identity_review_creates_user_notice_and_required_email_delivery(admin_panel_client, admin_panel_db):
    response = admin_panel_client.post(
        "/api/admin/identity-applications/40/reject",
        json={
            "admin_reason": "internal evidence mismatch",
            "user_notice": "认证资料不完整，请补充清晰证明后重新提交。",
            "notify_channels": ["in_app", "push", "email"],
        },
    )

    assert response.status_code == 200
    notice = admin_panel_db.query(Notification).filter(
        Notification.user_id == 2,
        Notification.notification_type == "identity_rejected",
    ).one()
    assert notice.content == "认证资料不完整，请补充清晰证明后重新提交。"
    assert "internal evidence mismatch" not in notice.content

    delivery = admin_panel_db.query(NotificationDelivery).filter(
        NotificationDelivery.user_id == 2,
        NotificationDelivery.channel == "email",
        NotificationDelivery.event_type == "identity_rejected",
    ).one()
    assert delivery.recipient_email == "target@example.com"
    assert "认证资料不完整，请补充清晰证明后重新提交。" in delivery.body
    assert "internal evidence mismatch" not in delivery.body


def test_report_detail_includes_safe_target_snapshot_and_reporter_notice(admin_panel_client, admin_panel_db):
    detail = admin_panel_client.get("/api/admin/reports/30")
    assert detail.status_code == 200
    assert detail.json()["target_snapshot"]["id"] == 10
    assert detail.json()["target_snapshot"]["content"] == "public post"
    assert detail.json()["reporter"]["id"] == 3

    resolved = admin_panel_client.post(
        "/api/admin/reports/30/resolve",
        json={
            "admin_reason": "internal reporter context",
            "reporter_notice": "你提交的举报已处理，感谢反馈。",
            "target_notice": "你的帖子因违反社区规则已被隐藏。",
            "action_taken": "hide_post",
            "notify_channels": ["in_app", "push"],
        },
    )
    assert resolved.status_code == 200
    reporter_notice = admin_panel_db.query(Notification).filter(
        Notification.user_id == 3,
        Notification.notification_type == "report_resolved",
    ).one()
    target_notice = admin_panel_db.query(Notification).filter(
        Notification.user_id == 2,
        Notification.notification_type == "post_hidden",
    ).one()
    assert "举报已处理" in reporter_notice.content
    assert "举报" not in target_notice.content
    assert "reporter" not in target_notice.content.lower()



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


def test_admin_can_toggle_image_moderation_setting_with_audit(admin_panel_client, admin_panel_db):
    initial = admin_panel_client.get("/api/admin/settings/moderation")
    assert initial.status_code == 200
    assert initial.json()["image_moderation_enabled"] is False
    assert initial.json()["image_moderation_provider"] == "tencent_cos_ci"
    assert initial.json()["source"] == "environment"
    assert "secret" not in json.dumps(initial.json()).lower()

    response = admin_panel_client.patch(
        "/api/admin/settings/moderation",
        json={"image_moderation_enabled": True, "reason": "开启图片审核"},
    )

    assert response.status_code == 200
    assert response.json()["image_moderation_enabled"] is True
    assert response.json()["source"] == "database"
    setting = admin_panel_db.query(AdminSetting).filter(AdminSetting.key == "image_moderation_enabled").one()
    assert setting.value == "true"
    audit = admin_panel_db.query(AdminAuditLog).filter(AdminAuditLog.action == "update_moderation_settings").one()
    assert audit.target_type == "admin_setting"
    assert audit.target_id == "image_moderation_enabled"
    assert audit.reason == "开启图片审核"
    assert "true" in audit.metadata_json
    assert "secret" not in audit.metadata_json.lower()


def test_admin_moderation_setting_requires_reason(admin_panel_client):
    response = admin_panel_client.patch(
        "/api/admin/settings/moderation",
        json={"image_moderation_enabled": True},
    )

    assert response.status_code == 422
