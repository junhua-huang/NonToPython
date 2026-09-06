import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.models import User
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
        asyncio.run(send_otp(data, request=request, db=GuardDb(), background_tasks=BackgroundTasks()))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "目前仅支持 QQ 邮箱注册"


def test_send_otp_schedules_email_without_waiting_for_smtp(monkeypatch):
    class Query:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class Db:
        def query(self, model):
            assert model is User
            return Query()

    monkeypatch.setattr("app.routers.auth.OtpService.check_rate_limit", lambda *_args: (True, None))
    monkeypatch.setattr("app.routers.auth.OtpService.generate_and_store", lambda *_args: "123456")

    async def slow_send(*_args):
        await asyncio.sleep(0.2)
        return True

    monkeypatch.setattr("app.routers.auth.EmailService.send_otp_email", slow_send)

    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    data = SendOtpRequest(email="user@qq.com", purpose="register")
    background_tasks = BackgroundTasks()

    started = time.perf_counter()
    result = asyncio.run(send_otp(data, request=request, db=Db(), background_tasks=background_tasks))
    elapsed = time.perf_counter() - started

    assert result == {"message": "验证码已发送，请查收邮箱"}
    assert elapsed < 0.1
    assert len(background_tasks.tasks) == 1


def test_forgot_password_schedules_email_without_waiting_for_smtp(monkeypatch):
    from app.routers.auth import ForgotPasswordRequest, forgot_password

    existing_user = SimpleNamespace(email="user@qq.com")

    class Query:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return existing_user

    class Db:
        def query(self, model):
            assert model is User
            return Query()

    monkeypatch.setattr("app.routers.auth.OtpService.check_rate_limit", lambda *_args: (True, None))
    monkeypatch.setattr("app.routers.auth.OtpService.generate_and_store", lambda *_args: "123456")

    async def slow_send(*_args):
        await asyncio.sleep(0.2)
        return True

    monkeypatch.setattr("app.routers.auth.EmailService.send_otp_email", slow_send)

    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    data = ForgotPasswordRequest(email="user@qq.com")
    background_tasks = BackgroundTasks()

    started = time.perf_counter()
    result = asyncio.run(forgot_password(data, request=request, db=Db(), background_tasks=background_tasks))
    elapsed = time.perf_counter() - started

    assert result == {"message": "若该邮箱已注册，验证码已发送"}
    assert elapsed < 0.1
    assert len(background_tasks.tasks) == 1


def test_username_supports_chinese_english_digits_title_symbols_and_emoji():
    assert normalize_username("  Coser·小鹿✨001  ") == "Coser·小鹿✨001"
    assert normalize_username("「阿花」🎀") == "「阿花」🎀"


@pytest.mark.parametrize("username", ["a", "bad/name", "bad@name", "bad\nname", "\u200bhidden"])
def test_username_rejects_too_short_route_control_and_invisible_characters(username):
    with pytest.raises(ValueError):
        normalize_username(username)
