from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import communities
from app.services.moderation_errors import ContentRejected


class RejectingModerationService:
    def __init__(self):
        self.calls = []

    def moderate_fields(self, fields, context):
        self.calls.append((dict(fields), context))
        raise ContentRejected()


def _fail_mutation(*args, **kwargs):
    raise AssertionError("community mutation must not run after moderation rejection")


@pytest.mark.parametrize(
    "route_call,payload,expected_fields,target_type,is_public,service_method",
    [
        (
            lambda payload, user, db: communities.create_community(payload=payload, user=user, db=db),
            {"name": "blocked", "description": "d", "rules": "r", "avatar_url": "https://cdn.invalid/a.png"},
            {"name": "blocked", "description": "d", "rules": "r"},
            "community_create",
            True,
            "create_community",
        ),
        (
            lambda payload, user, db: communities.update_community(3, payload=payload, user=user, db=db),
            {"description": "blocked", "avatar_url": "https://cdn.invalid/a.png"},
            {"description": "blocked"},
            "community_edit",
            True,
            "update_community",
        ),
        (
            lambda payload, user, db: communities.join_community(3, payload=payload, user=user, db=db),
            {"message": "blocked"},
            {"message": "blocked"},
            "community_join_request",
            False,
            "request_join",
        ),
        (
            lambda payload, user, db: communities.create_announcement(3, payload=payload, user=user, db=db),
            {"title": "blocked", "content": "body", "is_pinned": True},
            {"title": "blocked", "content": "body"},
            "community_announcement",
            True,
            "create_announcement",
        ),
        (
            lambda payload, user, db: communities.update_announcement(3, 4, payload=payload, user=user, db=db),
            {"content": "blocked", "is_pinned": False},
            {"content": "blocked"},
            "community_announcement",
            True,
            "update_announcement",
        ),
        (
            lambda payload, user, db: communities.ban_user(3, payload=payload, user=user, db=db),
            {"user_id": 22, "reason": "blocked"},
            {"reason": "blocked"},
            "community_ban",
            False,
            "ban_user",
        ),
    ],
)
def test_community_text_writes_use_targeted_moderation_before_mutation(
    monkeypatch,
    route_call,
    payload,
    expected_fields,
    target_type,
    is_public,
    service_method,
):
    moderation_spy = RejectingModerationService()
    monkeypatch.setattr(communities, "moderation_service", moderation_spy)
    monkeypatch.setattr(communities.CommunityService, service_method, _fail_mutation)

    with pytest.raises(HTTPException) as exc_info:
        route_call(payload, SimpleNamespace(id=1), object())

    assert exc_info.value.status_code == 422
    assert len(moderation_spy.calls) == 1
    fields, context = moderation_spy.calls[0]
    assert fields == expected_fields
    assert context.target_type == target_type
    assert context.actor_user_id == 1
    assert context.is_public is is_public


@pytest.mark.parametrize(
    "route_call,payload,service_method",
    [
        (
            lambda payload, user, db: communities.create_community(payload=payload, user=user, db=db),
            {"name": {"bad": "type"}, "description": "ok", "rules": "ok"},
            "create_community",
        ),
        (
            lambda payload, user, db: communities.update_community(3, payload=payload, user=user, db=db),
            {"description": ["bad"]},
            "update_community",
        ),
        (
            lambda payload, user, db: communities.join_community(3, payload=payload, user=user, db=db),
            {"message": 123},
            "request_join",
        ),
        (
            lambda payload, user, db: communities.create_announcement(3, payload=payload, user=user, db=db),
            {"title": "ok", "content": {"bad": "type"}},
            "create_announcement",
        ),
        (
            lambda payload, user, db: communities.ban_user(3, payload=payload, user=user, db=db),
            {"user_id": 22, "reason": ["bad"]},
            "ban_user",
        ),
    ],
)
def test_community_text_writes_fail_closed_on_non_string_moderated_fields(
    monkeypatch,
    route_call,
    payload,
    service_method,
):
    monkeypatch.setattr(communities.CommunityService, service_method, _fail_mutation)

    with pytest.raises(HTTPException) as exc_info:
        route_call(payload, SimpleNamespace(id=1), object())

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "code": "MODERATION_UNAVAILABLE",
        "message": "内容审核服务暂不可用，请稍后重试",
        "retryable": True,
    }
