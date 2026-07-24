from collections.abc import Mapping

from app.services.moderation_errors import (
    AppContractError,
    ContentRejected,
    ModerationUnavailable,
    to_http_exception,
)
from app.services.moderation_event_service import record_moderation_event
from app.services.moderation_inventory import MODERATED_TEXT_FIELDS
from app.services.moderation_types import ModerationContext

_MEDIA_MESSAGE_TYPES = {"image", "video"}


def _extract_moderated_fields(
    route_key: str,
    payload: object,
) -> dict[str, str | None]:
    if route_key not in MODERATED_TEXT_FIELDS:
        return {}
    if not isinstance(payload, Mapping):
        raise ModerationUnavailable(TypeError("moderation payload must be a mapping"))

    fields: dict[str, str | None] = {}
    for field_name in MODERATED_TEXT_FIELDS[route_key]:
        if field_name.endswith("[]"):
            source_name = field_name[:-2]
            value = payload.get(source_name)
            if value is None:
                continue
            if not isinstance(value, list):
                raise ModerationUnavailable(
                    TypeError("moderation list field must be a list")
                )
            for index, item in enumerate(value):
                if item is None:
                    continue
                if not isinstance(item, str):
                    raise ModerationUnavailable(
                        TypeError("moderation list values must be strings")
                    )
                if item.strip():
                    fields[f"{source_name}[{index}]"] = item
            continue

        if field_name not in payload:
            continue
        value = payload.get(field_name)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ModerationUnavailable(TypeError("moderation field must be a string"))
        if value.strip():
            fields[field_name] = value
    return fields


def message_text_payload_for_moderation(payload: object) -> dict[str, str]:
    if not isinstance(payload, Mapping):
        return {}
    raw_type = payload.get("message_type") or "text"
    message_type = raw_type.strip().lower() if isinstance(raw_type, str) else "text"
    raw_content = payload.get("content")
    if not isinstance(raw_content, str):
        return {}
    content = raw_content.strip()
    if not content:
        return {}
    media_value = payload.get("media_url") or payload.get("file_url")
    media_url = media_value.strip() if isinstance(media_value, str) else ""
    if message_type in _MEDIA_MESSAGE_TYPES:
        if media_url and content == media_url:
            return {}
        if (
            not media_url
            and content.startswith(("http://", "https://"))
            and not any(character.isspace() for character in content)
        ):
            return {}
    return {"content": content}


def moderate_route_fields(
    service,
    route_key: str,
    payload: object,
    *,
    actor_user_id: int | None,
    is_public: bool,
    db=None,
    target_id: str | int | None = None,
) -> None:
    try:
        fields = _extract_moderated_fields(route_key, payload)
        if not fields:
            return
        service.moderate_fields(
            fields,
            ModerationContext(
                target_type=route_key,
                actor_user_id=actor_user_id,
                is_public=is_public,
            ),
        )
    except ContentRejected as error:
        record_moderation_event(
            db,
            provider="local",
            content_type="text",
            route_key=route_key,
            target_type=route_key,
            target_id=target_id,
            actor_user_id=actor_user_id,
            decision="reject",
            error_code=error.error_code.value,
        )
        raise to_http_exception(error) from None
    except AppContractError as error:
        record_moderation_event(
            db,
            provider="local",
            content_type="text",
            route_key=route_key,
            target_type=route_key,
            target_id=target_id,
            actor_user_id=actor_user_id,
            decision="unavailable",
            error_code=error.error_code.value,
        )
        raise to_http_exception(error) from None
    except Exception as exc:
        error = ModerationUnavailable(exc)
        record_moderation_event(
            db,
            provider="local",
            content_type="text",
            route_key=route_key,
            target_type=route_key,
            target_id=target_id,
            actor_user_id=actor_user_id,
            decision="unavailable",
            error_code=error.error_code.value,
        )
        raise to_http_exception(error) from None
