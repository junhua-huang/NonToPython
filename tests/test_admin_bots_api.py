import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import auth_core
from app.core.config import Config
from app.database import Base, get_db
from app.dependencies import get_current_user
from app.models.models import AdminAuditLog, Role, User, UserRole
from app.routers import admin_bots

PREFIX = "/api/admin/bots"


def headers(user=1):
    token = jwt.encode({"sub": str(user)}, Config.JWT_SECRET_KEY, algorithm="HS256")
    return {"Authorization": "Bearer " + token}


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(Role(id=1, name="admin", label="Admin"))
        db.add(Role(id=2, name="bot", label="机器人", sort_order=1030))
        for identifier in range(1, 5):
            db.add(User(id=identifier, username=f"user{identifier}", email=f"user{identifier}@example.com",
                        password_hash="x", is_active=identifier != 4))
        db.flush()
        db.add_all([UserRole(user_id=identifier, role_id=1) for identifier in (1, 2, 4)])
        db.commit()
    monkeypatch.setattr(auth_core, "SessionLocal", factory)
    monkeypatch.setenv("MAX_BOT_ACCOUNTS", "10")
    application = FastAPI()
    application.include_router(admin_bots.router)

    def session():
        with factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    application.dependency_overrides[get_db] = session

    @application.get("/current")
    def current(user=Depends(get_current_user)):
        return user.id

    with TestClient(application, raise_server_exceptions=False) as client:
        yield client, factory
    engine.dispose()


def test_unauthenticated_401(setup):
    client, _ = setup
    assert client.get(PREFIX).status_code == 401
    assert client.post(f"{PREFIX}/provision-batch", json={}).status_code == 401


def test_non_admin_403(setup):
    client, _ = setup
    # user 3 无 admin 角色
    assert client.post(f"{PREFIX}/provision-batch", headers=headers(3),
                       json={"count": 1, "prefix": "bot", "reason": "test"}).status_code == 403


def test_inactive_admin_rejected(setup):
    client, _ = setup
    # user 4 inactive，虽有 admin 角色但 is_active=False
    assert client.post(f"{PREFIX}/provision-batch", headers=headers(4),
                       json={"count": 1, "prefix": "bot", "reason": "test"}).status_code == 403


def test_strict_fields(setup):
    client, _ = setup
    h = headers(1)
    # 未知字段 → 422
    assert client.post(f"{PREFIX}/provision-batch", headers=h,
                       json={"count": 1, "prefix": "bot", "reason": "x", "extra": 1}).status_code == 422
    # count 越界 → 422
    assert client.post(f"{PREFIX}/provision-batch", headers=h,
                       json={"count": 0, "prefix": "bot", "reason": "x"}).status_code == 422
    # 缺 reason → 422
    assert client.post(f"{PREFIX}/provision-batch", headers=h,
                       json={"count": 1, "prefix": "bot"}).status_code == 422
    # 前缀含非法字符 → 422
    assert client.post(f"{PREFIX}/provision-batch", headers=h,
                       json={"count": 1, "prefix": "BOT-X", "reason": "x"}).status_code == 422


def test_provision_success_and_once_only_password(setup):
    client, factory = setup
    h = headers(1)
    resp = client.post(f"{PREFIX}/provision-batch", headers=h,
                       json={"count": 3, "prefix": "bot", "reason": "course assignment"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["created"] == 3
    creds = body["credentials"]
    assert len(creds) == 3
    # 用户名/邮箱/密码字段齐全，且每个都分配 is_bot=True
    for c in creds:
        assert c["is_bot"] is True
        assert c["username"].startswith("bot_")
        assert c["email"].endswith("@bot.internal")
        assert c["password"]  # 一次性返回

    # 用户名唯一
    usernames = {c["username"] for c in creds}
    assert len(usernames) == 3

    # 数据库层面：is_bot=True，分配了 bot 角色
    with factory() as db:
        users = db.query(User).filter(User.is_bot == True).all()  # noqa: E712
        assert len(users) == 3
        for u in users:
            assert u.is_email_verified is True
            role_names = [ur.role.name for ur in u.user_roles if ur.role]
            assert "bot" in role_names
        # 审计记录存在，且不含明文密码
        logs = db.query(AdminAuditLog).filter(AdminAuditLog.action == "provision_bots").all()
        assert len(logs) == 1
        assert logs[0].metadata_json is not None
        assert creds[0]["password"] not in (logs[0].metadata_json or "")
        assert logs[0].action == "provision_bots"


def test_provision_limit_conflict(setup):
    client, factory = setup
    h = headers(1)
    # 已设 MAX_BOT_ACCOUNTS=10，先造 9 个，再申请 3 个会超上限 → 409
    assert client.post(f"{PREFIX}/provision-batch", headers=h,
                       json={"count": 9, "prefix": "bot", "reason": "x"}).status_code == 201
    resp = client.post(f"{PREFIX}/provision-batch", headers=h,
                       json={"count": 3, "prefix": "bot2", "reason": "x"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "bot_account_limit_reached"


def test_list_bots_no_password(setup):
    client, factory = setup
    h = headers(1)
    client.post(f"{PREFIX}/provision-batch", headers=h,
                json={"count": 2, "prefix": "bot", "reason": "x"})
    resp = client.get(PREFIX, headers=h)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert all("password" not in r for r in rows)
