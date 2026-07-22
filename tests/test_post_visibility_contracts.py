import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.models import (
    Block,
    Comment,
    Conversation,
    ConversationParticipant,
    Friendship,
    Like,
    Message,
    Post,
    User,
    post_visibility,
)
from app.routers import posts as posts_router


@pytest.fixture(autouse=True)
def approve_route_moderation(monkeypatch):
    from app.routers import chat as chat_router
    from app.routers import communities as communities_router
    from app.routers import interactions as interactions_router
    from app.routers import ws as ws_router

    monkeypatch.setattr(posts_router, "moderate_route_fields", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_router, "moderate_route_fields", lambda *args, **kwargs: None)
    monkeypatch.setattr(communities_router, "moderate_route_fields", lambda *args, **kwargs: None)
    monkeypatch.setattr(interactions_router, "moderate_route_fields", lambda *args, **kwargs: None)
    monkeypatch.setattr(ws_router, "moderate_route_fields", lambda *args, **kwargs: None)


@pytest.fixture()
def post_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        users = [
            User(id=1, username="viewer", email="viewer@example.com", password_hash="x"),
            User(id=2, username="friend", email="friend@example.com", password_hash="x"),
            User(id=3, username="pending", email="pending@example.com", password_hash="x"),
            User(id=4, username="stranger", email="stranger@example.com", password_hash="x"),
        ]
        session.add_all(users)
        session.add_all([
            Friendship(sender_id=1, receiver_id=2, status="accepted"),
            Friendship(sender_id=3, receiver_id=1, status="pending"),
        ])
        session.commit()
        yield session
    finally:
        session.close()
        engine.dispose()


def _add_post(db, author_id, visibility, content=None, is_public=None):
    post = Post(
        content=content or f"{visibility}-{author_id}",
        user_id=author_id,
        visibility=visibility,
        is_public=(visibility == "public") if is_public is None else is_public,
    )
    db.add(post)
    db.commit()
    return post


def _visibility_service():
    from app.services import post_visibility_service

    return post_visibility_service


def test_visibility_write_normalization_accepts_only_canonical_modes_and_legacy_alias():
    service = _visibility_service()

    assert service.normalize_post_visibility("public") == "public"
    assert service.normalize_post_visibility("friends") == "friends"
    assert service.normalize_post_visibility("friends_only") == "friends"

    for rejected in ("private", "custom", "followers", "", None):
        with pytest.raises(ValueError):
            service.normalize_post_visibility(rejected)


def test_visible_user_ids_custom_audiences_are_rejected():
    service = _visibility_service()

    for audience in ("2", "2,3", [2], {2}):
        with pytest.raises(ValueError):
            service.validate_post_visibility_write("public", audience)


def test_query_predicate_enforces_legacy_visibility_friendship_and_ownership(post_db):
    service = _visibility_service()
    public = _add_post(post_db, 4, "public")
    friend = _add_post(post_db, 2, "friends")
    friend_alias = _add_post(post_db, 2, "friends_only")
    pending_friend = _add_post(post_db, 3, "friends")
    private = _add_post(post_db, 2, "private", is_public=True)
    custom = _add_post(post_db, 2, "custom", is_public=True)
    arbitrary = _add_post(post_db, 2, "followers", is_public=True)
    own_private = _add_post(post_db, 1, "private")
    own_custom = _add_post(post_db, 1, "custom")

    visible_ids = {
        post.id
        for post in post_db.query(Post)
        .filter(service.post_visibility_predicate(1))
        .all()
    }

    assert visible_ids == {public.id, friend.id, friend_alias.id, own_private.id, own_custom.id}
    assert pending_friend.id not in visible_ids
    assert private.id not in visible_ids
    assert custom.id not in visible_ids
    assert arbitrary.id not in visible_ids


def test_legacy_custom_audience_rows_do_not_grant_access(post_db):
    service = _visibility_service()
    custom = _add_post(post_db, 4, "custom", is_public=False)
    post_db.execute(post_visibility.insert().values(post_id=custom.id, user_id=1))
    post_db.commit()

    assert not service.can_view_post(post_db, custom, 1)
    assert service.can_view_post(post_db, custom, 4)


def test_public_posts_respect_blocks_but_author_still_sees_own(post_db):
    service = _visibility_service()
    public = _add_post(post_db, 4, "public")
    own = _add_post(post_db, 1, "public")
    post_db.add(Block(blocker_id=4, blocked_id=1))
    post_db.commit()

    visible_ids = {
        post.id
        for post in post_db.query(Post)
        .filter(service.post_visibility_predicate(1))
        .all()
    }

    assert public.id not in visible_ids
    assert own.id in visible_ids
    assert not service.can_view_post(post_db, public, 1)
    assert service.can_view_post(post_db, own, 1)


def test_anonymous_viewers_see_only_public_posts(post_db):
    service = _visibility_service()
    public = _add_post(post_db, 4, "public")
    friend = _add_post(post_db, 2, "friends")
    private = _add_post(post_db, 2, "private", is_public=True)

    visible_ids = {
        post.id
        for post in post_db.query(Post)
        .filter(service.post_visibility_predicate(None))
        .all()
    }

    assert visible_ids == {public.id}
    assert service.can_view_post(post_db, public, None)
    assert not service.can_view_post(post_db, friend, None)
    assert not service.can_view_post(post_db, private, None)


def test_create_ignores_content_category_and_normalizes_friends_only(post_db):
    user = post_db.get(User, 1)

    with (
        patch.object(posts_router.TopicService, "auto_link_topics"),
        patch.object(posts_router.MentionService, "process_mentions"),
    ):
        response = asyncio.run(posts_router.create_post(
            image=None,
            video=None,
            content="new post",
            image_urls=None,
            video_url_input=None,
            content_category="cosplay",
            display_role_type=None,
            visibility="friends_only",
            visible_user_ids=None,
            community_id=None,
            community_only=False,
            user=user,
            db=post_db,
        ))

    created = post_db.query(Post).filter(Post.content == "new post").one()
    assert created.visibility == "friends"
    assert created.is_public is False
    assert created.content_category is None
    assert response["post"]["visibility"] == "friends"
    assert response["post"].get("content_category") is None


@pytest.mark.parametrize("visibility", ["private", "custom", "followers", ""])
def test_create_rejects_noncanonical_visibility(post_db, visibility):
    user = post_db.get(User, 1)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(posts_router.create_post(
            image=None,
            video=None,
            content="rejected post",
            image_urls=None,
            video_url_input=None,
            content_category=None,
            display_role_type=None,
            visibility=visibility,
            visible_user_ids=None,
            community_id=None,
            community_only=False,
            user=user,
            db=post_db,
        ))

    assert exc_info.value.status_code == 422
    assert post_db.query(Post).filter(Post.content == "rejected post").count() == 0


def test_create_rejects_visible_user_ids_even_with_public_visibility(post_db):
    user = post_db.get(User, 1)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(posts_router.create_post(
            image=None,
            video=None,
            content="custom audience",
            image_urls=None,
            video_url_input=None,
            content_category=None,
            display_role_type=None,
            visibility="public",
            visible_user_ids="2,3",
            community_id=None,
            community_only=False,
            user=user,
            db=post_db,
        ))

    assert exc_info.value.status_code == 422
    assert post_db.query(Post).filter(Post.content == "custom audience").count() == 0


def test_update_normalizes_visibility_and_ignores_content_category(post_db):
    post = _add_post(post_db, 1, "public")
    post.content_category = "legacy-category"
    post_db.commit()
    user = post_db.get(User, 1)

    response = posts_router.update_post(
        post.id,
        payload={"visibility": "friends_only", "content_category": "new-category"},
        user=user,
        db=post_db,
    )

    post_db.refresh(post)
    assert post.visibility == "friends"
    assert post.is_public is False
    assert post.content_category == "legacy-category"
    assert response["post"]["content_category"] == "legacy-category"


@pytest.mark.parametrize("payload", [
    {"visibility": "private"},
    {"visibility": "custom"},
    {"visibility": "followers"},
    {"visibility": "public", "visible_user_ids": [2]},
    {"is_public": False},
])
def test_update_rejects_legacy_or_custom_visibility_writes(post_db, payload):
    post = _add_post(post_db, 1, "public")
    user = post_db.get(User, 1)

    with pytest.raises(HTTPException) as exc_info:
        posts_router.update_post(post.id, payload=payload, user=user, db=post_db)

    assert exc_info.value.status_code == 422
    post_db.refresh(post)
    assert post.visibility == "public"
    assert post.is_public is True


def test_legacy_response_fields_remain_compatible():
    post = Post(
        id=9,
        content="legacy",
        user_id=1,
        visibility="custom",
        is_public=False,
        content_category="cosplay",
    )

    payload = post.to_dict(like_count=0, comment_count=0, topics=[], is_liked=False)

    assert payload["visibility"] == "custom"
    assert payload["is_public"] is False
    assert payload["content_category"] == "cosplay"


def test_detail_and_post_metadata_routes_fail_closed_for_invisible_posts(post_db):
    from app.routers import interactions as interactions_router

    private = _add_post(post_db, 4, "private", is_public=True)
    viewer = post_db.get(User, 1)
    calls = [
        lambda: posts_router.get_post(private.id, user=viewer, db=post_db),
        lambda: posts_router.record_post_view(private.id, user=viewer, db=post_db),
        lambda: posts_router.get_post_stats(private.id, user=viewer, db=post_db),
        lambda: interactions_router.like_post(private.id, user=viewer, db=post_db),
        lambda: interactions_router.get_post_likes(private.id, user=viewer, db=post_db),
        lambda: interactions_router.create_comment(
            private.id,
            payload={"content": "must not be created"},
            user=viewer,
            db=post_db,
        ),
        lambda: interactions_router.get_comments(private.id, user=viewer, db=post_db),
    ]

    for call in calls:
        with pytest.raises(HTTPException) as exc_info:
            call()
        assert exc_info.value.status_code == 404


def test_author_can_access_own_legacy_private_detail_and_metadata(post_db):
    private = _add_post(post_db, 1, "private", is_public=False)
    author = post_db.get(User, 1)

    assert posts_router.get_post(private.id, user=author, db=post_db)["post"]["id"] == private.id
    assert posts_router.get_post_stats(private.id, user=author, db=post_db)["post_id"] == private.id


def test_list_user_search_hashtag_topic_related_and_community_paths_do_not_leak(post_db):
    from app.models.community import Community
    from app.models.models import Topic, post_topics
    from app.services.community_service import CommunityService
    from app.services.recommendation_service import RecommendationService
    from app.services.search_service import SearchService
    from app.services.topic_service import TopicService

    viewer = post_db.get(User, 1)
    topic = Topic(name="visibility-contract")
    community = Community(name="Visibility", slug="visibility", owner_id=1, status="active")
    post_db.add_all([topic, community])
    post_db.commit()

    public = _add_post(post_db, 4, "public", content="needle #needle")
    friend = _add_post(post_db, 2, "friends", content="needle #needle")
    friend_alias = _add_post(post_db, 2, "friends_only", content="needle #needle")
    pending = _add_post(post_db, 3, "friends", content="needle #needle")
    private = _add_post(post_db, 4, "private", content="needle #needle", is_public=True)
    custom = _add_post(post_db, 4, "custom", content="needle #needle", is_public=True)
    own_private = _add_post(post_db, 1, "private", content="needle #needle")
    related_source = _add_post(post_db, 1, "public", content="source")

    all_posts = [public, friend, friend_alias, pending, private, custom, own_private]
    for post in all_posts + [related_source]:
        post.community_id = community.id
        post_db.execute(post_topics.insert().values(post_id=post.id, topic_id=topic.id))
    post_db.commit()

    expected_ids = {public.id, friend.id, friend_alias.id, own_private.id}

    list_ids = {item["id"] for item in posts_router.get_posts(
        page=1, per_page=100, community_id=None, user=viewer, db=post_db,
    )["posts"]}
    user_ids = {item["id"] for item in posts_router.get_user_posts(
        user_id=4, page=1, per_page=100, user=viewer, db=post_db,
    )["posts"]}
    search_ids = {item["id"] for item in SearchService.search_posts(
        post_db, "needle", per_page=100, current_user_id=viewer.id,
    )["posts"]}
    hashtag_ids = {item["id"] for item in SearchService.search_posts_by_hashtag(
        post_db, "needle", per_page=100, current_user_id=viewer.id,
    )["posts"]}
    topic_ids = {item["id"] for item in TopicService.get_topic_posts(
        post_db, topic.id, per_page=100, current_user_id=viewer.id,
    )["posts"]}
    related_ids = {item["id"] for item in RecommendationService.get_related_posts(
        post_db, related_source.id, limit=100, current_user_id=viewer.id,
    )["posts"]}
    community_ids = {post.id for post in CommunityService.list_hot_posts(
        post_db, community.id, limit=100, current_user_id=viewer.id,
    )}

    assert expected_ids <= list_ids
    assert user_ids == {public.id}
    assert search_ids == expected_ids
    assert hashtag_ids == expected_ids
    assert expected_ids <= topic_ids
    assert expected_ids <= related_ids
    assert expected_ids <= community_ids
    for result_ids in (list_ids, user_ids, search_ids, hashtag_ids, topic_ids, related_ids, community_ids):
        assert pending.id not in result_ids
        assert private.id not in result_ids
        assert custom.id not in result_ids


def test_anonymous_service_reads_return_public_only(post_db):
    from app.services.search_service import SearchService

    public = _add_post(post_db, 4, "public", content="anonymous needle")
    _add_post(post_db, 2, "friends", content="anonymous needle")
    _add_post(post_db, 4, "private", content="anonymous needle", is_public=True)

    result = SearchService.search_posts(
        post_db,
        "anonymous needle",
        per_page=100,
        current_user_id=None,
    )

    assert {post["id"] for post in result["posts"]} == {public.id}


def test_comment_by_id_and_comment_like_routes_fail_closed_for_hidden_parent_post(post_db):
    from app.routers import interactions as interactions_router

    hidden = _add_post(post_db, 4, "private", is_public=True)
    comment = Comment(content="hidden comment", user_id=4, post_id=hidden.id, like_count=1)
    post_db.add(comment)
    post_db.flush()
    post_db.add(Like(user_id=1, comment_id=comment.id))
    post_db.commit()
    viewer = post_db.get(User, 1)

    calls = [
        lambda: interactions_router.get_comment_detail(
            comment.id, page=1, per_page=20, user=viewer, db=post_db,
        ),
        lambda: interactions_router.like_comment(comment.id, user=viewer, db=post_db),
        lambda: interactions_router.unlike_comment(comment.id, user=viewer, db=post_db),
    ]
    for call in calls:
        with pytest.raises(HTTPException) as exc_info:
            call()
        assert exc_info.value.status_code == 404

    assert post_db.query(Like).filter(Like.user_id == viewer.id, Like.comment_id == comment.id).count() == 1


def test_unlike_post_fails_closed_before_disclosing_like_state(post_db):
    from app.routers import interactions as interactions_router

    hidden = _add_post(post_db, 4, "private", is_public=True)
    post_db.add(Like(user_id=1, post_id=hidden.id))
    post_db.commit()

    with pytest.raises(HTTPException) as exc_info:
        interactions_router.unlike_post(hidden.id, user=post_db.get(User, 1), db=post_db)

    assert exc_info.value.status_code == 404
    assert post_db.query(Like).filter(Like.user_id == 1, Like.post_id == hidden.id).count() == 1


def test_create_and_read_replies_reject_cross_post_parent_ids(post_db):
    from app.routers import interactions as interactions_router

    requested_post = _add_post(post_db, 1, "public", content="requested")
    other_post = _add_post(post_db, 4, "public", content="other")
    other_parent = Comment(content="other parent", user_id=4, post_id=other_post.id)
    post_db.add(other_parent)
    post_db.commit()
    viewer = post_db.get(User, 1)

    with pytest.raises(HTTPException) as create_error:
        interactions_router.create_comment(
            requested_post.id,
            payload={"content": "cross-post reply", "parent_id": other_parent.id},
            user=viewer,
            db=post_db,
        )
    assert create_error.value.status_code == 404
    assert post_db.query(Comment).filter(Comment.content == "cross-post reply").count() == 0

    with pytest.raises(HTTPException) as read_error:
        interactions_router.get_comments(
            requested_post.id,
            page=1,
            per_page=20,
            parent_id=other_parent.id,
            user=viewer,
            db=post_db,
        )
    assert read_error.value.status_code == 404


def test_top_level_comments_never_embed_cross_post_reply_rows(post_db):
    from app.routers import interactions as interactions_router

    requested_post = _add_post(post_db, 1, "public", content="requested")
    other_post = _add_post(post_db, 4, "public", content="other")
    parent = Comment(content="requested parent", user_id=1, post_id=requested_post.id)
    post_db.add(parent)
    post_db.flush()
    malformed_reply = Comment(
        content="cross-post malformed reply",
        user_id=4,
        post_id=other_post.id,
        parent_id=parent.id,
    )
    post_db.add(malformed_reply)
    post_db.commit()

    result = interactions_router.get_comments(
        requested_post.id,
        page=1,
        per_page=20,
        parent_id=None,
        user=post_db.get(User, 1),
        db=post_db,
    )

    embedded_ids = {
        reply["id"]
        for comment in result["comments"]
        for reply in comment.get("replies", [])
    }
    assert malformed_reply.id not in embedded_ids


def test_admin_hidden_posts_are_invisible_to_author_in_detail_and_search(post_db):
    from app.services.search_service import SearchService

    hidden = _add_post(post_db, 1, "public", content="admin hidden needle")
    hidden.hidden_by_admin = True
    post_db.commit()
    author = post_db.get(User, 1)

    with pytest.raises(HTTPException) as exc_info:
        posts_router.get_post(hidden.id, user=author, db=post_db)
    assert exc_info.value.status_code == 404

    result = SearchService.search_posts(
        post_db,
        "admin hidden needle",
        per_page=100,
        current_user_id=author.id,
    )
    assert hidden.id not in {post["id"] for post in result["posts"]}


def test_community_only_posts_require_active_membership_except_for_author(post_db):
    from app.models.community import Community, CommunityMember
    from app.services.post_visibility_service import can_view_post, post_visibility_predicate

    community = Community(name="Members", slug="members", owner_id=4, status="active")
    post_db.add(community)
    post_db.flush()
    post_db.add_all([
        CommunityMember(community_id=community.id, user_id=2, role="member", status="active"),
        CommunityMember(community_id=community.id, user_id=3, role="member", status="pending"),
    ])
    community_post = _add_post(post_db, 4, "public", content="members only")
    community_post.community_id = community.id
    community_post.community_only = True
    malformed = _add_post(post_db, 4, "public", content="missing community")
    malformed.community_only = True
    malformed.community_id = None
    post_db.commit()

    assert can_view_post(post_db, community_post, 4)
    assert can_view_post(post_db, community_post, 2)
    assert not can_view_post(post_db, community_post, 1)
    assert not can_view_post(post_db, community_post, 3)
    assert not can_view_post(post_db, community_post, None)
    assert not can_view_post(post_db, malformed, 1)

    for viewer_id, expected in ((4, True), (2, True), (1, False), (3, False), (None, False)):
        visible_ids = {
            row.id for row in post_db.query(Post).filter(post_visibility_predicate(viewer_id)).all()
        }
        assert (community_post.id in visible_ids) is expected


def test_community_only_visibility_is_consistent_in_feed_hot_search_and_recommendations(post_db):
    from app.models.community import Community, CommunityMember
    from app.services.community_service import CommunityService
    from app.services.recommendation_service import RecommendationService
    from app.services.search_service import SearchService

    community = Community(name="Scoped", slug="scoped", owner_id=4, status="active")
    post_db.add(community)
    post_db.flush()
    post_db.add(CommunityMember(community_id=community.id, user_id=2, role="member", status="active"))
    community_post = _add_post(post_db, 4, "public", content="scoped visibility needle")
    community_post.community_id = community.id
    community_post.community_only = True
    source = _add_post(post_db, 1, "public", content="related source")
    post_db.commit()

    member = post_db.get(User, 2)
    nonmember = post_db.get(User, 1)
    member_feed = posts_router.get_posts(page=1, per_page=100, community_id=community.id, user=member, db=post_db)
    nonmember_feed = posts_router.get_posts(page=1, per_page=100, community_id=community.id, user=nonmember, db=post_db)
    assert community_post.id in {post["id"] for post in member_feed["posts"]}
    assert community_post.id not in {post["id"] for post in nonmember_feed["posts"]}

    assert community_post.id in {
        post.id for post in CommunityService.list_hot_posts(
            post_db, community.id, limit=100, current_user_id=member.id,
        )
    }
    assert community_post.id not in {
        post.id for post in CommunityService.list_hot_posts(
            post_db, community.id, limit=100, current_user_id=nonmember.id,
        )
    }
    assert community_post.id in {
        post["id"] for post in SearchService.search_posts(
            post_db, "scoped visibility needle", per_page=100, current_user_id=member.id,
        )["posts"]
    }
    assert community_post.id not in {
        post["id"] for post in SearchService.search_posts(
            post_db, "scoped visibility needle", per_page=100, current_user_id=nonmember.id,
        )["posts"]
    }
    assert community_post.id not in {
        post["id"] for post in RecommendationService.get_related_posts(
            post_db, source.id, limit=100, current_user_id=nonmember.id,
        )["posts"]
    }


def test_visibility_predicate_compiles_for_sqlite_and_mysql():
    service = _visibility_service()
    statement = Post.__table__.select().where(service.post_visibility_predicate(1))

    for dialect in (sqlite.dialect(), mysql.dialect()):
        compiled = str(statement.compile(dialect=dialect))
        assert "posts" in compiled
        assert "community_members" in compiled


def test_private_post_card_normalization_fails_closed_and_author_succeeds(post_db):
    from app.services.message_type_service import normalize_user_message_payload

    private = _add_post(post_db, 4, "private", content="private post secret", is_public=False)
    payload = {
        "message_type": "post",
        "related_id": private.id,
        "content": "attacker supplied secret preview",
        "media_url": "https://attacker.invalid/leak.jpg",
    }

    with pytest.raises(HTTPException) as exc_info:
        normalize_user_message_payload(payload, post_db, viewer_user_id=1)
    assert exc_info.value.status_code == 404
    assert "private post secret" not in str(exc_info.value.detail)
    assert "attacker supplied" not in str(exc_info.value.detail)

    normalized = normalize_user_message_payload(payload, post_db, viewer_user_id=4)
    assert normalized["related_id"] == private.id
    assert normalized["content"] == "attacker supplied secret preview"


def test_http_direct_and_community_post_share_callers_reject_private_cards(post_db):
    from app.models.community import Community, CommunityMember
    from app.routers import chat as chat_router
    from app.routers import communities as communities_router

    private = _add_post(post_db, 4, "private", content="caller private secret", is_public=False)
    direct = Conversation(user1_id=1, user2_id=2, type="single")
    community = Community(name="Chat", slug="chat", owner_id=1, status="active")
    post_db.add_all([direct, community])
    post_db.flush()
    post_db.add_all([
        ConversationParticipant(conversation_id=direct.id, user_id=1),
        ConversationParticipant(conversation_id=direct.id, user_id=2),
        CommunityMember(community_id=community.id, user_id=1, role="owner", status="active"),
    ])
    community_conversation = Conversation(type="community", community_id=community.id)
    post_db.add(community_conversation)
    post_db.commit()
    user = post_db.get(User, 1)
    payload = {"message_type": "post", "related_id": private.id, "content": "do not persist"}

    with patch.object(chat_router.ws_manager, "get_blocked_user_ids", return_value=set()):
        with pytest.raises(HTTPException) as direct_error:
            asyncio.run(chat_router.send_message(direct.id, payload=payload, user=user, db=post_db))
    assert direct_error.value.status_code == 404

    with pytest.raises(HTTPException) as community_error:
        asyncio.run(communities_router.send_community_message(
            community.id, payload=payload, user=user, db=post_db,
        ))
    assert community_error.value.status_code == 404
    assert post_db.query(Message).filter(Message.content == "do not persist").count() == 0


def test_ws_post_share_caller_returns_post_unavailable_without_persisting(post_db):
    from app.routers import ws as ws_router

    private = _add_post(post_db, 4, "private", content="ws private secret", is_public=False)
    conversation = Conversation(user1_id=1, user2_id=2, type="single")
    post_db.add(conversation)
    post_db.flush()
    post_db.add_all([
        ConversationParticipant(conversation_id=conversation.id, user_id=1),
        ConversationParticipant(conversation_id=conversation.id, user_id=2),
    ])
    post_db.commit()
    payload = {
        "conversation_id": conversation.id,
        "message_type": "post",
        "related_id": private.id,
        "content": "ws do not persist",
    }

    class NonClosingSession:
        def __enter__(self):
            return post_db

        def __exit__(self, *args):
            return False

    with (
        patch.object(ws_router, "_get_db_session", return_value=post_db),
        patch.object(ws_router.ws_manager, "get_blocked_user_ids", return_value=set()),
        patch.object(ws_router.ws_manager, "send_raw", new=AsyncMock()) as send_raw,
    ):
        asyncio.run(ws_router._handle_send_message(
            SimpleNamespace(),
            1,
            {"type": "send_message", "request_id": "private-share", "payload": payload},
        ))

    sent = send_raw.await_args.args[1]
    assert sent["type"] == "error"
    assert sent["payload"]["code"] == 404
    assert "ws private secret" not in str(sent)
    assert "ws do not persist" not in str(sent)
    assert post_db.query(Message).filter(Message.content == "ws do not persist").count() == 0


def test_direct_post_card_checks_recipient_before_persisting_preview(post_db):
    from app.routers import chat as chat_router

    private = _add_post(post_db, 4, "private", content="author-only card", is_public=False)
    conversation = Conversation(user1_id=1, user2_id=4, type="single")
    post_db.add(conversation)
    post_db.flush()
    post_db.add_all([
        ConversationParticipant(conversation_id=conversation.id, user_id=1),
        ConversationParticipant(conversation_id=conversation.id, user_id=4),
    ])
    post_db.commit()

    with patch.object(chat_router.ws_manager, "get_blocked_user_ids", return_value=set()):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(chat_router.send_message(
                conversation.id,
                payload={
                    "message_type": "post",
                    "related_id": private.id,
                    "content": "must never persist",
                    "media_url": "https://example.invalid/private.jpg",
                },
                user=post_db.get(User, 4),
                db=post_db,
            ))

    assert exc_info.value.status_code == 404
    assert post_db.query(Message).filter(Message.content == "must never persist").count() == 0


def test_community_post_cards_reject_cross_community_and_friends_only_audiences(post_db):
    from app.models.community import Community, CommunityMember
    from app.routers import communities as communities_router

    source = Community(name="Source", slug="card-source", owner_id=1, status="active")
    destination = Community(name="Destination", slug="card-destination", owner_id=1, status="active")
    post_db.add_all([source, destination])
    post_db.flush()
    post_db.add_all([
        CommunityMember(community_id=source.id, user_id=1, role="owner", status="active"),
        CommunityMember(community_id=destination.id, user_id=1, role="owner", status="active"),
        CommunityMember(community_id=destination.id, user_id=2, role="member", status="active"),
        CommunityMember(community_id=destination.id, user_id=4, role="member", status="active"),
    ])
    destination_conversation = Conversation(type="community", community_id=destination.id)
    post_db.add(destination_conversation)
    post_db.flush()
    for user_id in (1, 2, 4):
        post_db.add(ConversationParticipant(
            conversation_id=destination_conversation.id,
            user_id=user_id,
        ))

    cross_community = _add_post(post_db, 1, "public", content="source members only")
    cross_community.community_id = source.id
    cross_community.community_only = True
    friends_only = _add_post(post_db, 1, "friends", content="friends card")
    post_db.commit()

    for post, preview in (
        (cross_community, "cross-community preview"),
        (friends_only, "friends-only preview"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(communities_router.send_community_message(
                destination.id,
                payload={"message_type": "post", "related_id": post.id, "content": preview},
                user=post_db.get(User, 1),
                db=post_db,
            ))
        assert exc_info.value.status_code == 404
        assert post_db.query(Message).filter(Message.content == preview).count() == 0


def test_ws_direct_post_card_checks_recipient_before_persisting(post_db):
    from app.routers import ws as ws_router

    private = _add_post(post_db, 4, "private", content="ws author-only card", is_public=False)
    conversation = Conversation(user1_id=1, user2_id=4, type="single")
    post_db.add(conversation)
    post_db.flush()
    post_db.add_all([
        ConversationParticipant(conversation_id=conversation.id, user_id=1),
        ConversationParticipant(conversation_id=conversation.id, user_id=4),
    ])
    post_db.commit()

    with (
        patch.object(ws_router, "_get_db_session", return_value=post_db),
        patch.object(ws_router.ws_manager, "get_blocked_user_ids", return_value=set()),
        patch.object(ws_router.ws_manager, "send_raw", new=AsyncMock()) as send_raw,
    ):
        asyncio.run(ws_router._handle_send_message(
            SimpleNamespace(),
            4,
            {
                "type": "send_message",
                "request_id": "recipient-check",
                "payload": {
                    "conversation_id": conversation.id,
                    "message_type": "post",
                    "related_id": private.id,
                    "content": "ws recipient leak",
                },
            },
        ))

    sent = send_raw.await_args.args[1]
    assert sent["type"] == "error"
    assert sent["payload"]["code"] == 404
    assert post_db.query(Message).filter(Message.content == "ws recipient leak").count() == 0


def test_legacy_post_card_reads_are_redacted_for_current_viewer(post_db):
    from app.routers import chat as chat_router

    private = _add_post(post_db, 4, "private", content="legacy private post", is_public=False)
    conversation = Conversation(user1_id=1, user2_id=4, type="single")
    post_db.add(conversation)
    post_db.flush()
    post_db.add_all([
        ConversationParticipant(conversation_id=conversation.id, user_id=1),
        ConversationParticipant(conversation_id=conversation.id, user_id=4),
        Message(
            conversation_id=conversation.id,
            sender_id=4,
            message_type="post",
            related_id=private.id,
            content="legacy leaked preview",
            media_url="https://example.invalid/legacy-private.jpg",
        ),
    ])
    post_db.commit()

    result = chat_router.get_messages(
        conversation.id,
        page=1,
        per_page=50,
        user=post_db.get(User, 1),
        db=post_db,
    )

    card = result["messages"][0]
    assert card["content"] == "帖子不可用"
    assert card["media_url"] is None
    assert card["file_url"] is None
    assert card["post_unavailable"] is True


def test_hidden_parent_blocks_comment_update_and_delete_and_preserves_comment(post_db):
    from app.routers import interactions as interactions_router

    hidden = _add_post(post_db, 1, "public", content="hidden parent")
    hidden.hidden_by_admin = True
    comment = Comment(content="original comment", user_id=1, post_id=hidden.id)
    post_db.add(comment)
    post_db.commit()

    with pytest.raises(HTTPException) as update_error:
        interactions_router.update_comment(
            comment.id,
            payload={"content": "mutated"},
            user=post_db.get(User, 1),
            db=post_db,
        )
    assert update_error.value.status_code == 404

    with pytest.raises(HTTPException) as delete_error:
        interactions_router.delete_comment(comment.id, user=post_db.get(User, 1), db=post_db)
    assert delete_error.value.status_code == 404

    post_db.refresh(comment)
    assert comment.content == "original comment"


def test_admin_hidden_post_blocks_author_update_and_delete(post_db):
    hidden = _add_post(post_db, 1, "public", content="admin hidden mutation")
    hidden.hidden_by_admin = True
    post_db.commit()
    author = post_db.get(User, 1)

    with pytest.raises(HTTPException) as update_error:
        posts_router.update_post(hidden.id, payload={"content": "changed"}, user=author, db=post_db)
    assert update_error.value.status_code == 404

    with pytest.raises(HTTPException) as delete_error:
        posts_router.delete_post(hidden.id, user=author, db=post_db)
    assert delete_error.value.status_code == 404
    assert post_db.get(Post, hidden.id) is not None
    assert post_db.get(Post, hidden.id).content == "admin hidden mutation"


def test_nullable_hidden_posts_remain_visible_in_community_feed_and_hot(post_db):
    from datetime import datetime
    from app.models.community import Community, CommunityMember
    from app.services.community_service import CommunityService

    community = Community(name="Nullable", slug="nullable-hidden", owner_id=1, status="active")
    post_db.add(community)
    post_db.flush()
    post_db.add(CommunityMember(
        community_id=community.id,
        user_id=1,
        role="owner",
        status="active",
    ))
    nullable = _add_post(post_db, 1, "public", content="nullable hidden")
    nullable.community_id = community.id
    nullable.hidden_by_admin = None
    nullable.created_at = datetime.utcnow()
    post_db.commit()

    feed = posts_router.get_posts(
        page=1,
        per_page=100,
        community_id=community.id,
        user=post_db.get(User, 1),
        db=post_db,
    )
    hot = CommunityService.list_hot_posts(
        post_db,
        community.id,
        limit=100,
        current_user_id=1,
    )

    assert nullable.id in {item["id"] for item in feed["posts"]}
    assert nullable.id in {item.id for item in hot}


def test_create_rejects_orphan_community_only_post(post_db):
    user = post_db.get(User, 1)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(posts_router.create_post(
            image=None,
            video=None,
            content="orphan create",
            image_urls=None,
            video_url_input=None,
            content_category=None,
            display_role_type=None,
            visibility="public",
            visible_user_ids=None,
            community_id=None,
            community_only=True,
            user=user,
            db=post_db,
        ))

    assert exc_info.value.status_code == 422
    assert post_db.query(Post).filter(Post.content == "orphan create").count() == 0


def test_update_rejects_removing_community_from_community_only_post(post_db):
    from app.models.community import Community, CommunityMember

    community = Community(name="Update Scope", slug="update-scope", owner_id=1, status="active")
    post_db.add(community)
    post_db.flush()
    post_db.add(CommunityMember(
        community_id=community.id,
        user_id=1,
        role="owner",
        status="active",
    ))
    post = _add_post(post_db, 1, "public", content="valid scoped post")
    post.community_id = community.id
    post.community_only = True
    post_db.commit()

    with pytest.raises(HTTPException) as exc_info:
        posts_router.update_post(
            post.id,
            payload={"community_id": None},
            user=post_db.get(User, 1),
            db=post_db,
        )

    assert exc_info.value.status_code == 422
    post_db.refresh(post)
    assert post.community_id == community.id
    assert post.community_only is True
