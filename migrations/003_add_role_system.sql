-- ============================================================
-- NanTuPy 角色系统迁移
-- 003_add_role_system.sql
-- 支持多角色：管理员、主办方、Coser、毛娘、妆娘、摄影师、后期师、票务代理、普通用户
-- ============================================================

-- -------------------------------------------
-- 1. 角色定义表
-- -------------------------------------------
CREATE TABLE IF NOT EXISTS roles (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(32)  NOT NULL UNIQUE,
    label       VARCHAR(32)  NOT NULL,
    description VARCHAR(128) DEFAULT '',
    sort_order  INT          DEFAULT 0,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------
-- 2. 用户-角色关联表（多对多）
-- -------------------------------------------
CREATE TABLE IF NOT EXISTS user_roles (
    user_id    INT NOT NULL,
    role_id    INT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, role_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------
-- 3. Coser 专属资料表
-- -------------------------------------------
CREATE TABLE IF NOT EXISTS coser_profiles (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    user_id            INT NOT NULL UNIQUE,
    cosname            VARCHAR(64)   DEFAULT '',        -- 圈名
    bio                TEXT,
    styles             VARCHAR(512)  DEFAULT '',        -- 擅长风格，逗号分隔
    city               VARCHAR(32)   DEFAULT '',
    is_available       TINYINT(1)    DEFAULT 1,        -- 是否接单
    price_range_min    INT           DEFAULT 0,
    price_range_max    INT           DEFAULT 0,
    portfolio_images   TEXT,                            -- JSON array of URLs
    social_links       TEXT,                            -- JSON: {weibo, bilibili, douyin}
    created_at         DATETIME      DEFAULT CURRENT_TIMESTAMP,
    updated_at         DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------
-- 4. 摄影师专属资料表
-- -------------------------------------------
CREATE TABLE IF NOT EXISTS photographer_profiles (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    user_id            INT NOT NULL UNIQUE,
    equipment          VARCHAR(512)  DEFAULT '',        -- 器材信息
    styles             VARCHAR(512)  DEFAULT '',        -- 擅长风格
    city               VARCHAR(32)   DEFAULT '',
    is_available       TINYINT(1)    DEFAULT 1,
    price_range_min    INT           DEFAULT 0,
    price_range_max    INT           DEFAULT 0,
    portfolio_images   TEXT,
    social_links       TEXT,
    created_at         DATETIME      DEFAULT CURRENT_TIMESTAMP,
    updated_at         DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------
-- 5. 通用服务商资料表（毛娘 / 妆娘 / 后期师 / 票务代理共用）
-- -------------------------------------------
CREATE TABLE IF NOT EXISTS service_profiles (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    user_id            INT NOT NULL UNIQUE,
    service_type       VARCHAR(32)   NOT NULL,          -- wig_stylist / makeup_artist / editor / ticket_agent
    description        TEXT,
    city               VARCHAR(32)   DEFAULT '',
    is_available       TINYINT(1)    DEFAULT 1,
    price_info         VARCHAR(512)  DEFAULT '',        -- 价格说明（自由文本）
    portfolio_images   TEXT,
    created_at         DATETIME      DEFAULT CURRENT_TIMESTAMP,
    updated_at         DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_service_type (service_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------
-- 6. 角色申请表（用户申请 → 管理员审核）
-- -------------------------------------------
CREATE TABLE IF NOT EXISTS role_applications (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    user_id         INT NOT NULL,
    role_id         INT NOT NULL,
    status          VARCHAR(16)  DEFAULT 'pending',     -- pending / approved / rejected
    reason          TEXT,                                -- 申请理由
    review_comment  TEXT,                                -- 审核备注
    reviewer_id     INT DEFAULT NULL,                    -- 审核人 ID
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    reviewed_at     DATETIME DEFAULT NULL,
    FOREIGN KEY (user_id)     REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (role_id)     REFERENCES roles(id)  ON DELETE CASCADE,
    FOREIGN KEY (reviewer_id) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_application_status (status),
    INDEX idx_application_user   (user_id, role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------
-- 7. 种子数据：9 种角色
-- -------------------------------------------
INSERT INTO roles (id, name, label, description, sort_order) VALUES
    (1, 'admin',          '管理员',   '系统最高权限，管理用户和内容',  0),
    (2, 'organizer',      '主办方',   '创建和管理漫展活动',            10),
    (3, 'coser',          'Coser',    '角色扮演者，展示作品和接单',    20),
    (4, 'wig_stylist',    '毛娘',     '假发造型服务',                  30),
    (5, 'makeup_artist',  '妆娘',     '化妆造型服务',                  31),
    (6, 'photographer',   '摄影师',   '摄影服务',                      40),
    (7, 'editor',         '后期师',   '图片后期处理服务',              41),
    (8, 'ticket_agent',   '票务代理', '漫展票务代理销售',              50),
    (9, 'user',           '普通用户', '基础社交功能',                  99)
ON DUPLICATE KEY UPDATE label = VALUES(label), description = VALUES(description);

-- -------------------------------------------
-- 8. 存量数据迁移：将现有 is_admin=1 的用户赋予 admin 角色
-- -------------------------------------------
INSERT INTO user_roles (user_id, role_id)
    SELECT u.id, 1
    FROM users u
    WHERE u.is_admin = 1
      AND NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id = u.id AND ur.role_id = 1);

-- -------------------------------------------
-- 9. 存量数据迁移：所有现有用户默认赋予 user 角色
-- -------------------------------------------
INSERT INTO user_roles (user_id, role_id)
    SELECT u.id, 9
    FROM users u
    WHERE NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id = u.id AND ur.role_id = 9);
