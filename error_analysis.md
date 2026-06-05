# NanTuPy 项目错误分析报告
> 分析时间：2026-06-02
> 分析范围：模型定义 vs 数据库实际表结构、路由端点参数、缺失方法
> 数据库：mysql7.sqlpub.com:3312 / database=facebook

---

## 一、模型定义与数据库实际表结构不一致（核心问题）

### 1. `conversations` 表

| 字段 | 数据库实际列 | 模型定义（models.py） | 状态 |
|------|-------------|----------------------|------|
| `user1_id` | ✅ 存在 | ❌ 模型无此列 | **不匹配** |
| `user2_id` | ✅ 存在 | ❌ 模型无此列 | **不匹配** |
| `last_message_at` | ✅ 存在 | ❌ 模型无此列 | **不匹配** |
| `created_at` | ✅ 存在 | ✅ 模型有 | ✅ 匹配 |
| `updated_at` | ✅ 存在 | ✅ 模型有 | ✅ 匹配 |

**影响**：`app/routers/chat.py` 中大量使用 `Conversation.user1_id`、`Conversation.user2_id`、`Conversation.last_message_at`，会导致 **500 错误**（AttributeError）。

**另外**：`Conversation` 模型缺少 `to_dict()` 方法，chat.py 调用 `conv.to_dict()` 会 500。

---

### 2. `messages` 表

| 字段 | 数据库实际列 | 模型定义 | 状态 |
|------|-------------|---------|------|
| `is_read` | ✅ `tinyint(1)` | ❌ 模型无此列（模型用 `read_at` datetime） | **不匹配** |
| `media_url` | ✅ `varchar(255)` | ❌ 模型定义为 `image_url` | **列名不匹配** |
| `related_id` | ✅ `int` | ❌ 模型无此列 | **不匹配** |
| `read_at` | ❌ 不存在 | ✅ 模型有 `read_at` | **模型多余列** |
| `message_type` | ✅ 存在 | ✅ 模型有 | ✅ 匹配 |
| `content` | ✅ 存在 | ✅ 模型有 | ✅ 匹配 |
| `sender_id` | ✅ 存在 | ✅ 模型有 | ✅ 匹配 |
| `conversation_id` | ✅ 存在 | ✅ 模型有 | ✅ 匹配 |

**影响**：
- `chat.py` 使用 `Message.is_read`（数据库有，模型无）→ **500**
- `chat.py` 使用 `Message.to_dict()` → 模型无此方法 → **500**
- 模型定义 `image_url`，但数据库列名为 `media_url`，SQLAlchemy ORM 读写时会列名不匹配 → **500**

---

### 3. `notifications` 表

| 字段 | 数据库实际列 | 模型定义 | 状态 |
|------|-------------|---------|------|
| `related_id` | ✅ 存在 | ❌ 模型定义为 `reference_id` | **列名不匹配** |
| `related_type` | ✅ 存在 | ❌ 模型定义为 `reference_type` | **列名不匹配** |
| `title` | ✅ `varchar(200)` | ❌ 模型无此列 | **模型缺列** |
| `reference_id` | ❌ 不存在 | ✅ 模型有 | **模型多余列** |
| `reference_type` | ❌ 不存在 | ✅ 模型有 | **模型多余列** |

**影响**：
- 模型用 `reference_id`/`reference_type`，但数据库列名是 `related_id`/`related_type` → SQLAlchemy 查询/写入时列名不匹配 → **500**
- `notification_service.py` 使用 `related_id`、`related_type` 写入 Notification → 与模型定义冲突 → **500**
- `Notification` 模型缺少 `to_dict()` 方法 → 路由调用时 **500**

---

### 4. `reports` 表

| 字段 | 数据库实际列 | 模型定义 | 状态 |
|------|-------------|---------|------|
| `target_type` | ✅ `varchar(20)` | ❌ 模型无此列 | **完全不匹配** |
| `target_id` | ✅ `int` | ❌ 模型无此列 | **完全不匹配** |
| `reporter_id` | ✅ 存在 | ✅ 模型有 | ✅ 匹配 |
| `reason` | ✅ 存在 | ✅ 模型有 | ✅ 匹配 |
| `description` | ✅ 存在 | ✅ 模型有 | ✅ 匹配 |
| `status` | ✅ 存在 | ✅ 模型有 | ✅ 匹配 |
| `created_at` | ✅ 存在 | ✅ 模型有 | ✅ 匹配 |
| `resolved_at` | ✅ 存在 | ✅ 模型有 | ✅ 匹配 |
| `reported_user_id` | ❌ 不存在 | ✅ 模型有 | **模型多余列** |
| `reported_post_id` | ❌ 不存在 | ✅ 模型有 | **模型多余列** |
| `reported_comment_id` | ❌ 不存在 | ✅ 模型有 | **模型多余列** |

**影响**：**模型设计与数据库完全不一致**。数据库用 `target_type`+`target_id` 通用设计，模型却按实体分了三列（`reported_user_id`/`reported_post_id`/`reported_comment_id`）。路由 `reports.py` 使用 `report_type` 和 `target_id` 作为 payload 字段（与数据库匹配），但模型无法正确映射 → **500**

---

### 5. `topics` 表

| 字段 | 数据库实际列 | 模型定义 | 状态 |
|------|-------------|---------|------|
| `icon_url` | ✅ `varchar(255)` | ❌ 模型定义为 `cover_url` | **列名不匹配** |
| `color` | ✅ `varchar(20)` | ❌ 模型无此列 | **模型缺列** |
| `is_trending` | ✅ `tinyint(1)` | ❌ 模型无此列（模型用 `is_official`） | **不匹配** |
| `updated_at` | ✅ 存在 | ❌ 模型无此列 | **模型缺列** |
| `cover_url` | ❌ 不存在 | ✅ 模型有 | **模型多余列** |
| `is_official` | ❌ 不存在 | ✅ 模型有 | **模型多余列** |
| `created_by` | ❌ 不存在 | ✅ 模型有 | **模型多余列** |

**影响**：
- `topics.py` 路由和 `topic_service.py` 使用 `topic.icon_url`、`topic.color` → 数据库有这些列，但模型定义的属性名是 `cover_url`，SQLAlchemy 映射错误 → **500**
- `Topic` 模型的 `to_dict()` 方法输出 `cover_url`，但数据库列名是 `icon_url` → API 返回字段名与预期不符

---

### 6. `comments` 表

| 字段 | 数据库实际列 | 模型定义 | 状态 |
|------|-------------|---------|------|
| `reply_to_user_id` | ✅ `int` | ❌ 模型无此列 | **模型缺列** |

**影响**：`interactions.py` 创建评论时传入 `reply_to_user_id=reply_to_user_id` → 模型无此列 → SQLAlchemy 写入时报错 → **500**

---

### 7. `posts` 表

| 字段 | 数据库实际列 | 模型定义 | 状态 |
|------|-------------|---------|------|
| `view_count` | ✅ `int` | ❌ 模型无此列 | **模型缺列** |

**影响**：`posts` 表有 `view_count` 列但模型未映射，相关统计功能异常。

---

### 8. `post_views` 表

| 字段 | 数据库实际列 | 模型定义 | 状态 |
|------|-------------|---------|------|
| `created_at` | ✅ 存在 | ❌ 模型定义为 `viewed_at` | **列名不匹配** |

**影响**：`posts.py` 中 `record_post_view` 端点写入 `PostView` 时，模型用 `viewed_at`，但数据库列名是 `created_at` → SQLAlchemy 列名不匹配 → **500**

---

### 9. `likes` 表

| 字段 | 数据库实际列 | 模型定义 | 状态 |
|------|-------------|---------|------|
| `post_id` | ✅ NOT NULL | ❌ 模型定义 `nullable=True` | **约束不匹配** |

### 10. `users` 表

所有字段与模型完全匹配，`User.to_dict()` 方法存在且正确。 ✅

---

## 二、模型缺少 `to_dict()` 方法

以下模型的路由代码中调用了 `to_dict()`，但模型未定义该方法，会导致 **500 错误**：

| 模型 | 路由文件 | 状态 |
|------|---------|------|
| `Friendship` | `friends.py` | ❌ 缺少 |
| `Block` | `blocks.py` | ❌ 缺少 |
| `Like` | `interactions.py` | ❌ 缺少 |
| `Conversation` | `chat.py` | ❌ 缺少 |
| `Message` | `chat.py` | ❌ 缺少 |
| `Notification` | `notifications.py` | ❌ 缺少 |
| `Report` | `reports.py` | ❌ 缺少 |

> `User`、`Post`、`Comment`、`Topic`、`SensitiveWord`、`SearchHistory`、`PostView` 已定义。

---

## 三、路由端点与测试脚本不匹配

测试文件 `tests/test_complete_api.py` 是针对 **Flask 版本** 的 API 路径编写的：

| 测试调用路径 | 实际 FastAPI 路径 | 状态 |
|-------------|-------------------|------|
| `GET/PUT /api/auth/profile` | ❌ 不存在（实际是 `GET /api/auth/me`） | **404** |
| `GET /api/auth/users/{id}` | ❌ 不存在 | **404** |
| `POST /api/upload/post/image` | ❌ 不存在 | **404** |
| `POST /api/posts/` 等 | ✅ 正常 | ✅ 匹配 |

**另外**：测试使用 `from app import create_app`（Flask），项目已重构为 FastAPI。

---

## 四、Pydantic Schema 不匹配

`auth.py` 的 `RegisterRequest` 只有 `username`/`email`/`password`，但测试传入 `first_name`/`last_name`/`bio` → **422 验证错误**。

---

## 五、Comment.to_dict() 参数错误

`Comment.to_dict()` 签名为 `def to_dict(self)`（无参数），但 `interactions.py` 调用 `comment.to_dict(current_user_id=user.id)` → **TypeError → 500**。

---

## 六、search.py 中 `db.case()` 用法错误

`db` 是 SQLAlchemy `Session` 对象，无 `case()` 方法。应改为 `from sqlalchemy import case`。

**影响**：`GET /api/search/mention-suggestions` → **500**

---

## 七、完整 500 端点清单（按模块分类）

### 聊天模块（Chat）— 6 个端点全部 500
| 端点 | 根本原因 |
|------|---------|
| `GET /api/chat/conversations` | `Conversation.user1_id/user2_id` 不存在 + `to_dict()` 缺失 |
| `GET /api/chat/conversations/{id}` | 同上 |
| `GET /api/chat/conversations/{id}/messages` | `Message.is_read` 不存在 + `to_dict()` 缺失 |
| `POST /api/chat/conversations/{id}/mark-read` | `Message.is_read` 不存在 |
| `GET /api/chat/users/online` | `Conversation.to_dict()` 缺失 |
| `GET /api/chat/unread-count` | `Message.is_read` 不存在 |

### 通知模块（Notifications）— 全部端点 500
| 端点 | 根本原因 |
|------|---------|
| 所有 GET/PUT 端点 | `reference_id` vs DB `related_id` 列名不匹配 + `to_dict()` 缺失 |

### 举报模块（Reports）— 全部端点 500
| 端点 | 根本原因 |
|------|---------|
| `POST /api/reports/` | 模型列定义与数据库完全不一致 |
| `GET /api/reports/` | `Report.to_dict()` 缺失 |

### 好友模块（Friends）— 3 个端点 500
| 端点 | 根本原因 |
|------|---------|
| `POST /api/friends/request` | `Friendship.to_dict()` 缺失 |
| `GET /api/friends/requests/pending` | 同上 |
| `GET /api/friends/` | 同上 |

### 互动模块（Interactions）— 3 个端点 500
| 端点 | 根本原因 |
|------|---------|
| `POST /api/posts/{id}/comments` | `Comment` 模型无 `reply_to_user_id` 列 |
| `PUT /api/comments/{id}` | `Comment.to_dict()` 不接受 `current_user_id` 参数 |
| `POST /api/posts/{id}/like` | `Like.to_dict()` 缺失 |

### 话题模块（Topics）— 2 个端点 500
| 端点 | 根本原因 |
|------|---------|
| `POST /api/topics/` | `Topic.icon_url` 与模型 `cover_url` 不匹配 |
| `POST /api/topics/{id}/follow` | 同上 |

### 帖子模块（Posts）— 1 个端点 500
| 端点 | 根本原因 |
|------|---------|
| `POST /api/posts/{id}/view` | `PostView.viewed_at` vs DB `created_at` 列名不匹配 |

### 搜索模块（Search）— 1 个端点 500
| 端点 | 根本原因 |
|------|---------|
| `GET /api/search/mention-suggestions` | `db.case()` 用法错误 |

### 屏蔽模块（Blocks）— 1 个端点 500
| 端点 | 根本原因 |
|------|---------|
| `POST /api/blocks/` | `Block.to_dict()` 缺失 |

---

## 八、修复优先级

### 🔴 最高优先级 — 7 个模型列定义修正

1. **Conversation**: 添加 `user1_id`, `user2_id`, `last_message_at` 列
2. **Message**: 改 `image_url`→`media_url`，添加 `is_read`, `related_id`，移除 `read_at`
3. **Notification**: 改 `reference_id/type`→`related_id/type`，添加 `title`
4. **Report**: 改为 `target_type`+`target_id`，移除 `reported_user_id/post_id/comment_id`
5. **Topic**: 改 `cover_url`→`icon_url`，添加 `color`, `is_trending`, `updated_at`，移除 `is_official`, `created_by`
6. **Comment**: 添加 `reply_to_user_id` 列
7. **PostView**: 改 `viewed_at`→`created_at`

### 🟡 高优先级 — 7 个模型添加 to_dict()
`Friendship`, `Block`, `Like`, `Conversation`, `Message`, `Notification`, `Report`

### 🟢 中优先级 — 代码逻辑修复
- `interactions.py`: 修复 Comment.to_dict() 参数
- `search.py`: 修复 `db.case()` → `case()`
- 统一测试脚本与路由路径

---

## 附录：数据库完整表结构

<details>
<summary>点击展开</summary>

- **blocks**: id(PK), blocker_id, blocked_id, created_at
- **comments**: id(PK), content, user_id, post_id, parent_id, created_at, updated_at, reply_to_user_id
- **conversations**: id(PK), user1_id, user2_id, created_at, updated_at, last_message_at
- **conversation_participants**: id(PK), conversation_id, user_id, joined_at
- **friendships**: id(PK), sender_id, receiver_id, status, created_at, updated_at
- **likes**: id(PK), user_id, post_id(NOT NULL), created_at, comment_id
- **messages**: id(PK), conversation_id, sender_id, content, message_type, media_url, related_id, is_read(tinyint), created_at
- **moderation_logs**: id(PK), user_id, content_type, original_text, reasons, created_at
- **notifications**: id(PK), user_id, sender_id, notification_type, title, content, related_id, related_type, is_read(tinyint), created_at
- **post_topics**: post_id(PK), topic_id(PK), created_at
- **post_views**: id(PK), user_id, post_id, created_at
- **post_visibility**: post_id(PK), user_id(PK), created_at
- **posts**: id(PK), content, image_url, video_url, post_type, user_id, created_at, updated_at, is_public, visibility(default:public), view_count(default:0)
- **reports**: id(PK), reporter_id, reason, target_type, target_id, description, status, created_at, resolved_at
- **search_history**: id(PK), user_id, query, search_type, created_at
- **sensitive_words**: id(PK), word(UNIQUE), created_at
- **shares**: id(PK), user_id, post_id, created_at
- **topic_followers**: user_id(PK), topic_id(PK), created_at
- **topics**: id(PK), name(UNIQUE), description, icon_url, color, post_count, follower_count, is_trending(tinyint), created_at, updated_at
- **users**: id(PK), username(UNIQUE), email(UNIQUE), password_hash, bio, avatar_url, cover_photo_url, created_at, updated_at, is_active, profile_visibility, post_default_visibility, show_email, allow_search, allow_friend_requests, notify_push, notify_message, notify_sound, is_admin

</details>