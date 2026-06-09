-- WS Protocol tables: seq log, user seq counter, ack dedup
-- 适用于 MySQL

CREATE TABLE IF NOT EXISTS ws_message_log (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT          NOT NULL,
    seq         INT          NOT NULL,
    payload     JSON         NOT NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_ws_msg_user_seq (user_id, seq),
    INDEX idx_ws_msg_user_seq (user_id, seq),
    CONSTRAINT fk_ws_msg_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ws_user_seq (
    user_id      INT PRIMARY KEY,
    current_seq  INT NOT NULL DEFAULT 0,
    CONSTRAINT fk_ws_seq_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ws_ack_dedup (
    client_msg_id  CHAR(36)     PRIMARY KEY,
    user_id        INT          NOT NULL,
    processed_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_ws_ack_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 定期清理 24h 前的 dedup 记录（可由定时任务执行）
-- DELETE FROM ws_ack_dedup WHERE processed_at < NOW() - INTERVAL 24 HOUR;
-- 定期清理 7 天前的消息日志
-- DELETE FROM ws_message_log WHERE created_at < NOW() - INTERVAL 7 DAY;
