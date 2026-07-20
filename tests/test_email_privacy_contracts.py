import ast
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.models import (
    Block,
    Comment,
    ComicComment,
    Conversation,
    ConversationParticipant,
    Like,
    Message,
    Post,
    RoleApplication,
    User,
)
from app.routers import auth as auth_router
from app.routers import communities as communities_router
from app.routers import ws as ws_router
from app.services.search_service import SearchService


ROOT = Path(__file__).resolve().parents[1]


def serialize_user_card(user):
    from app.serializers.user import serialize_user_card as serializer

    return serializer(user)


def serialize_user_profile(user, viewer_user_id):
    from app.serializers.user import serialize_user_profile as serializer

    return serializer(user, viewer_user_id=viewer_user_id)


def serialize_user_self(user):
    from app.serializers.user import serialize_user_self as serializer

    return serializer(user)


@pytest.fixture()
def privacy_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    try:
        db.add_all(
            [
                User(
                    id=1,
                    username="viewer",
                    email="viewer@self.test",
                    password_hash="x",
                    show_email=False,
                ),
                User(
                    id=2,
                    username="hiddenuser",
                    email="secret.local@hidden.example",
                    password_hash="x",
                    show_email=False,
                ),
                User(
                    id=3,
                    username="nulluser",
                    email="null.local@null.example",
                    password_hash="x",
                    show_email=None,
                ),
                User(
                    id=4,
                    username="visibleuser",
                    email="visible.local@visible.example",
                    password_hash="x",
                    show_email=True,
                ),
            ]
        )
        db.commit()
        yield db
    finally:
        db.close()
        engine.dispose()


def assert_no_email_key(value):
    if isinstance(value, dict):
        assert "email" not in value
        for child in value.values():
            assert_no_email_key(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            assert_no_email_key(child)


@pytest.mark.parametrize("show_email", [True, False, None])
def test_user_serializer_matrix_has_safe_card_and_complete_self_scope(show_email):
    user = User(
        id=10,
        username="matrix",
        email="matrix@example.test",
        password_hash="x",
        show_email=show_email,
    )

    assert "email" not in serialize_user_card(user)
    assert "show_email" not in serialize_user_card(user)
    assert "email" not in user.to_dict()
    assert "show_email" not in user.to_dict()

    own_payload = serialize_user_self(user)
    assert own_payload["email"] == "matrix@example.test"
    assert own_payload["show_email"] is (show_email is True)


@pytest.mark.parametrize(
    ("show_email", "viewer_user_id", "has_email"),
    [
        (True, 99, True),
        (False, 99, False),
        (None, 99, False),
        (False, 10, True),
        (None, 10, True),
    ],
)
def test_user_profile_serializer_reveals_email_only_when_allowed(
    show_email, viewer_user_id, has_email
):
    user = User(
        id=10,
        username="profile",
        email="profile@example.test",
        password_hash="x",
        show_email=show_email,
    )

    payload = serialize_user_profile(user, viewer_user_id=viewer_user_id)

    assert payload["show_email"] is (show_email is True)
    assert ("email" in payload) is has_email


@pytest.mark.parametrize("show_email", [True, False, None])
def test_auth_self_endpoints_always_return_email_and_show_email(show_email):
    user = User(
        id=10,
        username="self",
        email="self@example.test",
        password_hash="x",
        show_email=show_email,
    )

    for payload in (
        auth_router.get_current_user_info_me(user=user),
        auth_router.get_current_user_info(user=user),
        auth_router._build_user_response(user),
    ):
        assert payload["email"] == "self@example.test"
        assert payload["show_email"] is (show_email is True)


@pytest.mark.parametrize(
    ("target_id", "has_email", "show_email"),
    [(2, False, False), (3, False, False), (4, True, True), (1, True, False)],
)
def test_public_profile_endpoint_applies_email_policy(
    privacy_db, target_id, has_email, show_email
):
    viewer = privacy_db.get(User, 1)

    payload = auth_router.get_user_info(
        user_id=target_id,
        db=privacy_db,
        current_user=viewer,
    )

    assert payload["show_email"] is show_email
    assert ("email" in payload) is has_email


def test_public_profile_endpoint_preserves_bidirectional_block_behavior(privacy_db):
    privacy_db.add(Block(blocker_id=2, blocked_id=1))
    privacy_db.commit()

    with pytest.raises(HTTPException) as exc_info:
        auth_router.get_user_info(
            user_id=2,
            db=privacy_db,
            current_user=privacy_db.get(User, 1),
        )

    assert exc_info.value.status_code == 404


@pytest.mark.parametrize(
    "query", ["secret.local@hidden.example", "secret.local", "hidden.example"]
)
def test_hidden_email_cannot_be_discovered_or_counted(privacy_db, query):
    result = SearchService.search_users(
        privacy_db, query, current_user_id=1
    )

    assert result["users"] == []
    assert result["total"] == 0
    assert result["pages"] == 0


@pytest.mark.parametrize(
    "query", ["visible.local@visible.example", "visible.local", "visible.example"]
)
def test_visible_email_can_match_but_search_cards_never_include_email(privacy_db, query):
    result = SearchService.search_users(
        privacy_db, query, current_user_id=1
    )

    assert [user["id"] for user in result["users"]] == [4]
    assert result["total"] == 1
    assert_no_email_key(result)


def test_username_search_is_unchanged_for_hidden_email_user(privacy_db):
    result = SearchService.search_users(
        privacy_db, "hiddenuser", current_user_id=1
    )

    assert [user["id"] for user in result["users"]] == [2]
    assert result["total"] == 1
    assert_no_email_key(result)


def test_representative_model_trees_never_leak_nested_user_email(privacy_db):
    viewer = privacy_db.get(User, 1)
    hidden = privacy_db.get(User, 2)
    visible = privacy_db.get(User, 4)
    post = Post(id=10, user_id=2, content="anonymous-safe", author=hidden)
    comment = Comment(
        id=11,
        user_id=2,
        post_id=10,
        content="reply",
        author=hidden,
        reply_to_user=visible,
    )
    like = Like(id=12, user_id=4, post_id=10, user=visible)
    block = Block(id=13, blocker_id=1, blocked_id=2, blocker=viewer, blocked=hidden)
    comic_comment = ComicComment(
        id=14,
        user_id=2,
        event_id=20,
        content="comic reply",
        author=hidden,
        reply_to_user=visible,
    )

    payloads = [
        post.to_dict(like_count=0, comment_count=0, topics=[]),
        comment.to_dict(),
        like.to_dict(),
        block.to_dict(),
        comic_comment.to_dict(),
    ]

    for payload in payloads:
        assert_no_email_key(payload)


def test_community_message_sender_and_ws_session_cache_never_leak_email(privacy_db):
    viewer = privacy_db.get(User, 1)
    hidden = privacy_db.get(User, 2)
    conversation = Conversation(id=30, user1_id=1, user2_id=2, type="direct")
    conversation.participants = [
        ConversationParticipant(user_id=1),
        ConversationParticipant(user_id=2),
    ]
    message = Message(
        id=31,
        conversation=conversation,
        sender=hidden,
        sender_id=2,
        content="hello",
    )
    privacy_db.add_all([conversation, message])
    privacy_db.commit()

    community_payload = communities_router._community_message_to_dict(message)
    session_payload = ws_router._build_session_list(privacy_db, viewer.id)

    assert_no_email_key(community_payload)
    assert_no_email_key(session_payload)


def test_role_application_defaults_safe_but_private_scope_includes_applicant_email():
    applicant = User(
        id=50,
        username="applicant",
        email="applicant@example.test",
        password_hash="x",
        show_email=False,
    )
    application = RoleApplication(id=60, user_id=50, role_id=1, user=applicant)

    safe_payload = application.to_dict()
    private_payload = application.to_dict(include_private_user=True)

    assert_no_email_key(safe_payload)
    assert private_payload["user"]["email"] == "applicant@example.test"
    assert private_payload["user"]["show_email"] is False


def test_role_application_routes_request_private_applicant_scope_explicitly():
    roles_source = (ROOT / "app" / "routers" / "roles.py").read_text(
        encoding="utf-8"
    )
    admin_source = (ROOT / "app" / "routers" / "admin.py").read_text(
        encoding="utf-8"
    )

    assert "application.to_dict(include_private_user=True)" in roles_source
    assert "[a.to_dict(include_private_user=True) for a in applications]" in roles_source
    assert "app.to_dict(include_private_user=True)" in roles_source
    assert "[a.to_dict(include_private_user=True) for a in applications]" in admin_source
    assert "app.to_dict(include_private_user=True)" in admin_source


def test_raw_user_email_projection_is_centralized_in_user_serializer():
    violations = []
    for path in (ROOT / "app").rglob("*.py"):
        if path == ROOT / "app" / "serializers" / "user.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError, ValueError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "email"
                    and isinstance(value, ast.Attribute)
                    and value.attr == "email"
                ):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    assert violations == []
