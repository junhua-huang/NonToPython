-- 漫展城市表
CREATE TABLE IF NOT EXISTS comic_cities (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(64) NOT NULL,
    province VARCHAR(32) DEFAULT '广西',
    sort_order INT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 漫展标签表
CREATE TABLE IF NOT EXISTS comic_tags (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(64) NOT NULL,
    tag_type VARCHAR(32) DEFAULT 'type' COMMENT 'type=展会类型, scale=展会规模',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 漫展主表
CREATE TABLE IF NOT EXISTS comic_events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    city_id INT NOT NULL,
    venue VARCHAR(256) DEFAULT '',
    start_date DATE,
    end_date DATE,
    start_time VARCHAR(8) DEFAULT '09:00',
    end_time VARCHAR(8) DEFAULT '18:00',
    ticket_info VARCHAR(256) DEFAULT '',
    website VARCHAR(512) DEFAULT '',
    intro TEXT,
    status INT DEFAULT 0 COMMENT '0=即将开始, 1=进行中, 2=已结束',
    creator_id INT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (city_id) REFERENCES comic_cities(id),
    FOREIGN KEY (creator_id) REFERENCES users(id)
);

-- 漫展图片表
CREATE TABLE IF NOT EXISTS comic_event_images (
    id INT AUTO_INCREMENT PRIMARY KEY,
    event_id INT NOT NULL,
    image_url VARCHAR(512) NOT NULL,
    is_cover TINYINT DEFAULT 0 COMMENT '是否为封面图',
    sort_order INT DEFAULT 0,
    FOREIGN KEY (event_id) REFERENCES comic_events(id) ON DELETE CASCADE
);

-- 漫展-标签关联表
CREATE TABLE IF NOT EXISTS comic_event_tag_rel (
    id INT AUTO_INCREMENT PRIMARY KEY,
    event_id INT NOT NULL,
    tag_id INT NOT NULL,
    UNIQUE KEY uq_event_tag (event_id, tag_id),
    FOREIGN KEY (event_id) REFERENCES comic_events(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES comic_tags(id) ON DELETE CASCADE
);

-- 漫展关注表
CREATE TABLE IF NOT EXISTS comic_event_follows (
    id INT AUTO_INCREMENT PRIMARY KEY,
    event_id INT NOT NULL,
    user_id INT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_event_user (event_id, user_id),
    FOREIGN KEY (event_id) REFERENCES comic_events(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);