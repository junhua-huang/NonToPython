import asyncio
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.models import Block, Conversation, Friendship, Like, Notification, Post, User
from app.routers import auth as auth_router
from app.routers import blocks as blocks_router
from app.routers import friends as friends_router
from app.routers import posts as posts_router
from app.routers import search as search_router
from app.services import notification_query_service
from app.services.notification_service import NotificationService
from app.services.recommendation_service import RecommendationService
from app.services.search_service import SearchService


@pytest.fixture()
def block_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def register_sqlite_functions(connection, _record):
        connection.create_function("month", 1, lambda value: int(str(value)[5:7]))
        connection.create_function("year", 1, lambda value: int(str(value)[:4]))
        connection.create_function("rand", 0, lambda: 0.5)

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    try:
        db.add_all([
            User(id=1, username="alice", email="alice@example.com", password_hash="x"),
            User(id=2, username="bob", email="bob@example.com", password_hash="x"),
            User(id=3, username="carol", email="carol@example.com", password_hash="x"),
        ])
        db.commit()
        yield db
    finally:
        db.close()
        engine.dispose()


def test_shared_block_helpers_are_bidirectional_and_db_authoritative(block_db):
    from app.services.block_service import excluded_user_ids, has_block_between

    block_db.add(Block(blocker_id=2, blocked_id=1))
    block_db.commit()

    with patch("app.ws_manager.ws_manager.get_blocked_user_ids", return_value=set()):
        assert has_block_between(block_db, 1, 2)
        assert has_block_between(block_db, 2, 1)
        assert excluded_user_ids(block_db, 1) == {2}
        assert excluded_user_ids(block_db, 2) == {1}


def test_block_creation_is_idempotent_and_atomically_removes_relationships(block_db):
    block_db.add_all([
        Friendship(sender_id=1, receiver_id=2, status="accepted"),
        Friendship(sender_id=2, receiver_id=1, status="pending"),
    ])
    block_db.commit()
    alice = block_db.get(User, 1)

    with (
        patch.object(blocks_router.ws_manager, "invalidate_blocked_cache") as invalidate,
        patch.object(blocks_router.ws_manager, "invalidate_user_caches") as invalidate_user,
    ):
        first = blocks_router.block_user(payload={"user_id": 2}, user=alice, db=block_db)
        block_db.add(Friendship(sender_id=1, receiver_id=2, status="pending"))
        block_db.commit()
        second = blocks_router.block_user(payload={"user_id": 2}, user=alice, db=block_db)

    assert first["block"]["blocked_id"] == 2
    assert second["block"]["blocked_id"] == 2
    assert block_db.query(Block).filter_by(blocker_id=1, blocked_id=2).count() == 1
    assert block_db.query(Friendship).filter(
        ((Friendship.sender_id == 1) & (Friendship.receiver_id == 2))
        | ((Friendship.sender_id == 2) & (Friendship.receiver_id == 1))
    ).count() == 0
    assert {call.args[0] for call in invalidate.call_args_list} == {1, 2}
    assert {call.args[0] for call in invalidate_user.call_args_list} == {1, 2}


def test_block_creation_evicts_only_the_affected_direct_room(block_db, monkeypatch):
    block_db.add_all([
        Conversation(id=1, user1_id=1, user2_id=2, type="direct"),
        Conversation(id=2, user1_id=1, user2_id=3, type="direct"),
    ])
    block_db.commit()
    alice = block_db.get(User, 1)
    rooms = {1: {1, 2}, 2: {1, 3}, 99: {2, 3}}
    monkeypatch.setattr(blocks_router.ws_manager, "_conversation_users", rooms)

    blocks_router.block_user(payload={"user_id": 2}, user=alice, db=block_db)

    assert rooms == {2: {1, 3}, 99: {2, 3}}


def test_block_creation_rolls_back_block_and_relationship_cleanup_together(block_db, monkeypatch):
    block_db.add(Friendship(sender_id=1, receiver_id=2, status="accepted"))
    block_db.commit()
    alice = block_db.get(User, 1)

    original_commit = block_db.commit

    def fail_commit():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(block_db, "commit", fail_commit)
    with pytest.raises(HTTPException) as exc_info:
        blocks_router.block_user(payload={"user_id": 2}, user=alice, db=block_db)
    monkeypatch.setattr(block_db, "commit", original_commit)

    assert exc_info.value.status_code == 500
    assert block_db.query(Block).count() == 0
    assert block_db.query(Friendship).count() == 1


def test_profile_search_suggestions_and_friend_recommendations_hide_reverse_block(block_db):
    block_db.add(Block(blocker_id=2, blocked_id=1))
    block_db.commit()
    alice = block_db.get(User, 1)

    with pytest.raises(HTTPException) as exc_info:
        auth_router.get_user_info(user_id=2, db=block_db, current_user=alice)
    assert exc_info.value.status_code == 404

    assert SearchService.search_users(block_db, "bob", current_user_id=1)["users"] == []
    assert SearchService.suggest_users(block_db, "bo", current_user_id=1) == []
    recommendations = friends_router.get_friend_recommendations(user=alice, db=block_db)
    assert {item["id"] for item in recommendations["recommendations"]} == {3}


def test_smart_recommendations_and_mentions_hide_reverse_block_with_stale_cache(block_db):
    block_db.add_all([
        Block(blocker_id=2, blocked_id=1),
        Friendship(sender_id=1, receiver_id=3, status="accepted"),
        Friendship(sender_id=2, receiver_id=3, status="accepted"),
    ])
    block_db.commit()
    alice = block_db.get(User, 1)

    with patch("app.ws_manager.ws_manager.get_blocked_user_ids", return_value=set()):
        assert RecommendationService._get_blocked_feed_author_ids(block_db, 1) == {2}
        suggested = RecommendationService.get_suggested_users(block_db, 1)
        smart = RecommendationService.get_friend_recommendations(block_db, 1)
        mentions = search_router.get_mention_suggestions(
            prefix="bo", limit=5, user=alice, db=block_db,
        )

    assert {item["id"] for item in suggested["suggestions"]} == set()
    assert 2 not in {item["id"] for item in smart["recommendations"]}
    assert mentions == {"suggestions": [], "total": 0}


def test_friend_operations_are_hidden_or_denied_for_reverse_block(block_db):
    block_db.add_all([
        Block(blocker_id=2, blocked_id=1),
        Friendship(sender_id=2, receiver_id=1, status="pending"),
    ])
    block_db.commit()
    alice = block_db.get(User, 1)
    friendship = block_db.query(Friendship).one()

    with pytest.raises(HTTPException) as send_error:
        friends_router.send_friend_request(payload={"receiver_id": 2}, user=alice, db=block_db)
    assert send_error.value.status_code in {403, 404}

    with pytest.raises(HTTPException) as accept_error:
        friends_router.accept_friend_request(request_id=friendship.id, user=alice, db=block_db)
    assert accept_error.value.status_code == 404

    pending = friends_router.get_pending_requests(user=alice, db=block_db)
    assert pending["received"] == []
    assert pending["sent"] == []
    assert friends_router.check_friendship_status(user_id=2, user=alice, db=block_db) == {"status": "none"}
    assert friends_router.get_sent_requests(user=alice, db=block_db) == {"requests": [], "total": 0}
    assert friends_router.get_received_requests(user=alice, db=block_db) == {"requests": [], "total": 0}
    with pytest.raises(HTTPException) as cancel_error:
        friends_router.cancel_friend_request(request_id=friendship.id, user=alice, db=block_db)
    assert cancel_error.value.status_code == 404
    with pytest.raises(HTTPException) as count_error:
        friends_router.get_user_friend_count(user_id=2, user=alice, db=block_db)
    assert count_error.value.status_code == 404


def test_blocked_user_activity_feeds_are_hidden_bidirectionally(block_db):
    block_db.add_all([
        Block(blocker_id=2, blocked_id=1),
        Post(id=1, user_id=2, content="hidden author", visibility="public", is_public=True),
        Post(id=2, user_id=3, content="liked", visibility="public", is_public=True),
        Like(user_id=2, post_id=2),
    ])
    block_db.commit()
    alice = block_db.get(User, 1)

    with patch("app.ws_manager.ws_manager.get_blocked_user_ids", return_value=set()):
        authored = posts_router.get_user_posts(user_id=2, page=1, per_page=20, user=alice, db=block_db)
        liked = posts_router.get_user_liked_posts(user_id=2, page=1, per_page=20, user=alice, db=block_db)

    assert authored["posts"] == []
    assert liked["posts"] == []


def test_notifications_hide_sender_when_sender_blocked_recipient_and_keep_system(block_db):
    block_db.add_all([
        Block(blocker_id=2, blocked_id=1),
        Notification(id=1, user_id=1, sender_id=2, notification_type="mention", title="hidden"),
        Notification(id=2, user_id=1, sender_id=None, notification_type="system", title="system"),
    ])
    block_db.commit()

    visible = notification_query_service.visible_notification_query(block_db, 1).all()
    assert [item.id for item in visible] == [2]
    assert notification_query_service.visible_unread_count(block_db, 1) == 1


def test_sender_backed_notification_is_not_persisted_across_block(block_db, monkeypatch):
    block_db.add(Block(blocker_id=2, blocked_id=1))
    block_db.commit()
    websocket_push = patch.object(NotificationService, "_push_new_notification")
    mobile_push = patch("app.services.notification_service.AliyunPushService.schedule_notification_push")

    with websocket_push as ws_push, mobile_push as push:
        result = NotificationService.create_notification(
            user_id=1,
            sender_id=2,
            notification_type="mention",
            title="hidden",
            content="hidden",
            db=block_db,
        )

    assert result is None
    assert block_db.query(Notification).count() == 0
    ws_push.assert_not_called()
    push.assert_not_called()


def test_unblock_is_idempotent_and_does_not_restore_friendship(block_db):
    block_db.add(Block(blocker_id=1, blocked_id=2))
    block_db.commit()
    alice = block_db.get(User, 1)

    with (
        patch.object(blocks_router.ws_manager, "invalidate_blocked_cache") as invalidate_blocked,
        patch.object(blocks_router.ws_manager, "invalidate_user_caches") as invalidate_user,
    ):
        first = blocks_router.unblock_user(user_id=2, user=alice, db=block_db)
        second = blocks_router.unblock_user(user_id=2, user=alice, db=block_db)

    assert first == {"message": "User unblocked successfully"}
    assert second == {"message": "User already unblocked"}
    assert block_db.query(Friendship).count() == 0
    assert {call.args[0] for call in invalidate_blocked.call_args_list} == {1, 2}
    assert {call.args[0] for call in invalidate_user.call_args_list} == {1, 2}


def test_post_visibility_is_bidirectional_even_with_stale_empty_ws_cache(block_db):
    from app.services.post_visibility_service import load_visible_post

    block_db.add_all([
        Block(blocker_id=2, blocked_id=1),
        Post(id=1, user_id=2, content="hidden", visibility="public", is_public=True),
    ])
    block_db.commit()

    with patch("app.ws_manager.ws_manager.get_blocked_user_ids", return_value=set()):
        assert load_visible_post(block_db, 1, 1) is None
