import pytest
from fastapi import HTTPException

from app.services.moderation_errors import ContentRejected, ModerationUnavailable
from app.services.moderation_route_helpers import moderate_route_fields
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
