import pytest
from fastapi import HTTPException

from app.services.moderation_errors import ContentRejected, ModerationUnavailable
from app.services.moderation_route_helpers import (
    message_text_payload_for_moderation,
    moderate_route_fields,
)
from app.services.moderation_types import ModerationContext


class Recorder:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def moderate_fields(self, fields, context):
        self.calls.append((fields, context))
        if self.error:
            raise self.error
        return ()


def test_moderate_route_fields_extracts_configured_values_without_media_or_passwords():
    service = Recorder()
    payload = {
        "content": "hello",
        "image_urls": ["https://private.example/image.png"],
        "password": "secret",
        "nested": {"content": "ignored"},
    }

    moderate_route_fields(
        service,
        "POST /api/posts",
        payload,
        actor_user_id=7,
        is_public=True,
    )

    assert service.calls == [
        (
            {"content": "hello"},
            ModerationContext(
                target_type="POST /api/posts",
                actor_user_id=7,
                is_public=True,
            ),
        )
    ]


def test_moderate_route_fields_supports_list_field_suffix_for_text_lists():
    service = Recorder()
    payload = {
        "reason": "apply",
        "portfolio_links": ["about me", None, "   ", "https://portfolio.example"],
        "proof_images": ["https://private.example/proof.png"],
    }

    moderate_route_fields(
        service,
        "POST /api/roles/apply",
        payload,
        actor_user_id=11,
        is_public=False,
    )

    fields, context = service.calls[0]
    assert fields == {
        "reason": "apply",
        "portfolio_links[0]": "about me",
        "portfolio_links[3]": "https://portfolio.example",
    }
    assert context.actor_user_id == 11
    assert context.is_public is False


def test_moderate_route_fields_ignores_missing_empty_and_unknown_inventory():
    service = Recorder()

    moderate_route_fields(
        service,
        "POST /api/posts",
        {"content": "   "},
        actor_user_id=None,
        is_public=True,
    )
    moderate_route_fields(
        service,
        "GET /api/posts",
        {"content": "not moderated"},
        actor_user_id=None,
        is_public=True,
    )

    assert service.calls == []


def test_message_text_payload_for_moderation_skips_media_url_fallbacks():
    assert message_text_payload_for_moderation(
        {"message_type": "image", "content": "https://files.example/private.png"}
    ) == {}
    assert message_text_payload_for_moderation(
        {"message_type": "video", "content": "https://files.example/private.mp4"}
    ) == {}
    assert message_text_payload_for_moderation(
        {"message_type": "post", "content": "https://files.example/private-card"}
    ) == {}


def test_message_text_payload_for_moderation_keeps_text_and_media_captions():
    assert message_text_payload_for_moderation(
        {"message_type": "text", "content": " hello "}
    ) == {"content": "hello"}
    assert message_text_payload_for_moderation(
        {
            "message_type": "image",
            "content": "caption text",
            "media_url": "https://files.example/private.png",
        }
    ) == {"content": "caption text"}


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (ContentRejected(), 422, "CONTENT_REJECTED"),
        (ModerationUnavailable(RuntimeError("PRIVATE_BODY_8392")), 503, "MODERATION_UNAVAILABLE"),
    ],
)
def test_moderate_route_fields_maps_contract_errors_to_safe_http(error, status_code, code):
    service = Recorder(error=error)

    with pytest.raises(HTTPException) as exc_info:
        moderate_route_fields(
            service,
            "POST /api/posts",
            {"content": "blocked"},
            actor_user_id=1,
            is_public=True,
        )

    assert exc_info.value.status_code == status_code
    assert exc_info.value.detail["code"] == code
    assert "PRIVATE_BODY_8392" not in str(exc_info.value.detail)


def test_moderate_route_fields_fails_closed_for_bad_payload_shape():
    service = Recorder()

    with pytest.raises(HTTPException) as exc_info:
        moderate_route_fields(
            service,
            "POST /api/posts",
            ["content"],
            actor_user_id=1,
            is_public=True,
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "MODERATION_UNAVAILABLE"
    assert service.calls == []


def test_all_inventory_write_route_keys_are_wired_in_route_modules():
    import inspect

    from app.routers import admin, auth, chat, comic, communities, interactions, posts, reports, roles, topics, ws

    modules_by_route_key = {
        "POST /api/auth/register": auth,
        "PUT /api/auth/profile": auth,
        "POST /api/posts": posts,
        "PUT /api/posts/{post_id}": posts,
        "POST /api/posts/{post_id}/comments": interactions,
        "PUT /api/comments/{comment_id}": interactions,
        "POST /api/chat/conversations/{conversation_id}/messages": chat,
        "WS send_message": ws,
        "POST /api/communities": communities,
        "PATCH /api/communities/{community_id}": communities,
        "POST /api/communities/{community_id}/join": communities,
        "POST /api/communities/{community_id}/announcements": communities,
        "PATCH /api/communities/{community_id}/announcements/{announcement_id}": communities,
        "POST /api/communities/{community_id}/bans": communities,
        "POST /api/communities/{community_id}/chat/messages": communities,
        "POST /api/comic/events": comic,
        "PUT /api/comic/events/{event_id}": comic,
        "POST /api/comic/events/{event_id}/comments": comic,
        "POST /api/roles/apply": roles,
        "PUT /api/roles/profiles/coser": roles,
        "PUT /api/roles/profiles/photographer": roles,
        "PUT /api/roles/profiles/service": roles,
        "POST /api/roles/applications/{application_id}/approve": roles,
        "POST /api/roles/applications/{application_id}/reject": roles,
        "POST /api/roles/applications/{application_id}/suspend": roles,
        "POST /role-applications/{application_id}/approve": admin,
        "POST /role-applications/{application_id}/reject": admin,
        "POST /role-applications/{application_id}/suspend": admin,
        "POST /api/topics": topics,
        "PUT /api/topics/{topic_id}": topics,
        "POST /api/reports": reports,
        "POST /api/reports/post": reports,
        "POST /api/reports/comment": reports,
        "POST /api/reports/user": reports,
    }

    missing = {
        route_key: module.__name__
        for route_key, module in modules_by_route_key.items()
        if route_key not in inspect.getsource(module)
    }

    assert missing == {}


def test_create_post_moderates_before_file_upload_and_db_mutation(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.routers import posts as posts_router

    events = []

    class FakeUpload:
        filename = "clip.mp4"

    class FakeDB:
        def add(self, item):
            events.append(("db.add", type(item).__name__))

        def flush(self):
            events.append(("db.flush",))

        def commit(self):
            events.append(("db.commit",))

        def rollback(self):
            events.append(("db.rollback",))

    def fake_moderate(service, route_key, payload, *, actor_user_id, is_public):
        events.append(("moderate", route_key, sorted(payload), actor_user_id, is_public))
        raise HTTPException(
            status_code=422,
            detail={"code": "CONTENT_REJECTED", "message": "内容未通过审核", "retryable": False},
        )

    def fake_save_file(*args, **kwargs):
        events.append(("file_upload",))
        return {"success": True, "url": "https://files.example/video.mp4"}

    monkeypatch.setattr(posts_router, "moderate_route_fields", fake_moderate, raising=False)
    monkeypatch.setattr(posts_router, "moderation_service", object(), raising=False)
    monkeypatch.setattr(posts_router.FileUploader, "save_file", fake_save_file)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
                posts_router.create_post(
                    image=None,
                    video=FakeUpload(),
                    content="sample text",
                    image_urls=None,
                    video_url_input=None,
                    content_category=None,
                    display_role_type=None,
                    visibility="public",
                    visible_user_ids=None,
                    community_id=None,
                    community_only=False,
                    user=SimpleNamespace(id=42),
                    db=FakeDB(),
                )

        )

    assert exc_info.value.status_code == 422
    assert events == [("moderate", "POST /api/posts", ["content"], 42, True)]


def test_create_community_moderates_before_service_side_effect(monkeypatch):
    from types import SimpleNamespace

    from app.routers import communities as communities_router

    events = []

    def fake_moderate(service, route_key, payload, *, actor_user_id, is_public):
        events.append(("moderate", route_key, sorted(payload), actor_user_id, is_public))
        raise HTTPException(
            status_code=422,
            detail={"code": "CONTENT_REJECTED", "message": "内容未通过审核", "retryable": False},
        )

    def fake_create_community(*args, **kwargs):
        events.append(("service.create_community",))
        raise AssertionError("community service should not run after moderation rejection")

    monkeypatch.setattr(communities_router, "moderate_route_fields", fake_moderate, raising=False)
    monkeypatch.setattr(communities_router, "moderation_service", object(), raising=False)
    monkeypatch.setattr(communities_router.CommunityService, "create_community", fake_create_community)

    with pytest.raises(HTTPException) as exc_info:
        communities_router.create_community(
            {"name": "sample", "description": "sample", "rules": "sample"},
            user=SimpleNamespace(id=17),
            db=object(),
        )

    assert exc_info.value.status_code == 422
    assert events == [
        (
            "moderate",
            "POST /api/communities",
            ["description", "name", "rules"],
            17,
            True,
        )
    ]


def test_private_chat_message_moderates_before_persist_and_fanout(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.routers import chat as chat_router

    events = []
    conversation = SimpleNamespace(id=5, type="single", community_id=None, last_message_at=None)

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return conversation

        def count(self):
            return 0

    class FakeDB:
        def query(self, *args, **kwargs):
            return FakeQuery()

        def add(self, item):
            events.append(("db.add", type(item).__name__))

        def commit(self):
            events.append(("db.commit",))

        def refresh(self, item):
            events.append(("db.refresh",))

        def rollback(self):
            events.append(("db.rollback",))

    def fake_moderate(service, route_key, payload, *, actor_user_id, is_public):
        events.append(("moderate", route_key, sorted(payload), actor_user_id, is_public))
        raise HTTPException(
            status_code=422,
            detail={"code": "CONTENT_REJECTED", "message": "内容未通过审核", "retryable": False},
        )

    monkeypatch.setattr(chat_router, "moderate_route_fields", fake_moderate, raising=False)
    monkeypatch.setattr(chat_router, "moderation_service", object(), raising=False)
    monkeypatch.setattr(chat_router, "can_access_conversation", lambda *args, **kwargs: True)
    monkeypatch.setattr(chat_router, "get_active_conversation_participant_ids", lambda *args, **kwargs: [1, 2])
    monkeypatch.setattr(
        chat_router,
        "normalize_user_message_payload",
        lambda payload, *args, **kwargs: {
            "content": payload.get("content", ""),
            "message_type": payload.get("message_type", "text"),
            "media_url": payload.get("media_url"),
            "related_id": payload.get("related_id"),
        },
    )
    monkeypatch.setattr(chat_router, "validate_quote", lambda *args, **kwargs: None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            chat_router.send_message(
                5,
                {"content": "sample text", "media_url": "https://files.example/private.png"},
                user=SimpleNamespace(id=1),
                db=FakeDB(),
            )
        )

    assert exc_info.value.status_code == 422
    assert events == [
        (
            "moderate",
            "POST /api/chat/conversations/{conversation_id}/messages",
            ["content"],
            1,
            False,
        )
    ]


def test_private_chat_media_url_fallback_is_not_moderated(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.routers import chat as chat_router

    events = []
    conversation = SimpleNamespace(id=5, type="single", community_id=None, last_message_at=None)

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return conversation

    class FakeDB:
        def query(self, *args, **kwargs):
            return FakeQuery()

        def add(self, item):
            events.append(("db.add", type(item).__name__))
            raise RuntimeError("stop after add")

        def rollback(self):
            events.append(("db.rollback",))

    def fail_if_moderated(*args, **kwargs):
        raise AssertionError("media URL fallback must not be moderated")

    monkeypatch.setattr(chat_router, "moderate_route_fields", fail_if_moderated, raising=False)
    monkeypatch.setattr(chat_router, "moderation_service", object(), raising=False)
    monkeypatch.setattr(chat_router, "can_access_conversation", lambda *args, **kwargs: True)
    monkeypatch.setattr(chat_router, "get_active_conversation_participant_ids", lambda *args, **kwargs: [1, 2])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            chat_router.send_message(
                5,
                {"message_type": "image", "content": "https://files.example/private.png"},
                user=SimpleNamespace(id=1),
                db=FakeDB(),
            )
        )

    assert exc_info.value.status_code == 500
    assert events == [("db.add", "Message"), ("db.rollback",)]
