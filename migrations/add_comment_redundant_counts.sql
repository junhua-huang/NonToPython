-- ============================================================
-- 评论区性能优化 - 数据库迁移脚本
-- 日期：2026-06-04
-- 说明：
--   1. Comment 表添加 like_count / reply_count 冗余计数列
--   2. comments 表添加复合索引
--   3. likes 表添加复合索引
-- ============================================================

-- P1 - 冗余计数缓存：添加列
ALTER TABLE comments ADD COLUMN like_count INTEGER DEFAULT 0;
ALTER TABLE comments ADD COLUMN reply_count INTEGER DEFAULT 0;

-- P0 - 复合索引：comments 表
CREATE INDEX IF NOT EXISTS idx_comments_post_parent ON comments(post_id, parent_id);
CREATE INDEX IF NOT EXISTS idx_comments_parent_created ON comments(parent_id, created_at);

-- P0 - 复合索引：likes 表
CREATE INDEX IF NOT EXISTS idx_likes_comment_user ON likes(comment_id, user_id);

-- 初始化已有数据的 like_count（从 likes 表统计）
UPDATE comments SET like_count = (
    SELECT COUNT(*) FROM likes WHERE likes.comment_id = comments.id
);

-- 初始化已有数据的 reply_count（从 comments 表统计）
UPDATE comments SET reply_count = (
    SELECT COUNT(*) FROM comments AS replies WHERE replies.parent_id = comments.id
);
