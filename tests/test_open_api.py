import bcrypt as bcrypt_lib
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import auth_core
from app.database import Base, get_db
from app.models.models import Post, Role, Topic, User, UserRole
from app.routers import open_api


PREFIX = "/nontoOpenApi"


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    password_hash = bcrypt_lib.hashpw(b"Password123", bcrypt_lib.gensalt()).decode("utf-8")
    with factory() as db:
        db.add(Role(id=1, name="user", label="User"))
        db.add(
            User(
                id=1,
                username="existing",
                email="existing@qq.com",
                password_hash=password_hash,
                is_active=True,
                is_email_verified=True,
            )
        )
        db.add(
            Post(
                id=1,
                content="public hello",
                user_id=1,
                visibility="public",
                is_public=True,
            )
        )
        db.add(Topic(id=1, name="漫展", description="漫展讨论", color="#3b82f6"))
        db.commit()
    monkeypatch.setattr(auth_core, "SessionLocal", factory)
    monkeypatch.setattr(open_api, "moderate_route_fields", lambda *args, **kwargs: None)
    monkeypatch.setattr(open_api, "_moderate_auth_fields", lambda *args, **kwargs: None)
    monkeypatch.setattr(open_api.TopicService, "auto_link_topics", lambda *args, **kwargs: None)
    monkeypatch.setattr(open_api.MentionService, "process_mentions", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "app.routers.interactions.moderation_service.moderate_fields",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("app.routers.interactions.moderate_route_fields", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.notification_service.NotificationService.notify_like", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.notification_service.NotificationService.notify_comment", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.mention_service.MentionService.process_mentions", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.routers.topics._moderate_topic_fields", lambda *args, **kwargs: None)

    application = FastAPI()
    application.include_router(open_api.router, prefix=PREFIX)

    def session():
        with factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    application.dependency_overrides[get_db] = session
    with TestClient(application, raise_server_exceptions=False) as client:
        yield client, factory
    engine.dispose()


def test_index_describes_open_api(setup):
    client, _ = setup
    html = client.get(PREFIX, headers={"Accept": "text/html"})
    assert html.status_code == 200
    assert "text/html" in html.headers["content-type"]
    assert "南图 Open API" in html.text
    assert "/nontoOpenApi/register" in html.text

    catalog = client.get(PREFIX, headers={"Accept": "application/json"})
    assert catalog.status_code == 200
    body = catalog.json()
    assert body["ai_account_clusters_allowed"] is True
    assert "高质量" in body["ai_account_policy"]
    assert body["endpoints"]["update_profile"] == "PUT /nontoOpenApi/profile"
    assert body["endpoints"]["list_topics"] == "GET /nontoOpenApi/topics"
    assert "AI 账号集群" in html.text
    assert "/nontoOpenApi/topics" in html.text
    assert client.get(f"{PREFIX}/meta").json()["docs"] == "GET /nontoOpenApi"


def test_register_requires_email_but_not_otp(setup):
    client, factory = setup
    missing = client.post(
        f"{PREFIX}/register",
        json={"username": "openapi_user", "password": "Password123"},
    )
    assert missing.status_code == 422

    response = client.post(
        f"{PREFIX}/register",
        json={
            "username": "openapi_user",
            "password": "Password123",
            "email": "openapi_user@gmail.com",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["username"] == "openapi_user"
    assert body["user"]["email"] == "openapi_user@gmail.com"
    assert "email_code" not in body
    with factory() as db:
        user = db.query(User).filter(User.username == "openapi_user").first()
        assert user is not None
        assert user.is_email_verified is False
        assert user.email == "openapi_user@gmail.com"
        role = db.query(UserRole).filter(UserRole.user_id == user.id).first()
        assert role is not None


def test_register_accepts_non_qq_email_without_otp(setup):
    client, _ = setup
    response = client.post(
        f"{PREFIX}/register",
        json={
            "username": "gmail_user",
            "password": "Password123",
            "email": "user@gmail.com",
        },
    )
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "user@gmail.com"


def test_login_without_otp(setup):
    client, _ = setup
    response = client.post(
        f"{PREFIX}/login",
        json={"login": "existing", "password": "Password123"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"]
    me = client.get(
        f"{PREFIX}/me",
        headers={"Authorization": f"Bearer {response.json()['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["username"] == "existing"


def test_login_rejects_bad_password(setup):
    client, _ = setup
    response = client.post(
        f"{PREFIX}/login",
        json={"login": "existing", "password": "wrong-password"},
    )
    assert response.status_code == 401


def test_posts_require_token_and_create_text_post(setup):
    client, factory = setup
    assert client.get(f"{PREFIX}/posts").status_code == 401
    token = client.post(
        f"{PREFIX}/login",
        json={"email": "existing@qq.com", "password": "Password123"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    listing = client.get(f"{PREFIX}/posts", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["posts"][0]["content"] == "public hello"

    created = client.post(
        f"{PREFIX}/posts",
        headers=headers,
        json={"content": "hello from open api"},
    )
    assert created.status_code == 200
    post = created.json()["post"]
    assert post["content"] == "hello from open api"
    assert post["visibility"] == "public"
    with factory() as db:
        stored = db.query(Post).filter(Post.content == "hello from open api").first()
        assert stored is not None
        assert stored.user_id == 1


def _auth_headers(client):
    token = client.post(
        f"{PREFIX}/login",
        json={"email": "existing@qq.com", "password": "Password123"},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_like_comment_reply_and_repost(setup):
    client, factory = setup
    headers = _auth_headers(client)

    liked = client.post(f"{PREFIX}/posts/1/like", headers=headers)
    assert liked.status_code == 200
    assert liked.json()["liked"] is True

    comment = client.post(
        f"{PREFIX}/posts/1/comments",
        headers=headers,
        json={"content": "first comment"},
    )
    assert comment.status_code == 200
    comment_id = comment.json()["comment"]["id"]
    assert comment.json()["comment"]["content"] == "first comment"

    reply = client.post(
        f"{PREFIX}/posts/1/comments",
        headers=headers,
        json={"content": "a reply", "parent_id": comment_id},
    )
    assert reply.status_code == 200
    assert reply.json()["comment"]["parent_id"] == comment_id

    comment_like = client.post(f"{PREFIX}/comments/{comment_id}/like", headers=headers)
    assert comment_like.status_code == 200
    assert comment_like.json()["liked"] is True

    listing = client.get(f"{PREFIX}/posts/1/comments", headers=headers)
    assert listing.status_code == 200
    comments = listing.json()["comments"]
    assert comments[0]["content"] == "first comment"
    assert comments[0]["replies"][0]["content"] == "a reply"

    repost = client.post(f"{PREFIX}/posts/1/repost", headers=headers, json={"content": "forwarding"})
    assert repost.status_code == 200
    body = repost.json()["post"]
    assert body["quoted_post_id"] == 1
    assert body["content"] == "forwarding"

    unliked = client.delete(f"{PREFIX}/posts/1/like", headers=headers)
    assert unliked.status_code == 200
    assert unliked.json()["liked"] is False

    with factory() as db:
        stored_repost = db.query(Post).filter(Post.quoted_post_id == 1).first()
        assert stored_repost is not None


def test_profile_and_topics(setup):
    client, factory = setup
    headers = _auth_headers(client)

    updated = client.put(
        f"{PREFIX}/profile",
        headers=headers,
        json={"display_name": "南图用户", "bio": "喜欢漫展"},
    )
    assert updated.status_code == 200
    assert updated.json()["user"]["username"] == "南图用户"
    assert "喜欢漫展" in updated.json()["user"]["bio"]

    listing = client.get(f"{PREFIX}/topics", headers=headers)
    assert listing.status_code == 200
    created = client.post(
        f"{PREFIX}/topics",
        headers=headers,
        json={"name": "摄影", "description": "摄影交流"},
    )
    assert created.status_code == 200
    topic_id = created.json()["topic"]["id"]
    followed = client.post(f"{PREFIX}/topics/{topic_id}/follow", headers=headers)
    assert followed.status_code == 200
    by_name = client.get(f"{PREFIX}/topics/name/摄影", headers=headers)
    assert by_name.status_code == 200
    assert by_name.json()["name"] == "摄影"
    with factory() as db:
        assert db.query(Topic).filter(Topic.name == "摄影").first() is not None
