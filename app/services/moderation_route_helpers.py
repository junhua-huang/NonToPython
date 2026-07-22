from collections.abc import Mapping

from app.services.moderation_errors import (
    AppContractError,
    ModerationUnavailable,
    to_http_exception,
)
from app.services.moderation_inventory import MODERATED_TEXT_FIELDS
from app.services.moderation_types import ModerationContext


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


def moderate_route_fields(
    service,
    route_key: str,
    payload: object,
    *,
    actor_user_id: int | None,
    is_public: bool,
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
    except AppContractError as error:
        raise to_http_exception(error) from None
    except Exception as exc:
        raise to_http_exception(ModerationUnavailable(exc)) from None
