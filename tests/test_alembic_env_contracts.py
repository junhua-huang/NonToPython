from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


DB_MANAGED_INDEXES = {
    ("comic_event_follows", "idx_comic_event_follows_event_user"),
    ("comic_event_follows", "idx_comic_event_follows_user_event"),
    ("comic_event_images", "idx_comic_event_images_event_cover_sort"),
    ("comic_events", "idx_comic_events_city_end_start"),
    ("comic_events", "idx_comic_events_end_start"),
    ("conversation_participants", "ix_cp_conversation_last_read_message"),
    ("conversation_participants", "ix_cp_conversation_user"),
    ("conversations", "idx_conversations_user1_last_message"),
    ("conversations", "idx_conversations_user2_last_message"),
    ("friendships", "idx_friendships_receiver_status_sender"),
    ("friendships", "idx_friendships_sender_status_receiver"),
    ("likes", "idx_likes_post_user"),
    ("likes", "idx_likes_user_created"),
    ("messages", "idx_messages_conversation_read_sender"),
    ("post_topics", "idx_post_topics_topic_post"),
    ("posts", "idx_posts_public_created"),
    ("posts", "idx_posts_user_public_created"),
    ("posts", "idx_posts_visibility_created"),
    ("service_profiles", "user_id"),
    ("topic_followers", "idx_topic_followers_topic_user"),
    ("topic_followers", "idx_topic_followers_user_topic"),
}


def test_alembic_env_ignores_database_managed_legacy_indexes():
    source = (ROOT / "alembic" / "env.py").read_text(encoding="utf-8")

    assert "_DB_MANAGED_INDEXES" in source
    for table_name, index_name in DB_MANAGED_INDEXES:
        assert f"('{table_name}', '{index_name}')" in source
    assert "_DB_MANAGED_INDEXES" in source[source.index("def include_object") :]


def test_default_pytest_collection_ignores_live_localhost_scripts():
    source = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")

    assert "NONTO_RUN_LIVE_API_TESTS" in source
    for filename in (
        "full_api_test.py",
        "test_batch_messages.py",
        "test_complete_api.py",
        "test_email_search.py",
        "test_empty_search.py",
        "test_profile_upload.py",
        "test_search_api.py",
        "test_search_fix.py",
        "test_upload.py",
        "test_ws_connect.py",
        "test_ws_simple.py",
    ):
        assert f'"{filename}"' in source
