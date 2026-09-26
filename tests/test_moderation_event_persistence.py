import json

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from app.models.models import Base, ModerationEvent
from app.services.media_moderation_route_helpers import moderate_cos_image_or_raise
from app.services.moderation_errors import ContentRejected, ModerationUnavailable
from app.services.moderation_route_helpers import moderate_route_fields


@pytest.fixture()
def event_db():
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


class RejectingTextService:
    def moderate_fields(self, fields, context):
        raise ContentRejected()


class UnavailableTextService:
    def moderate_fields(self, fields, context):
        raise ModerationUnavailable(RuntimeError("PRIVATE_BODY_SHOULD_NOT_LEAK"))


class RejectingImageService:
    def moderate(self, target):
        raise ContentRejected()


class UnavailableImageService:
    def moderate(self, target):
        raise ModerationUnavailable(RuntimeError("SIGNED_URL_SHOULD_NOT_LEAK"))


def _events(db):
    return db.query(ModerationEvent).order_by(ModerationEvent.id.asc()).all()


def test_text_moderation_rejection_writes_safe_metadata_event(event_db):
    with pytest.raises(HTTPException) as exc_info:
        moderate_route_fields(
            RejectingTextService(),
            "POST /api/posts",
            {"content": "raw private body"},
            actor_user_id=12,
            is_public=True,
            db=event_db,
        )

    assert exc_info.value.status_code == 422
    row = _events(event_db)[0]
    assert row.provider == "local"
    assert row.content_type == "text"
    assert row.route_key == "POST /api/posts"
    assert row.target_type == "POST /api/posts"
    assert row.actor_user_id == 12
    assert row.decision == "reject"
    assert row.error_code == "CONTENT_REJECTED"
    assert "raw private body" not in json.dumps(row.__dict__, default=str)


def test_text_moderation_unavailable_writes_safe_metadata_event(event_db):
    with pytest.raises(HTTPException) as exc_info:
        moderate_route_fields(
            UnavailableTextService(),
            "POST /api/posts",
            {"content": "raw private body"},
            actor_user_id=12,
            is_public=True,
            db=event_db,
        )

    assert exc_info.value.status_code == 503
    row = _events(event_db)[0]
    assert row.decision == "unavailable"
    assert row.error_code == "MODERATION_UNAVAILABLE"
    assert "PRIVATE_BODY_SHOULD_NOT_LEAK" not in json.dumps(row.__dict__, default=str)


def test_image_moderation_rejection_writes_safe_metadata_event_without_cos_key(event_db):
    with pytest.raises(HTTPException) as exc_info:
        moderate_cos_image_or_raise(
            "posts/12/private-image.jpg",
            target_type="post_image",
            actor_user_id=12,
            upload_type="post",
            is_public=True,
            data_id="post-image-12",
            service=RejectingImageService(),
            db=event_db,
            route_key="POST /api/posts",
        )

    assert exc_info.value.status_code == 422
    row = _events(event_db)[0]
    assert row.provider == "tencent_cos_ci"
    assert row.content_type == "image"
    assert row.route_key == "POST /api/posts"
    assert row.target_type == "post_image"
    assert row.target_id == "post-image-12"
    assert row.decision == "reject"
    assert row.error_code == "CONTENT_REJECTED"
    body = json.dumps(row.__dict__, default=str)
    assert "posts/12/private-image.jpg" not in body
    assert "cos_key" not in body
    assert "signed_url" not in body


def test_image_moderation_unavailable_writes_safe_metadata_event(event_db):
    with pytest.raises(HTTPException) as exc_info:
        moderate_cos_image_or_raise(
            "avatars/12/private-image.jpg",
            target_type="avatar_image",
            actor_user_id=12,
            upload_type="avatar",
            is_public=True,
            service=UnavailableImageService(),
            db=event_db,
        )

    assert exc_info.value.status_code == 503
    row = _events(event_db)[0]
    assert row.decision == "unavailable"
    assert row.error_code == "MODERATION_UNAVAILABLE"
    assert "SIGNED_URL_SHOULD_NOT_LEAK" not in json.dumps(row.__dict__, default=str)
