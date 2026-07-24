import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.routers.auth import RegisterRequest, SendOtpRequest, is_allowed_registration_email, normalize_username, send_otp


def test_registration_email_allows_only_qq_foxmail_and_vip_qq_domains():
    assert is_allowed_registration_email("user@qq.com") is True
    assert is_allowed_registration_email("USER@foxmail.com") is True
    assert is_allowed_registration_email("name@vip.qq.com") is True
    assert is_allowed_registration_email("user@gmail.com") is False
    assert is_allowed_registration_email("user@qq.com.evil.com") is False


def test_register_request_rejects_non_qq_family_email():
    with pytest.raises(ValidationError):
        RegisterRequest(username="南图用户✨001", email="user@gmail.com", password="Password123", email_code="123456")


def test_register_otp_rejects_non_qq_family_email_before_database_lookup():
    class GuardDb:
        def query(self, *_args, **_kwargs):
            raise AssertionError("registration domain must be checked before querying users")

    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    data = SendOtpRequest(email="user@gmail.com", purpose="register")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(send_otp(data, request=request, db=GuardDb()))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "目前仅支持 QQ 邮箱注册"


def test_username_supports_chinese_english_digits_title_symbols_and_emoji():
    assert normalize_username("  Coser·小鹿✨001  ") == "Coser·小鹿✨001"
    assert normalize_username("「阿花」🎀") == "「阿花」🎀"


@pytest.mark.parametrize("username", ["a", "bad/name", "bad@name", "bad\nname", "\u200bhidden"])
def test_username_rejects_too_short_route_control_and_invisible_characters(username):
    with pytest.raises(ValueError):
        normalize_username(username)
