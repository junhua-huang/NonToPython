import logging
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from app.services.moderation_errors import (
    ContentRejected,
    ModerationUnavailable,
    to_http_exception,
)


SENSITIVE_COS_KEY = "private/uploads/secret-image.jpg"
SIGNED_URL = "https://bucket.cos.ap-guangzhou.myqcloud.com/private/uploads/secret-image.jpg?sign=SECRET_SIGNATURE"


class FakeCosClient:
    def __init__(self):
        self.calls = []

    def get_presigned_url(self, **kwargs):
        self.calls.append(kwargs)
        params = kwargs.get("Params") or {}
        return f"{SIGNED_URL}&{urlencode(params)}"


class FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def make_config(**overrides):
    values = {
        "COS_BUCKET_NAME": "test-bucket-1250000000",
        "COS_CI_IMAGE_AUDIT_ENABLED": True,
        "COS_CI_IMAGE_AUDIT_BIZ_TYPE": "audit-biz",
        "COS_CI_IMAGE_AUDIT_TIMEOUT_SECONDS": 8,
        "COS_CI_IMAGE_AUDIT_LARGE_IMAGE_DETECT": "1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_target(**overrides):
    from app.services.media_moderation_types import MediaModerationTarget

    values = {
        "cos_key": SENSITIVE_COS_KEY,
        "target_type": "post_image",
        "actor_user_id": 123,
        "upload_type": "image",
        "is_public": True,
        "content_type": "image/jpeg",
        "data_id": "upload-abc-123",
    }
    values.update(overrides)
    return MediaModerationTarget(**values)


def test_provider_sends_expected_ci_query_params(monkeypatch):
    from app.services.media_moderation_types import MediaModerationDecision
    from app.services.tencent_cos_image_moderator import TencentCosImageModerator

    client = FakeCosClient()
    captured = {}

    def fake_get(url, timeout, allow_redirects=True):
        captured["url"] = url
        captured["timeout"] = timeout
        captured["allow_redirects"] = allow_redirects
        return FakeResponse(
            200,
            "<Response><JobsDetail><Result>0</Result><Label>Normal</Label>"
            "<Category>Normal</Category><SubLabel></SubLabel><Score>0</Score>"
            "<JobId>job-1</JobId></JobsDetail></Response>",
        )

    monkeypatch.setattr("app.services.tencent_cos_image_moderator.requests.get", fake_get)

    result = TencentCosImageModerator(cos_client=client, config=make_config()).moderate(
        make_target()
    )

    assert result.decision is MediaModerationDecision.APPROVE
    assert result.provider == "tencent_cos_ci"
    assert captured["timeout"] == 8
    assert captured["allow_redirects"] is False
    assert client.calls == [
        {
            "Method": "GET",
            "Bucket": "test-bucket-1250000000",
            "Key": SENSITIVE_COS_KEY,
            "Expired": 300,
            "Params": {
                "ci-process": "sensitive-content-recognition",
                "async": "0",
                "dataid": "upload-abc-123",
                "biz-type": "audit-biz",
                "large-image-detect": "1",
            },
        }
    ]
    query = parse_qs(urlparse(captured["url"]).query)
    assert query["ci-process"] == ["sensitive-content-recognition"]
    assert query["async"] == ["0"]
    assert query["dataid"] == ["upload-abc-123"]
    assert query["biz-type"] == ["audit-biz"]
    assert query["large-image-detect"] == ["1"]


@pytest.mark.parametrize(
    ("provider_result", "expected_decision"),
    [("0", "approve"), ("1", "reject"), ("2", "reject")],
)
def test_provider_maps_result_values(monkeypatch, provider_result, expected_decision):
    from app.services.media_moderation_types import MediaModerationDecision
    from app.services.tencent_cos_image_moderator import TencentCosImageModerator

    def fake_get(url, timeout, allow_redirects=True):
        return FakeResponse(
            200,
            f"<Response><Result>{provider_result}</Result><Label>Porn</Label>"
            "<Category>Image</Category><SubLabel>Explicit</SubLabel>"
            "<Score>87</Score><JobId>job-99</JobId></Response>",
        )

    monkeypatch.setattr("app.services.tencent_cos_image_moderator.requests.get", fake_get)

    result = TencentCosImageModerator(
        cos_client=FakeCosClient(), config=make_config(COS_CI_IMAGE_AUDIT_BIZ_TYPE="")
    ).moderate(make_target(data_id=None))

    assert result.decision is MediaModerationDecision(expected_decision)
    assert result.label == "Porn"
    assert result.category == "Image"
    assert result.sub_label == "Explicit"
    assert result.score == 87
    assert result.job_id == "job-99"


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(500, "provider down with OCR private text"),
        FakeResponse(200, "not xml"),
        FakeResponse(200, "<Response><Label>Normal</Label></Response>"),
        FakeResponse(200, "<Response><Result>9</Result></Response>"),
    ],
)
def test_provider_failures_raise_sanitized_unavailable(monkeypatch, response):
    from app.services.tencent_cos_image_moderator import (
        TencentCosImageModerator,
        TencentCosImageModerationError,
    )

    def fake_get(url, timeout, allow_redirects=True):
        return response

    monkeypatch.setattr("app.services.tencent_cos_image_moderator.requests.get", fake_get)

    with pytest.raises(TencentCosImageModerationError) as excinfo:
        TencentCosImageModerator(cos_client=FakeCosClient(), config=make_config()).moderate(
            make_target()
        )

    error_text = str(excinfo.value)
    assert "provider down" not in error_text
    assert "OCR" not in error_text
    assert SENSITIVE_COS_KEY not in error_text
    assert SIGNED_URL not in error_text
    assert "SECRET_SIGNATURE" not in error_text


def test_provider_missing_cos_client_is_sanitized_unavailable():
    from app.services.tencent_cos_image_moderator import (
        TencentCosImageModerator,
        TencentCosImageModerationError,
    )

    with pytest.raises(TencentCosImageModerationError) as excinfo:
        TencentCosImageModerator(cos_client=None, config=make_config()).moderate(make_target())

    assert "COS client unavailable" in str(excinfo.value)
    assert SENSITIVE_COS_KEY not in str(excinfo.value)


def test_provider_default_cos_client_initialization_is_sanitized(monkeypatch):
    from app.services.tencent_cos_image_moderator import (
        TencentCosImageModerator,
        TencentCosImageModerationError,
    )

    def fail_client():
        raise RuntimeError(f"raw secret leak {SENSITIVE_COS_KEY} {SIGNED_URL}")

    monkeypatch.setattr(
        "app.services.tencent_cos_image_moderator.FileUploader._get_cos_client",
        fail_client,
    )

    with pytest.raises(TencentCosImageModerationError) as excinfo:
        TencentCosImageModerator(config=make_config()).moderate(make_target())

    assert "COS client unavailable" in str(excinfo.value)
    assert "raw secret leak" not in str(excinfo.value)
    assert SENSITIVE_COS_KEY not in str(excinfo.value)
    assert SIGNED_URL not in str(excinfo.value)


def test_provider_requires_signed_url_to_include_audit_params(monkeypatch):
    from app.services.tencent_cos_image_moderator import (
        TencentCosImageModerator,
        TencentCosImageModerationError,
    )

    class BadCosClient:
        def get_presigned_url(self, **kwargs):
            return SIGNED_URL

    def fake_get(url, timeout, allow_redirects=True):
        raise AssertionError("unsigned audit URL must not be requested")

    monkeypatch.setattr("app.services.tencent_cos_image_moderator.requests.get", fake_get)

    with pytest.raises(TencentCosImageModerationError) as excinfo:
        TencentCosImageModerator(cos_client=BadCosClient(), config=make_config()).moderate(
            make_target()
        )

    assert "signed audit URL unavailable" in str(excinfo.value)
    assert SIGNED_URL not in str(excinfo.value)


def test_provider_rejects_oversized_provider_response(monkeypatch):
    from app.services.tencent_cos_image_moderator import (
        TencentCosImageModerator,
        TencentCosImageModerationError,
    )

    def fake_get(url, timeout, allow_redirects=True):
        return FakeResponse(200, "<Response>" + ("x" * 70000) + "</Response>")

    monkeypatch.setattr("app.services.tencent_cos_image_moderator.requests.get", fake_get)

    with pytest.raises(TencentCosImageModerationError) as excinfo:
        TencentCosImageModerator(cos_client=FakeCosClient(), config=make_config()).moderate(
            make_target()
        )

    assert "response too large" in str(excinfo.value)
    assert "x" * 100 not in str(excinfo.value)


def test_service_disabled_skips_provider_call():
    from app.services.media_moderation_service import MediaModerationService
    from app.services.media_moderation_types import MediaModerationDecision

    class FailingProvider:
        def moderate(self, target):
            raise AssertionError("provider should not be called")

    result = MediaModerationService(
        provider=FailingProvider(), config=make_config(COS_CI_IMAGE_AUDIT_ENABLED=False)
    ).moderate(make_target())

    assert result.decision is MediaModerationDecision.APPROVE
    assert result.provider == "disabled"


def test_service_database_setting_overrides_disabled_environment():
    import sqlalchemy as sa
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import Base
    from app.models.models import AdminSetting
    from app.services.media_moderation_service import MediaModerationService
    from app.services.media_moderation_types import MediaModerationDecision, MediaModerationResult

    engine = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(AdminSetting(key="image_moderation_enabled", value="true", updated_by=1))
    db.commit()

    class ApprovingProvider:
        def __init__(self):
            self.called = False

        def moderate(self, target):
            self.called = True
            return MediaModerationResult(decision=MediaModerationDecision.APPROVE, provider="tencent_cos_ci")

    provider = ApprovingProvider()
    result = MediaModerationService(
        provider=provider,
        config=make_config(COS_CI_IMAGE_AUDIT_ENABLED=False),
    ).moderate(make_target(), db=db)

    assert provider.called is True
    assert result.decision is MediaModerationDecision.APPROVE
    assert result.provider == "tencent_cos_ci"
    db.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_service_rejection_maps_to_content_rejected_contract(caplog):
    from app.services.media_moderation_service import MediaModerationService
    from app.services.media_moderation_types import (
        MediaModerationDecision,
        MediaModerationResult,
    )

    class RejectingProvider:
        def moderate(self, target):
            return MediaModerationResult(
                decision=MediaModerationDecision.REJECT,
                provider="tencent_cos_ci",
                label="Porn",
                category="Image",
                sub_label="Explicit",
                score=91,
                job_id="job-reject",
            )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(ContentRejected) as excinfo:
            MediaModerationService(
                provider=RejectingProvider(), config=make_config()
            ).moderate(make_target())

    http_error = to_http_exception(excinfo.value)
    assert http_error.status_code == 422
    assert http_error.detail == {
        "code": "CONTENT_REJECTED",
        "message": "内容未通过审核",
        "retryable": False,
    }
    assert "target_type=post_image" in caplog.text
    assert "actor_user_id=123" in caplog.text
    assert "upload_type=image" in caplog.text
    assert "decision=reject" in caplog.text
    assert SENSITIVE_COS_KEY not in caplog.text
    assert SIGNED_URL not in caplog.text


def test_service_provider_error_maps_to_moderation_unavailable_contract(caplog):
    from app.services.media_moderation_service import MediaModerationService
    from app.services.tencent_cos_image_moderator import TencentCosImageModerationError

    class FailingProvider:
        def moderate(self, target):
            raise TencentCosImageModerationError("sanitized provider unavailable")

    with caplog.at_level(logging.WARNING):
        with pytest.raises(ModerationUnavailable) as excinfo:
            MediaModerationService(provider=FailingProvider(), config=make_config()).moderate(
                make_target()
            )

    http_error = to_http_exception(excinfo.value)
    assert http_error.status_code == 503
    assert http_error.detail == {
        "code": "MODERATION_UNAVAILABLE",
        "message": "内容审核服务暂不可用，请稍后重试",
        "retryable": True,
    }
    assert "error_type=TencentCosImageModerationError" in caplog.text
    assert SENSITIVE_COS_KEY not in caplog.text
    assert SIGNED_URL not in caplog.text


def test_service_does_not_log_untrusted_provider_result_string(caplog):
    from app.services.media_moderation_service import MediaModerationService
    from app.services.media_moderation_types import (
        MediaModerationDecision,
        MediaModerationResult,
    )

    class UntrustedProviderName:
        def moderate(self, target):
            return MediaModerationResult(
                decision=MediaModerationDecision.APPROVE,
                provider=f"leaky provider {SENSITIVE_COS_KEY} {SIGNED_URL}",
            )

    with caplog.at_level(logging.INFO):
        MediaModerationService(provider=UntrustedProviderName(), config=make_config()).moderate(
            make_target()
        )

    assert "provider=tencent_cos_ci" in caplog.text
    assert "leaky provider" not in caplog.text
    assert SENSITIVE_COS_KEY not in caplog.text
    assert SIGNED_URL not in caplog.text


def test_service_unexpected_error_is_sanitized_in_logs(caplog):
    from app.services.media_moderation_service import MediaModerationService

    class ExplodingProvider:
        def moderate(self, target):
            raise RuntimeError(f"unexpected leak {SENSITIVE_COS_KEY} {SIGNED_URL}")

    with caplog.at_level(logging.WARNING):
        with pytest.raises(ModerationUnavailable):
            MediaModerationService(provider=ExplodingProvider(), config=make_config()).moderate(
                make_target()
            )

    assert "RuntimeError" in caplog.text
    assert "unexpected leak" not in caplog.text
    assert SENSITIVE_COS_KEY not in caplog.text
    assert SIGNED_URL not in caplog.text
