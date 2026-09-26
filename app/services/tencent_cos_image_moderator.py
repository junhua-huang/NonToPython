from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlsplit
import xml.etree.ElementTree as ET

import requests

from app.core.config import Config
from app.services.media_moderation_types import (
    MediaModerationDecision,
    MediaModerationResult,
    MediaModerationTarget,
)
from app.utils import FileUploader


class TencentCosImageModerationError(Exception):
    """Sanitized provider exception for Tencent COS CI image audit failures."""


_DEFAULT_COS_CLIENT = object()
_MAX_PROVIDER_RESPONSE_BYTES = 64 * 1024


class TencentCosImageModerator:
    provider_name = "tencent_cos_ci"

    def __init__(self, cos_client=_DEFAULT_COS_CLIENT, config=Config):
        self._cos_client = cos_client
        self._config = config

    def _get_cos_client(self):
        if self._cos_client is not _DEFAULT_COS_CLIENT:
            return self._cos_client
        try:
            return FileUploader._get_cos_client()
        except Exception:
            raise TencentCosImageModerationError("COS client unavailable") from None

    def moderate(self, target: MediaModerationTarget) -> MediaModerationResult:
        params = self._build_query_params(target)
        try:
            cos_client = self._get_cos_client()
            if cos_client is None:
                raise TencentCosImageModerationError("COS client unavailable")
            signed_url = cos_client.get_presigned_url(
                Method="GET",
                Bucket=self._config.COS_BUCKET_NAME,
                Key=target.cos_key,
                Expired=300,
                Params=params,
            )
            _assert_signed_url_contains_params(signed_url, params)
            response = requests.get(
                signed_url,
                timeout=self._config.COS_CI_IMAGE_AUDIT_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise TencentCosImageModerationError("Tencent COS CI image audit HTTP error")
            return self._parse_response(_bounded_response_text(response))
        except TencentCosImageModerationError:
            raise
        except Exception:
            raise TencentCosImageModerationError(
                "Tencent COS CI image audit unavailable"
            ) from None

    def _build_query_params(self, target: MediaModerationTarget) -> dict[str, str]:
        params = {
            "ci-process": "sensitive-content-recognition",
            "async": "0",
            "dataid": target.data_id or _safe_data_id(target.cos_key),
        }
        biz_type = getattr(self._config, "COS_CI_IMAGE_AUDIT_BIZ_TYPE", "")
        if biz_type:
            params["biz-type"] = biz_type
        large_image_detect = getattr(
            self._config, "COS_CI_IMAGE_AUDIT_LARGE_IMAGE_DETECT", "0"
        )
        if large_image_detect:
            params["large-image-detect"] = str(large_image_detect)
        return params

    def _parse_response(self, xml_text: str) -> MediaModerationResult:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            raise TencentCosImageModerationError(
                "Tencent COS CI image audit invalid XML"
            ) from None

        result_text = _find_text(root, "Result")
        if result_text not in {"0", "1", "2"}:
            raise TencentCosImageModerationError(
                "Tencent COS CI image audit invalid result"
            )

        decision = (
            MediaModerationDecision.APPROVE
            if result_text == "0"
            else MediaModerationDecision.REJECT
        )
        return MediaModerationResult(
            decision=decision,
            provider=self.provider_name,
            label=_optional_text(_find_text(root, "Label")),
            category=_optional_text(_find_text(root, "Category")),
            sub_label=_optional_text(_find_text(root, "SubLabel")),
            score=_parse_score(_find_text(root, "Score")),
            job_id=_optional_text(_find_text(root, "JobId")),
        )


def _assert_signed_url_contains_params(url: str, params: dict[str, str]) -> None:
    signed_query = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
    if not all(signed_query.get(key) == value for key, value in params.items()):
        raise TencentCosImageModerationError("signed audit URL unavailable")


def _bounded_response_text(response) -> str:
    content = getattr(response, "content", None)
    if content is not None:
        if len(content) > _MAX_PROVIDER_RESPONSE_BYTES:
            raise TencentCosImageModerationError("Tencent COS CI image audit response too large")
        return content.decode(getattr(response, "encoding", None) or "utf-8", errors="replace")
    text = getattr(response, "text", "")
    if len(text.encode("utf-8", errors="replace")) > _MAX_PROVIDER_RESPONSE_BYTES:
        raise TencentCosImageModerationError("Tencent COS CI image audit response too large")
    return text


def _safe_data_id(cos_key: str) -> str:
    return hashlib.sha256(cos_key.encode("utf-8")).hexdigest()[:32]


def _find_text(root: ET.Element, tag_name: str) -> str | None:
    for element in root.iter():
        if _strip_namespace(element.tag) == tag_name:
            return element.text.strip() if element.text is not None else ""
    return None


def _strip_namespace(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _optional_text(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return value


def _parse_score(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        raise TencentCosImageModerationError(
            "Tencent COS CI image audit invalid score"
        ) from None
