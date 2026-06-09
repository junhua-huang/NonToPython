-- 为批量消息查询添加复合索引
-- 作用：WHERE conversation_id = X ORDER BY created_at DESC LIMIT N 无需 filesort
--       索引直接按 (conversation_id, created_at) 排序，找到最新 N 条即停止

ALTER TABLE messages ADD INDEX idx_messages_conv_time (conversation_id, created_at);

-- 验证
-- EXPLAIN SELECT * FROM messages WHERE conversation_id = 1 ORDER BY created_at DESC LIMIT 30;
-- 应显示 Extra: Using index（无 filesort）