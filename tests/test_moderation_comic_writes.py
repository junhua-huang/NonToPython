from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import comic
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


class FailingWriteDB:
    def execute(self, *args, **kwargs):
        raise AssertionError("comic write must not run after moderation rejection")

    def flush(self):
        raise AssertionError("comic flush must not run after moderation rejection")

    def commit(self):
        raise AssertionError("comic commit must not run after moderation rejection")

    def add(self, item):
        raise AssertionError("comic add must not run after moderation rejection")


class QueryResult:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class EventOwnerDB(FailingWriteDB):
    def __init__(self, owner_id=1):
        self.owner_id = owner_id
        self.checked_owner = False

    def execute(self, *args, **kwargs):
        if not self.checked_owner:
            self.checked_owner = True
            return QueryResult((self.owner_id,))
        return super().execute(*args, **kwargs)


class ExistingEventDB(FailingWriteDB):
    def query(self, model):
        return ExistingEventQuery()


class ExistingEventQuery:
    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return SimpleNamespace(id=5, comment_count=0)


@pytest.mark.parametrize(
    "route_call,db,payload,expected_fields,target_type",
    [
        (
            lambda payload, user, db: comic.create_event(payload=payload, user=user, db=db),
            FailingWriteDB(),
            {
                "name": "blocked",
                "venue": "venue",
                "ticket_info": "ticket",
                "website": "https://fiction.invalid",
                "intro": "intro",
                "city_id": 1,
                "start_date": "2026-08-01",
                "end_date": "2026-08-02",
            },
            {"name": "blocked", "venue": "venue", "ticket_info": "ticket", "website": "https://fiction.invalid", "intro": "intro"},
            "comic_event",
        ),
        (
            lambda payload, user, db: comic.create_event(payload=payload, user=user, db=db),
            FailingWriteDB(),
            {"name": "ok", "ticket_info": "   ", "ticketInfo": "blocked", "city_id": 1},
            {"name": "ok", "ticket_info": "blocked"},
            "comic_event",
        ),
        (
            lambda payload, user, db: comic.update_event(5, payload=payload, user=user, db=db),
            EventOwnerDB(owner_id=1),
            {"intro": "blocked"},
            {"intro": "blocked"},
            "comic_event",
        ),
        (
            lambda payload, user, db: comic.post_comic_comment(5, payload=payload, current_user=user, db=db),
            ExistingEventDB(),
            {"content": "blocked"},
            {"content": "blocked"},
            "comic_comment",
        ),
    ],
)
def test_comic_text_writes_use_targeted_moderation_before_mutation(
    monkeypatch,
    route_call,
    db,
    payload,
    expected_fields,
    target_type,
):
    moderation_spy = RejectingModerationService()
    monkeypatch.setattr(comic, "moderation_service", moderation_spy)

    with pytest.raises(HTTPException) as exc_info:
        route_call(payload, SimpleNamespace(id=1), db)

    assert exc_info.value.status_code == 422
    assert len(moderation_spy.calls) == 1
    fields, context = moderation_spy.calls[0]
    assert fields == expected_fields
    assert context.target_type == target_type
    assert context.actor_user_id == 1
    assert context.is_public is True


def test_comic_event_ticket_info_alias_conflict_fails_closed_before_mutation(monkeypatch):
    moderation_spy = ApprovingModerationService()
    monkeypatch.setattr(comic, "moderation_service", moderation_spy)

    with pytest.raises(HTTPException) as exc_info:
        comic.create_event(
            payload={"name": "ok", "ticketInfo": "safe", "ticket_info": "blocked", "city_id": 1},
            user=SimpleNamespace(id=1),
            db=FailingWriteDB(),
        )

    assert exc_info.value.status_code == 503
    assert moderation_spy.calls == []


@pytest.mark.parametrize(
    "route_call,db,payload",
    [
        (
            lambda payload, user, db: comic.create_event(payload=payload, user=user, db=db),
            FailingWriteDB(),
            {"name": {"bad": "type"}, "city_id": 1},
        ),
        (
            lambda payload, user, db: comic.create_event(payload=payload, user=user, db=db),
            FailingWriteDB(),
            {"name": "ok", "ticketInfo": "safe", "ticket_info": "blocked", "city_id": 1},
        ),
        (
            lambda payload, user, db: comic.create_event(payload=payload, user=user, db=db),
            FailingWriteDB(),
            {"name": "ok", "ticketInfo": "safe", "ticket_info": ["bad"], "city_id": 1},
        ),
        (
            lambda payload, user, db: comic.update_event(5, payload=payload, user=user, db=db),
            EventOwnerDB(owner_id=1),
            {"intro": ["bad"]},
        ),
        (
            lambda payload, user, db: comic.post_comic_comment(5, payload=payload, current_user=user, db=db),
            ExistingEventDB(),
            {"content": {"bad": "type"}},
        ),
    ],
)
def test_comic_text_writes_fail_closed_on_invalid_or_ambiguous_moderated_fields(
    route_call,
    db,
    payload,
):
    with pytest.raises(HTTPException) as exc_info:
        route_call(payload, SimpleNamespace(id=1), db)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "code": "MODERATION_UNAVAILABLE",
        "message": "内容审核服务暂不可用，请稍后重试",
        "retryable": True,
    }
