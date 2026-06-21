-- Performance indexes for Home, Explore, Conversations, and Profile pages.
-- Safe to re-run on MySQL/MariaDB: each index is created only if missing.
-- Apply manually after reviewing existing production indexes and maintenance window.

DELIMITER $$

DROP PROCEDURE IF EXISTS add_index_if_missing $$
CREATE PROCEDURE add_index_if_missing(
    IN p_table_name VARCHAR(128),
    IN p_index_name VARCHAR(128),
    IN p_index_columns TEXT
)
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = p_table_name
          AND INDEX_NAME = p_index_name
    ) THEN
        SET @ddl = CONCAT('CREATE INDEX ', p_index_name, ' ON ', p_table_name, ' (', p_index_columns, ')');
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END $$

DELIMITER ;

CALL add_index_if_missing('posts', 'idx_posts_public_created', 'is_public, created_at');
CALL add_index_if_missing('posts', 'idx_posts_user_public_created', 'user_id, is_public, created_at');
CALL add_index_if_missing('posts', 'idx_posts_visibility_created', 'visibility, created_at');

CALL add_index_if_missing('friendships', 'idx_friendships_sender_status_receiver', 'sender_id, status, receiver_id');
CALL add_index_if_missing('friendships', 'idx_friendships_receiver_status_sender', 'receiver_id, status, sender_id');

CALL add_index_if_missing('likes', 'idx_likes_user_created', 'user_id, created_at');
CALL add_index_if_missing('likes', 'idx_likes_post_user', 'post_id, user_id');

CALL add_index_if_missing('conversations', 'idx_conversations_user1_last_message', 'user1_id, last_message_at');
CALL add_index_if_missing('conversations', 'idx_conversations_user2_last_message', 'user2_id, last_message_at');
CALL add_index_if_missing('messages', 'idx_messages_conversation_read_sender', 'conversation_id, is_read, sender_id');

CALL add_index_if_missing('post_topics', 'idx_post_topics_topic_post', 'topic_id, post_id');
CALL add_index_if_missing('topic_followers', 'idx_topic_followers_topic_user', 'topic_id, user_id');
CALL add_index_if_missing('topic_followers', 'idx_topic_followers_user_topic', 'user_id, topic_id');

CALL add_index_if_missing('comic_events', 'idx_comic_events_status_start', 'status, start_date');
CALL add_index_if_missing('comic_events', 'idx_comic_events_city_status_start', 'city_id, status, start_date');
CALL add_index_if_missing('comic_event_follows', 'idx_comic_event_follows_event_user', 'event_id, user_id');
CALL add_index_if_missing('comic_event_follows', 'idx_comic_event_follows_user_event', 'user_id, event_id');
CALL add_index_if_missing('comic_event_images', 'idx_comic_event_images_event_cover_sort', 'event_id, is_cover, sort_order');

DROP PROCEDURE IF EXISTS add_index_if_missing;
