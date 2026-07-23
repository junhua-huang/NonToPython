from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import reports, topics
from app.services.moderation_errors import ContentRejected


MODERATION_REJECTION_ROUTES = {
    "POST /api/topics",
    "PUT /api/topics/{topic_id}",
    "POST /api/reports",
    "POST /api/reports/post",
    "POST /api/reports/comment",
    "POST /api/reports/user",
}


class RejectingModerationService:
    def __init__(self):
        self.calls = []

    def moderate_fields(self, fields, context):
        self.calls.append((dict(fields), context))
        raise ContentRejected()


class ExistingTopicDB:
    def __init__(self):
        self.topic = SimpleNamespace(
            id=3,
            description="old",
            icon_url="old-icon",
            color="#000000",
            to_dict=lambda: {},
        )
        self.committed = False

    def query(self, *args, **kwargs):
        return ExistingTopicQuery(self.topic)

    def commit(self):
        self.committed = True
        raise AssertionError("topic commit must not run after moderation rejection")

    def rollback(self):
        pass


class ExistingTopicQuery:
    def __init__(self, topic):
        self.topic = topic

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.topic


class FailingReportDB:
    def query(self, *args, **kwargs):
        raise AssertionError("report lookup must not run after moderation rejection")

    def add(self, *args, **kwargs):
        raise AssertionError("report add must not run after moderation rejection")

    def commit(self):
        raise AssertionError("report commit must not run after moderation rejection")


@pytest.mark.parametrize(
    "route_call,expected_fields,target_type,is_public",
    [
        pytest.param(
            lambda: topics.create_topic(
                payload={"name": "blocked", "description": "d", "icon_url": "https://cdn.invalid/icon.png"},
                user=SimpleNamespace(id=1),
                db=object(),
            ),
            {"name": "blocked", "description": "d"},
            "topic",
            True,
            id="POST /api/topics",
        ),
        pytest.param(
            lambda: topics.update_topic(
                3,
                payload={"description": "blocked", "icon_url": "https://cdn.invalid/icon.png"},
                user=SimpleNamespace(id=2),
                db=ExistingTopicDB(),
            ),
            {"description": "blocked"},
            "topic_edit",
            True,
            id="PUT /api/topics/{topic_id}",
        ),
    ],
)
def test_topic_text_rejects_before_mutation(
    monkeypatch,
    route_call,
    expected_fields,
    target_type,
    is_public,
):
    moderation_spy = RejectingModerationService()
    monkeypatch.setattr(topics, "moderation_service", moderation_spy)
    monkeypatch.setattr(topics.TopicService, "get_topic_by_name", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("duplicate topic lookup must not run after moderation rejection")
    ))
    monkeypatch.setattr(topics.TopicService, "create_topic", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("topic creation must not run after moderation rejection")
    ))

    with pytest.raises(HTTPException) as exc_info:
        route_call()

    assert exc_info.value.status_code == 422
    fields, context = moderation_spy.calls[0]
    assert fields == expected_fields
    assert context.target_type == target_type
    assert context.is_public is is_public


def test_topic_moderation_unknown_route_fails_closed():
    with pytest.raises(HTTPException) as exc_info:
        topics._moderate_topic_fields(
            "PUT /api/topics/{drifted_topic_id}",
            {"description": "blocked"},
            actor_user_id=2,
            is_public=True,
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "code": "MODERATION_UNAVAILABLE",
        "message": "内容审核服务暂不可用，请稍后重试",
        "retryable": True,
    }


def test_topic_edit_rejection_leaves_existing_topic_fields_unchanged(monkeypatch):
    moderation_spy = RejectingModerationService()
    db = ExistingTopicDB()
    monkeypatch.setattr(topics, "moderation_service", moderation_spy)

    with pytest.raises(HTTPException) as exc_info:
        topics.update_topic(
            3,
            payload={
                "description": "blocked",
                "icon_url": "https://cdn.invalid/new.png",
                "color": "#ffffff",
            },
            user=SimpleNamespace(id=2),
            db=db,
        )

    assert exc_info.value.status_code == 422
    assert db.topic.description == "old"
    assert db.topic.icon_url == "old-icon"
    assert db.topic.color == "#000000"
    assert db.committed is False


@pytest.mark.parametrize(
    "route_call,actor_id",
    [
        pytest.param(
            lambda db: reports.submit_report(
                payload={"type": "post", "target_id": 1, "reason": "blocked"},
                user=SimpleNamespace(id=10),
                db=db,
            ),
            10,
            id="POST /api/reports",
        ),
        pytest.param(
            lambda db: reports.report_post(
                payload={"post_id": 1, "reason": "blocked"},
                user=SimpleNamespace(id=11),
                db=db,
            ),
            11,
            id="POST /api/reports/post",
        ),
        pytest.param(
            lambda db: reports.report_comment(
                payload={"comment_id": 1, "reason": "blocked"},
                user=SimpleNamespace(id=12),
                db=db,
            ),
            12,
            id="POST /api/reports/comment",
        ),
        pytest.param(
            lambda db: reports.report_user(
                payload={"user_id": 2, "reason": "blocked"},
                user=SimpleNamespace(id=13),
                db=db,
            ),
            13,
            id="POST /api/reports/user",
        ),
    ],
)
def test_report_reason_rejects_before_lookup_or_write(monkeypatch, route_call, actor_id):
    moderation_spy = RejectingModerationService()
    monkeypatch.setattr(reports, "moderation_service", moderation_spy)

    with pytest.raises(HTTPException) as exc_info:
        route_call(FailingReportDB())

    assert exc_info.value.status_code == 422
    fields, context = moderation_spy.calls[0]
    assert fields == {"reason": "blocked"}
    assert context.target_type == "report_reason"
    assert context.actor_user_id == actor_id
    assert context.is_public is False
