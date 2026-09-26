import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.models import Block, Comment, Like, Post, User
from app.routers import interactions


@pytest.fixture()
def interaction_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        db.add_all([
            User(id=1, username="viewer", email="viewer@example.com", password_hash="x"),
            User(id=2, username="blocked", email="blocked@example.com", password_hash="x"),
            User(id=3, username="visible", email="visible@example.com", password_hash="x"),
            Post(id=1, user_id=3, content="public", visibility="public", is_public=True),
            Comment(id=1, post_id=1, user_id=2, content="hidden"),
            Comment(id=2, post_id=1, user_id=3, content="visible"),
            Like(id=1, post_id=1, user_id=2),
            Like(id=2, post_id=1, user_id=3),
            Block(blocker_id=2, blocked_id=1),
        ])
        db.commit()
        yield db
    finally:
        db.close()
        engine.dispose()


def test_comment_and_liker_lists_hide_reverse_blocked_users(interaction_db):
    viewer = interaction_db.get(User, 1)

    comments = interactions.get_comments(
        post_id=1, page=1, per_page=20, parent_id=None, user=viewer, db=interaction_db,
    )
    likes = interactions.get_post_likes(post_id=1, user=viewer, db=interaction_db)

    assert [item["id"] for item in comments["comments"]] == [2]
    assert [item["user_id"] for item in likes["likes"]] == [3]


def test_blocked_comment_detail_and_interactions_are_hidden(interaction_db):
    viewer = interaction_db.get(User, 1)

    calls = [
        lambda: interactions.get_comment_detail(comment_id=1, page=1, per_page=20, user=viewer, db=interaction_db),
        lambda: interactions.like_comment(comment_id=1, user=viewer, db=interaction_db),
        lambda: interactions.create_comment(
            post_id=1,
            payload={"content": "reply", "parent_id": 1, "reply_to_user_id": 2},
            user=viewer,
            db=interaction_db,
        ),
    ]
    for call in calls:
        with pytest.raises(HTTPException) as exc_info:
            call()
        assert exc_info.value.status_code == 404


def test_blocked_parent_comment_reply_page_is_hidden(interaction_db):
    viewer = interaction_db.get(User, 1)

    with pytest.raises(HTTPException) as exc_info:
        interactions.get_comments(
            post_id=1,
            page=1,
            per_page=20,
            parent_id=1,
            user=viewer,
            db=interaction_db,
        )

    assert exc_info.value.status_code == 404
