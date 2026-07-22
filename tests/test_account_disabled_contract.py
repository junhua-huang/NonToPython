from types import SimpleNamespace

import pytest
from jose import jwt
from fastapi import HTTPException

from app.core import auth_core
from app.core.config import Config
from app.dependencies import get_current_user, require_admin
from app.services.moderation_errors import AccountDisabled, to_http_exception


class FakeQuery:
    def __init__(self, user):
        self.user = user

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.user


class FakeSession:
    def __init__(self, user):
        self.user = user
        self.expunge_called = False
        self.closed = False

    def query(self, *args, **kwargs):
        return FakeQuery(self.user)

    def expunge(self, user):
        self.expunge_called = True

    def close(self):
        self.closed = True


def token_for_user(user_id: int) -> str:
    return jwt.encode({"sub": str(user_id)}, Config.JWT_SECRET_KEY, algorithm="HS256")


def test_account_disabled_contract_is_stable_and_safe():
    error = AccountDisabled()
    http_error = to_http_exception(error)

    assert http_error.status_code == 403
    assert http_error.detail == {
        "code": "ACCOUNT_DISABLED",
        "message": "账号已停用",
        "retryable": False,
    }


def test_verify_token_rejects_inactive_user_without_returning_user(monkeypatch):
    user = SimpleNamespace(id=3, is_active=False)
    session = FakeSession(user)
    monkeypatch.setattr(auth_core, "SessionLocal", lambda: session)

    with pytest.raises(AccountDisabled):
        auth_core.verify_token(token_for_user(3))

    assert session.expunge_called is False
    assert session.closed is True


def test_http_current_user_dependency_maps_disabled_account_to_contract(monkeypatch):
    monkeypatch.setattr("app.dependencies.verify_token", lambda token: (_ for _ in ()).throw(AccountDisabled()))

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(SimpleNamespace(), access_token="token", db=None)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {
        "code": "ACCOUNT_DISABLED",
        "message": "账号已停用",
        "retryable": False,
    }


def test_role_dependency_maps_disabled_account_before_role_lookup(monkeypatch):
    monkeypatch.setattr("app.dependencies.verify_token", lambda token: (_ for _ in ()).throw(AccountDisabled()))
    dependency = require_admin

    with pytest.raises(HTTPException) as exc_info:
        dependency(SimpleNamespace(), access_token="token", db=None)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "ACCOUNT_DISABLED"
