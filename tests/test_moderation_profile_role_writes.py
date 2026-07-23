from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import admin, auth, roles
from app.services.moderation_errors import ContentRejected


class RejectingModerationService:
    def __init__(self):
        self.calls = []

    def moderate_fields(self, fields, context):
        self.calls.append((dict(fields), context))
        raise ContentRejected()


class ApprovingModerationService:
    def __init__(self):
        self.calls = []

    def moderate_fields(self, fields, context):
        self.calls.append((dict(fields), context))
        return ()


class FailingDB:
    def query(self, *args, **kwargs):
        raise AssertionError("DB query must not run after moderation rejection")

    def add(self, *args, **kwargs):
        raise AssertionError("DB add must not run after moderation rejection")

    def commit(self):
        raise AssertionError("DB commit must not run after moderation rejection")

    def refresh(self, *args, **kwargs):
        raise AssertionError("DB refresh must not run after moderation rejection")


class SequencedDB:
    def __init__(self, *rows):
        self.rows = list(rows)
        self.committed = False
        self.rolled_back = False

    def query(self, *args, **kwargs):
        return SequencedQuery(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class SequencedQuery:
    def __init__(self, db):
        self.db = db

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.db.rows.pop(0) if self.db.rows else None


class FakeBackgroundTasks:
    def add_task(self, *args, **kwargs):
        raise AssertionError("background email must not be scheduled after moderation rejection")


def _raise_content_rejected(service, route_key, payload, *, actor_user_id, is_public):
    raise HTTPException(
        status_code=422,
        detail={"code": "CONTENT_REJECTED", "message": "内容未通过审核", "retryable": False},
    )


def test_registration_uses_user_registration_target_before_otp_and_user_creation(monkeypatch):
    moderation_spy = RejectingModerationService()
    monkeypatch.setattr(auth, "moderation_service", moderation_spy)
    monkeypatch.setattr(auth.OtpService, "verify", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("OTP must not be consumed after moderation rejection")
    ))

    with pytest.raises(HTTPException) as exc_info:
        auth.register(
            auth.RegisterRequest(
                username="blocked_user",
                email="new@example.invalid",
                password="Password9",
                email_code="123456",
            ),
            db=FailingDB(),
        )

    assert exc_info.value.status_code == 422
    assert len(moderation_spy.calls) == 1
    fields, context = moderation_spy.calls[0]
    assert fields == {"username": "blocked_user"}
    assert context.target_type == "user_registration"
    assert context.actor_user_id is None
    assert context.is_public is True


def test_profile_update_uses_user_profile_target_before_db_query(monkeypatch):
    moderation_spy = RejectingModerationService()
    monkeypatch.setattr(auth, "moderation_service", moderation_spy)

    with pytest.raises(HTTPException) as exc_info:
        auth.update_profile(
            auth.ProfileUpdateRequest(display_name="blocked", bio="bio"),
            user=SimpleNamespace(id=7),
            db=FailingDB(),
        )

    assert exc_info.value.status_code == 422
    fields, context = moderation_spy.calls[0]
    assert fields == {"display_name": "blocked", "bio": "bio"}
    assert context.target_type == "user_profile"
    assert context.actor_user_id == 7
    assert context.is_public is True


def test_profile_update_applies_display_name_after_moderation(monkeypatch):
    moderation_spy = ApprovingModerationService()
    monkeypatch.setattr(auth, "moderation_service", moderation_spy)
    current = SimpleNamespace(
        id=7,
        username="old_name",
        email="me@example.invalid",
        bio="old bio",
        avatar_url="",
        cover_photo_url="",
        show_email=False,
        created_at=None,
    )
    db = SequencedDB(current, None)

    response = auth.update_profile(
        auth.ProfileUpdateRequest(display_name="New.Name", bio="<b>bio</b>"),
        user=SimpleNamespace(id=7),
        db=db,
    )

    assert current.username == "New.Name"
    assert current.bio == "&lt;b&gt;bio&lt;/b&gt;"
    assert db.committed is True
    assert response["user"]["display_name"] == "New.Name"
    fields, context = moderation_spy.calls[0]
    assert fields == {"display_name": "New.Name", "bio": "<b>bio</b>"}
    assert context.target_type == "user_profile"


@pytest.mark.parametrize(
    "route_call,route_key,expected_payload,actor_id,is_public",
    [
        (
            lambda: roles.apply_role(
                roles.RoleApplyRequest(
                    role_name="coser",
                    reason="blocked",
                    application_text="a",
                    contact_info="c",
                    extra_note="n",
                    portfolio_links=["https://fiction.invalid/work"],
                ),
                background_tasks=FakeBackgroundTasks(),
                user=SimpleNamespace(id=11, username="user"),
                db=FailingDB(),
            ),
            "POST /api/roles/apply",
            {"reason", "application_text", "contact_info", "extra_note", "portfolio_links[0]"},
            11,
            False,
        ),
        (
            lambda: roles.update_coser_profile(
                roles.CoserProfileUpdate(cosname="blocked", bio="b", styles="s", city="c", social_links="l"),
                user=SimpleNamespace(id=12),
                db=FailingDB(),
            ),
            "PUT /api/roles/profiles/coser",
            {"cosname", "bio", "styles", "city", "social_links"},
            12,
            True,
        ),
        (
            lambda: roles.update_photographer_profile(
                roles.PhotographerProfileUpdate(equipment="blocked", styles="s", city="c", social_links="l"),
                user=SimpleNamespace(id=13),
                db=FailingDB(),
            ),
            "PUT /api/roles/profiles/photographer",
            {"equipment", "styles", "city", "social_links"},
            13,
            True,
        ),
        (
            lambda: roles.update_service_profile(
                roles.ServiceProfileUpdate(
                    service_type="makeup_artist",
                    description="blocked",
                    city="c",
                    price_info="p",
                ),
                user=SimpleNamespace(id=14),
                db=SequencedDB(SimpleNamespace(id=1)),
            ),
            "PUT /api/roles/profiles/service",
            {"service_type", "description", "city", "price_info"},
            14,
            True,
        ),
    ],
)
def test_role_application_and_profiles_moderate_before_write(
    monkeypatch,
    route_call,
    route_key,
    expected_payload,
    actor_id,
    is_public,
):
    moderation_spy = RejectingModerationService()
    monkeypatch.setattr(roles, "moderation_service", moderation_spy)

    with pytest.raises(HTTPException) as exc_info:
        route_call()

    assert exc_info.value.status_code == 422
    fields, context = moderation_spy.calls[0]
    assert set(fields) == expected_payload
    assert context.target_type == route_key
    assert context.actor_user_id == actor_id
    assert context.is_public is is_public


@pytest.mark.parametrize(
    "route_call,route_key",
    [
        (
            lambda db, app: roles.approve_application(
                5,
                data=roles.RoleReviewRequest(review_comment="blocked"),
                user=SimpleNamespace(id=21, username="admin"),
                db=db,
            ),
            "POST /api/roles/applications/{application_id}/approve",
        ),
        (
            lambda db, app: roles.reject_application(
                5,
                data=roles.RoleReviewRequest(review_comment="blocked"),
                user=SimpleNamespace(id=21, username="admin"),
                db=db,
            ),
            "POST /api/roles/applications/{application_id}/reject",
        ),
        (
            lambda db, app: roles.suspend_application(
                5,
                data=roles.RoleReviewRequest(review_comment="blocked"),
                user=SimpleNamespace(id=21, username="admin"),
                db=db,
            ),
            "POST /api/roles/applications/{application_id}/suspend",
        ),
    ],
)
def test_role_reviews_moderate_review_comment_before_status_mutation(monkeypatch, route_call, route_key):
    app = SimpleNamespace(id=5, status="pending", user_id=30, role_id=40)
    db = SequencedDB(app)
    calls = []

    def fake_moderate(service, actual_route_key, payload, *, actor_user_id, is_public):
        calls.append((actual_route_key, payload, actor_user_id, is_public))
        raise HTTPException(status_code=422, detail={"code": "CONTENT_REJECTED"})

    monkeypatch.setattr(roles, "moderate_route_fields", fake_moderate)

    with pytest.raises(HTTPException) as exc_info:
        route_call(db, app)

    assert exc_info.value.status_code == 422
    assert app.status == "pending"
    assert db.committed is False
    assert calls == [(route_key, {"review_comment": "blocked"}, 21, False)]


@pytest.mark.parametrize(
    "route_call,route_key",
    [
        (
            lambda db, app: admin.admin_approve_application(
                5,
                payload={"review_comment": "blocked"},
                admin=SimpleNamespace(id=31, username="admin"),
                db=db,
            ),
            "POST /role-applications/{application_id}/approve",
        ),
        (
            lambda db, app: admin.admin_reject_application(
                5,
                payload={"review_comment": "blocked"},
                admin=SimpleNamespace(id=31, username="admin"),
                db=db,
            ),
            "POST /role-applications/{application_id}/reject",
        ),
        (
            lambda db, app: admin.admin_suspend_application(
                5,
                payload={"review_comment": "blocked"},
                admin=SimpleNamespace(id=31, username="admin"),
                db=db,
            ),
            "POST /role-applications/{application_id}/suspend",
        ),
    ],
)
def test_legacy_admin_role_reviews_moderate_before_status_mutation(monkeypatch, route_call, route_key):
    app = SimpleNamespace(id=5, status="pending", user_id=30, role_id=40)
    db = SequencedDB(app)
    calls = []

    def fake_moderate(service, actual_route_key, payload, *, actor_user_id, is_public):
        calls.append((actual_route_key, payload, actor_user_id, is_public))
        raise HTTPException(status_code=422, detail={"code": "CONTENT_REJECTED"})

    monkeypatch.setattr(admin, "moderate_route_fields", fake_moderate)

    with pytest.raises(HTTPException) as exc_info:
        route_call(db, app)

    assert exc_info.value.status_code == 422
    assert app.status == "pending"
    assert db.committed is False
    assert calls == [(route_key, {"review_comment": "blocked"}, 31, False)]
