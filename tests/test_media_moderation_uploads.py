import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.moderation_errors import ContentRejected, ModerationUnavailable, to_http_exception


IMAGE_KEY = "posts/42/20260724/image.jpg"
IMAGE_URL = "https://test-bucket-1250000000.cos.ap-guangzhou.myqcloud.com/posts/42/20260724/image.jpg"
AVATAR_KEY = "avatars/42/20260724/avatar.jpg"
COVER_KEY = "covers/42/20260724/cover.jpg"
CHAT_KEY = "chat/42/20260724/private.jpg"


class RejectingMediaService:
    def moderate(self, target):
        raise ContentRejected()


class UnavailableMediaService:
    def moderate(self, target):
        raise ModerationUnavailable(RuntimeError("provider leaked raw cos failure"))


class ApprovingMediaService:
    calls = []

    def moderate(self, target):
        self.calls.append(target)
        return SimpleNamespace(decision="approve")


def test_upload_confirm_rejects_image_before_confirm_and_cleans_up(monkeypatch):
    from app.routers import upload as upload_router

    events = []

    def fake_confirm(*args, **kwargs):
        events.append("confirm")
        raise AssertionError("confirm_upload must not run for rejected image")

    def fake_delete(url):
        events.append(("delete", url))
        return {"success": True}

    monkeypatch.setattr(upload_router.FileUploader, "confirm_upload", fake_confirm)
    monkeypatch.setattr(upload_router.FileUploader, "delete_file", fake_delete)
    monkeypatch.setattr(
        "app.services.media_moderation_route_helpers.media_moderation_service",
        RejectingMediaService(),
    )

    with pytest.raises(HTTPException) as excinfo:
        upload_router.confirm_upload(
            {"cos_key": IMAGE_KEY, "final_filename": "final.jpg"},
            user=SimpleNamespace(id=42),
            db=None,
        )

    assert excinfo.value.status_code == 422
    assert excinfo.value.detail == {
        "code": "CONTENT_REJECTED",
        "message": "内容未通过审核",
        "retryable": False,
    }
    assert len(events) == 1
    assert events[0][0] == "delete"
    assert events[0][1].endswith(f"/{IMAGE_KEY}")


def test_upload_confirm_unavailable_is_retryable_safe_and_keeps_object(monkeypatch):
    from app.routers import upload as upload_router

    events = []
    monkeypatch.setattr(upload_router.FileUploader, "confirm_upload", lambda *args, **kwargs: events.append("confirm"))
    monkeypatch.setattr(upload_router.FileUploader, "delete_file", lambda url: events.append(("delete", url)))
    monkeypatch.setattr(
        "app.services.media_moderation_route_helpers.media_moderation_service",
        UnavailableMediaService(),
    )

    with pytest.raises(HTTPException) as excinfo:
        upload_router.confirm_upload(
            {"cos_key": IMAGE_KEY, "final_filename": "final.jpg"},
            user=SimpleNamespace(id=42),
            db=None,
        )

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == {
        "code": "MODERATION_UNAVAILABLE",
        "message": "内容审核服务暂不可用，请稍后重试",
        "retryable": True,
    }
    assert events == []
    assert IMAGE_KEY not in str(excinfo.value.detail)
    assert "provider leaked" not in str(excinfo.value.detail)


def test_upload_confirm_skips_chat_path(monkeypatch):
    from app.routers import upload as upload_router

    def fail_if_called(target):
        raise AssertionError("chat images must not be sent to Tencent image audit by default")

    monkeypatch.setattr(
        "app.services.media_moderation_route_helpers.media_moderation_service",
        SimpleNamespace(moderate=fail_if_called),
    )
    monkeypatch.setattr(
        upload_router.FileUploader,
        "confirm_upload",
        lambda cos_key, final_filename: {
            "success": True,
            "final_url": "https://example.com/final.jpg",
            "final_cos_key": "chat/42/final.jpg",
        },
    )

    result = upload_router.confirm_upload(
        {"cos_key": CHAT_KEY, "final_filename": "final.jpg"},
        user=SimpleNamespace(id=42),
        db=None,
    )

    assert result["url"] == "https://example.com/final.jpg"


def test_upload_confirm_requires_owned_key_before_moderation_or_confirm(monkeypatch):
    from app.routers import upload as upload_router

    events = []
    monkeypatch.setattr(
        "app.services.media_moderation_route_helpers.media_moderation_service",
        SimpleNamespace(moderate=lambda target: events.append("moderate")),
    )
    monkeypatch.setattr(
        upload_router.FileUploader,
        "confirm_upload",
        lambda *args, **kwargs: events.append("confirm"),
    )

    with pytest.raises(HTTPException) as excinfo:
        upload_router.confirm_upload(
            {"cos_key": "posts/7/20260724/image.jpg", "final_filename": "final.jpg"},
            user=SimpleNamespace(id=42),
            db=None,
        )

    assert excinfo.value.status_code == 403
    assert events == []


def test_upload_confirm_rejects_path_traversal_final_filename(monkeypatch):
    from app.routers import upload as upload_router

    events = []
    monkeypatch.setattr(
        "app.services.media_moderation_route_helpers.media_moderation_service",
        SimpleNamespace(moderate=lambda target: events.append("moderate")),
    )
    monkeypatch.setattr(
        upload_router.FileUploader,
        "confirm_upload",
        lambda *args, **kwargs: events.append("confirm"),
    )

    with pytest.raises(HTTPException) as excinfo:
        upload_router.confirm_upload(
            {"cos_key": IMAGE_KEY, "final_filename": "../final.jpg"},
            user=SimpleNamespace(id=42),
            db=None,
        )

    assert excinfo.value.status_code == 400
    assert events == []


def test_upload_confirm_audits_image_final_filename_even_when_temp_key_has_no_image_suffix(monkeypatch):
    from app.routers import upload as upload_router

    service = ApprovingMediaService()
    service.calls.clear()
    monkeypatch.setattr(
        "app.services.media_moderation_route_helpers.media_moderation_service",
        service,
    )
    monkeypatch.setattr(
        upload_router.FileUploader,
        "confirm_upload",
        lambda cos_key, final_filename: {
            "success": True,
            "final_url": "https://example.com/final.jpg",
            "final_cos_key": "posts/42/20260724/final.jpg",
        },
    )

    result = upload_router.confirm_upload(
        {"cos_key": "posts/42/20260724/upload.bin", "final_filename": "final.jpg"},
        user=SimpleNamespace(id=42),
        db=None,
    )

    assert result["url"] == "https://example.com/final.jpg"
    assert len(service.calls) == 1
    assert service.calls[0].content_type == "image/jpeg"


def test_upload_confirm_passes_db_to_moderation_service(monkeypatch):
    from app.routers import upload as upload_router

    calls = []
    fake_db = object()

    class DbAwareMediaService:
        def moderate(self, target, db=None):
            calls.append((target, db))
            return SimpleNamespace(decision="approve")

    monkeypatch.setattr(
        "app.services.media_moderation_route_helpers.media_moderation_service",
        DbAwareMediaService(),
    )
    monkeypatch.setattr(
        upload_router.FileUploader,
        "confirm_upload",
        lambda cos_key, final_filename: {
            "success": True,
            "final_url": "https://example.com/final.jpg",
            "final_cos_key": "posts/42/20260724/final.jpg",
        },
    )

    result = upload_router.confirm_upload(
        {"cos_key": IMAGE_KEY, "final_filename": "final.jpg"},
        user=SimpleNamespace(id=42),
        db=fake_db,
    )

    assert result["url"] == "https://example.com/final.jpg"
    assert calls[0][1] is fake_db


@pytest.mark.parametrize(
    ("filename", "file_type"),
    [
        ("payload.svg", "svg"),
        ("payload.svg", "image"),
        ("payload%2esvg", "image"),
        ("payload%252esvg", "image"),
        ("payload%252esvg", "mp4"),
    ],
)
def test_upload_presign_rejects_unsupported_svg_before_cos(monkeypatch, filename, file_type):
    from app.routers import upload as upload_router

    monkeypatch.setattr(
        upload_router.FileUploader,
        "generate_presigned_urls",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unsupported file types must not get presigned URLs")
        ),
    )

    with pytest.raises(HTTPException) as excinfo:
        upload_router.presign_upload(
            {"filename": filename, "file_type": file_type, "upload_type": "post"},
            user=SimpleNamespace(id=42),
        )

    assert excinfo.value.status_code == 400


def test_upload_delete_requires_owned_cos_url(monkeypatch):
    from app.routers import upload as upload_router

    events = []
    monkeypatch.setattr(
        upload_router.FileUploader,
        "cos_key_from_url",
        lambda url: "posts/7/20260724/image.jpg",
    )
    monkeypatch.setattr(
        upload_router.FileUploader,
        "delete_file",
        lambda url: events.append(("delete", url)) or {"success": True},
    )

    with pytest.raises(HTTPException) as excinfo:
        upload_router.delete_file({"url": IMAGE_URL}, user=SimpleNamespace(id=42))

    assert excinfo.value.status_code == 403
    assert events == []


def test_upload_info_requires_owned_cos_url(monkeypatch):
    from app.routers import upload as upload_router

    events = []
    monkeypatch.setattr(
        upload_router.FileUploader,
        "cos_key_from_url",
        lambda url: "posts/7/20260724/image.jpg",
    )
    monkeypatch.setattr(
        upload_router.FileUploader,
        "get_file_info",
        lambda url: events.append(("info", url)) or {"exists": True},
    )

    with pytest.raises(HTTPException) as excinfo:
        upload_router.get_file_info(url=IMAGE_URL, user=SimpleNamespace(id=42))

    assert excinfo.value.status_code == 403
    assert events == []


def test_legacy_upload_redirect_requires_owned_key():
    from app.routers import upload as upload_router

    with pytest.raises(HTTPException) as excinfo:
        upload_router.serve_upload(
            "posts/7/20260724/image.jpg",
            user=SimpleNamespace(id=42),
        )

    assert excinfo.value.status_code == 403


def test_legacy_upload_redirect_rejects_encoded_traversal():
    from app.routers import upload as upload_router

    with pytest.raises(HTTPException) as excinfo:
        upload_router.serve_upload(
            "posts/42/20260724/%2e%2e/image.jpg",
            user=SimpleNamespace(id=42),
        )

    assert excinfo.value.status_code == 400


def test_avatar_confirm_rejection_does_not_update_user(monkeypatch):
    from app.routers import upload as upload_router

    user_row = SimpleNamespace(id=42, avatar_url="old-avatar")

    class Query:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return user_row

    class FakeDB:
        def query(self, model):
            return Query()

        def commit(self):
            raise AssertionError("avatar commit must not run after moderation rejection")

    monkeypatch.setattr(upload_router.FileUploader, "cos_key_from_url", lambda url: AVATAR_KEY)
    monkeypatch.setattr(
        "app.services.media_moderation_route_helpers.media_moderation_service",
        RejectingMediaService(),
    )

    with pytest.raises(HTTPException) as excinfo:
        upload_router.confirm_avatar(
            {"url": IMAGE_URL},
            user=SimpleNamespace(id=42),
            db=FakeDB(),
        )

    assert excinfo.value.status_code == 422
    assert user_row.avatar_url == "old-avatar"


def test_cover_confirm_rejection_does_not_update_user(monkeypatch):
    from app.routers import upload as upload_router

    user_row = SimpleNamespace(id=42, cover_photo_url="old-cover")

    class Query:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return user_row

    class FakeDB:
        def query(self, model):
            return Query()

        def commit(self):
            raise AssertionError("cover commit must not run after moderation rejection")

    monkeypatch.setattr(upload_router.FileUploader, "cos_key_from_url", lambda url: COVER_KEY)
    monkeypatch.setattr(
        "app.services.media_moderation_route_helpers.media_moderation_service",
        RejectingMediaService(),
    )

    with pytest.raises(HTTPException) as excinfo:
        upload_router.confirm_cover(
            {"url": IMAGE_URL},
            user=SimpleNamespace(id=42),
            db=FakeDB(),
        )

    assert excinfo.value.status_code == 422
    assert user_row.cover_photo_url == "old-cover"


def test_avatar_confirm_rejects_unrecognized_public_url_before_db_update(monkeypatch):
    from app.routers import upload as upload_router

    class FakeDB:
        def query(self, model):
            raise AssertionError("DB must not be updated for unrecognized avatar URL")

    monkeypatch.setattr(upload_router.FileUploader, "cos_key_from_url", lambda url: None)

    with pytest.raises(HTTPException) as excinfo:
        upload_router.confirm_avatar(
            {"url": "https://cdn.example.com/avatar.jpg"},
            user=SimpleNamespace(id=42),
            db=FakeDB(),
        )

    assert excinfo.value.status_code == 400


def test_create_post_image_urls_are_audited_before_db_mutation(monkeypatch):
    from app.routers import posts as posts_router

    events = []

    class FakeDB:
        def add(self, item):
            events.append(("db.add", type(item).__name__))

        def flush(self):
            events.append(("db.flush",))

        def commit(self):
            events.append(("db.commit",))

        def rollback(self):
            events.append(("db.rollback",))

    monkeypatch.setattr(
        posts_router,
        "moderate_route_fields",
        lambda *args, **kwargs: events.append(("text_moderation",)),
    )
    monkeypatch.setattr(posts_router.FileUploader, "cos_key_from_url", lambda url: IMAGE_KEY)
    monkeypatch.setattr(
        "app.services.media_moderation_route_helpers.media_moderation_service",
        RejectingMediaService(),
    )

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            posts_router.create_post(
                image=None,
                video=None,
                content="safe text",
                image_urls=f'["{IMAGE_URL}"]',
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

    assert excinfo.value.status_code == 422
    assert events == [("text_moderation",)]


def test_create_post_rejects_external_image_urls_before_db_mutation(monkeypatch):
    from app.routers import posts as posts_router

    events = []

    class FakeDB:
        def add(self, item):
            events.append(("db.add", type(item).__name__))

        def rollback(self):
            events.append(("db.rollback",))

    monkeypatch.setattr(
        posts_router,
        "moderate_route_fields",
        lambda *args, **kwargs: events.append(("text_moderation",)),
    )
    monkeypatch.setattr(posts_router.FileUploader, "cos_key_from_url", lambda url: None)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            posts_router.create_post(
                image=None,
                video=None,
                content="safe text",
                image_urls='["https://cdn.example.com/image.jpg"]',
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

    assert excinfo.value.status_code == 400
    assert events == [("text_moderation",)]


def test_create_post_rejects_other_user_cos_video_url_before_db_mutation(monkeypatch):
    from app.routers import posts as posts_router

    events = []

    class FakeDB:
        def add(self, item):
            events.append(("db.add", type(item).__name__))

    monkeypatch.setattr(
        posts_router,
        "moderate_route_fields",
        lambda *args, **kwargs: events.append(("text_moderation",)),
    )
    monkeypatch.setattr(
        posts_router.FileUploader,
        "cos_key_from_url",
        lambda url: "posts/7/20260724/private.mp4",
    )

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            posts_router.create_post(
                image=None,
                video=None,
                content="safe text",
                image_urls=None,
                video_url_input="https://test.example/posts/7/20260724/private.mp4",
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

    assert excinfo.value.status_code == 403
    assert events == [("text_moderation",)]


def test_update_post_rejects_other_user_cos_video_url_before_commit(monkeypatch):
    from app.routers import posts as posts_router

    post = SimpleNamespace(
        id=5,
        user_id=42,
        visibility="public",
        community_only=False,
        community_id=None,
        content="old",
        video_url=None,
    )

    class FakeDB:
        def commit(self):
            raise AssertionError("commit must not run for invalid media URL")

    monkeypatch.setattr(posts_router, "load_visible_post", lambda db, post_id, user_id: post)
    monkeypatch.setattr(posts_router, "moderate_route_fields", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        posts_router.FileUploader,
        "cos_key_from_url",
        lambda url: "posts/7/20260724/private.mp4",
    )

    with pytest.raises(HTTPException) as excinfo:
        posts_router.update_post(
            5,
            {"video_url": "https://test.example/posts/7/20260724/private.mp4"},
            user=SimpleNamespace(id=42),
            db=FakeDB(),
        )

    assert excinfo.value.status_code == 403


def test_delete_post_skips_unowned_cos_media_urls(monkeypatch):
    from app.routers import posts as posts_router

    events = []
    post = SimpleNamespace(
        id=5,
        user_id=42,
        images='["https://test.example/posts/7/20260724/private.jpg"]',
        video_url="https://test.example/posts/7/20260724/private.mp4",
    )

    class Query:
        def filter(self, *args, **kwargs):
            return self

        def delete(self, synchronize_session=False):
            events.append(("query.delete", synchronize_session))

    class FakeDB:
        def query(self, model):
            return Query()

        def delete(self, item):
            events.append(("db.delete", item.id))

        def commit(self):
            events.append(("db.commit",))

        def rollback(self):
            events.append(("db.rollback",))

    monkeypatch.setattr(posts_router, "load_visible_post", lambda db, post_id, user_id: post)
    monkeypatch.setattr(
        posts_router.FileUploader,
        "cos_key_from_url",
        lambda url: "posts/7/20260724/private.mp4" if url.endswith(".mp4") else "posts/7/20260724/private.jpg",
    )
    monkeypatch.setattr(
        posts_router.FileUploader,
        "delete_file",
        lambda url: events.append(("delete_file", url)) or {"success": True},
    )

    result = posts_router.delete_post(5, user=SimpleNamespace(id=42), db=FakeDB())

    assert result == {"message": "Post deleted successfully"}
    assert not any(event[0] == "delete_file" for event in events)
    assert ("db.delete", 5) in events
    assert ("db.commit",) in events


def test_create_post_image_rejection_cleans_uploaded_object_and_skips_db(monkeypatch):
    from app.routers import posts as posts_router

    events = []

    class FakeImage:
        filename = "image.jpg"

        async def read(self):
            return b"image-bytes"

    class FakeDB:
        def add(self, item):
            events.append(("db.add", type(item).__name__))

        def flush(self):
            events.append(("db.flush",))

        def commit(self):
            events.append(("db.commit",))

        def rollback(self):
            events.append(("db.rollback",))

        def query(self, model):
            raise AssertionError("community/user role DB lookup not expected")

    monkeypatch.setattr(
        posts_router,
        "moderate_route_fields",
        lambda *args, **kwargs: events.append(("text_moderation",)),
    )
    monkeypatch.setattr(
        posts_router.FileUploader,
        "save_file",
        lambda *args, **kwargs: {"success": True, "url": IMAGE_URL, "cos_key": IMAGE_KEY},
    )
    monkeypatch.setattr(
        posts_router.FileUploader,
        "delete_file",
        lambda url: events.append(("delete", url)) or {"success": True},
    )
    monkeypatch.setattr(
        "app.services.image_optimizer.ImageOptimizer.optimize_image",
        lambda contents: {"success": True, "data": b"optimized"},
    )
    monkeypatch.setattr(
        "app.services.media_moderation_route_helpers.media_moderation_service",
        RejectingMediaService(),
    )

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            posts_router.create_post(
                image=FakeImage(),
                video=None,
                content="safe text",
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

    assert excinfo.value.status_code == 422
    assert events == [("text_moderation",), ("delete", IMAGE_URL)]
