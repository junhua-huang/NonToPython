# Facebook Clone Backend API - 完整文档 v2.6.0

**版本**: v2.6.0 | **最后更新**: 2026-06-01  
**开发者**: Facebook Clone Team | **许可证**: MIT

---

## 📋 目录

- [项目概述](#项目概述)
- [功能特性](#功能特性)
- [技术栈](#技术栈)
- [安装与配置](#安装与配置)
- [API文档](#api文档)
- [数据库模型](#数据库模型)
- [WebSocket实时通信](#websocket实时通信)
- [使用示例](#使用示例)
- [项目结构](#项目结构)
- [测试指南](#测试指南)
- [更新日志](#更新日志)
- [开发指南](#开发指南)
- [常见问题](#常见问题)

---

## 项目概述

基于 Flask 框架开发的 Facebook 克隆版后端服务，提供完整的社交网络平台功能。包括用户认证、帖子管理、好友系统、实时聊天、推荐算法、通知系统等核心功能。

### 核心亮点

- ✅ **智能推荐系统** - 多维度算法、匹配度评分、个性化推荐
- ✅ **实时通信** - WebSocket支持6种消息类型
- ✅ **隐私保护** - 细粒度可见性控制
- ✅ **性能优化** - 图片自动压缩、无限滚动加载
- ✅ **完整测试** - 自动化测试覆盖所有功能

---

## 功能特性

### ✅ 核心功能（v1.0.0 - v2.0.0）

1. **用户系统**
   - 注册/登录/认证（JWT Token）
   - 个人资料管理
   - 头像/封面照片上传

2. **社交功能**
   - 好友请求系统
   - 好友列表管理
   - 关系状态查询

3. **内容管理**
   - 帖子 CRUD（创建、查询、更新、删除）
   - 评论和回复系统
   - 点赞功能
   - 动态消息流

4. **实时通信**
   - WebSocket 聊天
   - 6种消息类型（文本/图片/视频/文件/帖子卡片/评论卡片）
   - 在线状态追踪
   - 正在输入提示
   - 消息已读回执

5. **通知系统**
   - 6种通知类型
   - 实时推送
   - 未读统计
   - 批量操作

6. **搜索功能**
   - 用户搜索
   - 帖子搜索
   - 全局搜索
   - 标签搜索
   - 自动补全

7. **话题系统**
   - 话题管理
   - 关注/取消关注
   - 热门算法
   - 自动提取标签

8. **@提及功能**
   - 自动识别 @用户
   - 发送通知
   - 智能建议

9. **文件上传**
   - 图片/视频上传
   - 多文件上传
   - 文件删除
   - 安全检查

### ✨ v2.1.0 新增功能

10. **私信卡片消息**
    - 帖子卡片分享
    - 评论卡片分享
    - 6种消息类型完整支持

### ✨ v2.2.0 新增功能

11. **推荐算法系统**
    - 个性化推荐动态
    - 热门帖子
    - 推荐用户
    - 相关帖子

12. **朋友圈可见性控制**
    - 公开（public）
    - 好友可见（friends）
    - 私密（private）
    - 自定义（custom）

13. **无限滚动加载**
    - has_more字段支持
    - 前端完整实现示例

14. **图片压缩优化**
    - 自动压缩60-80%
    - 智能缩放
    - 质量自适应

### ✨ v2.6.0 (2026-06-01)

**新增端点文档：**
- ✅ 认证模块新增7个端点（修改密码、删除账户、密码重置、隐私设置、Token刷新）
- ✅ 帖子模块新增3个端点（点赞过的帖子、浏览记录、帖子统计）
- ✅ 互动模块新增2个端点（评论点赞/取消点赞）
- ✅ 好友模块新增2个端点（好友计数、好友推荐）
- ✅ 新增屏蔽模块（3个端点）
- ✅ 新增举报模块（3个端点）
- ✅ 新增管理模块（敏感词管理，3个端点）
- ✅ 新增数据库健康检查端点
- ✅ 上传模块迁移至腾讯云COS预签名URL模式
- ✅ 通知设置字段更新（notify_push/notify_message/notify_sound）

**API端点总数：100+**

---

### ✨ v2.5.0 (2026-04-26)

**文档同步：**
- ✅ 完整API端点梳理和文档更新
- ✅ 推荐系统算法版本统一（feed v2, trending v2, friends v3）
- ✅ WebSocket事件完整记录（新增mark_notification_read）
- ✅ 所有路由蓝图前缀规范为 /api
- ✅ 搜索历史功能完善（保存/查询/清空）
- ✅ @提及用户建议接口文档补充

**技术状态：**
- 测试成功率维持在 95%+
- 核心功能 100% 覆盖
- API端点总数：80+

---

### ✨ v2.4.0 (2026-04-22)

**Bug修复：**
- ✅ 修复 Post.get_topics() 方法缺失导致的 500 错误
- ✅ 添加 post_visibility 表定义（自定义可见范围）
- ✅ 修复 JWT Identity 类型转换问题（字符串 vs 整数）
- ✅ 修复循环导入问题（notification_service.py）
- ✅ 修复模块导入路径错误（3个文件）
- ✅ 修复 SQLAlchemy 查询顺序错误（suggest_users）
- ✅ 优化测试脚本预期（级联删除处理）

**技术改进：**
- 统一所有路由的 get_jwt_identity() 类型转换
- 修复 socketio 实例获取方式（避免循环导入）
- 优化数据库查询性能（子查询显式转换）
- 完善错误处理和异常捕获

**测试覆盖：**
- 测试成功率从 75% 提升至 95.74%
- 47个测试用例，45个通过
- 核心功能 100% 覆盖

---

### ✨ v2.3.0 (2026-04-21)

## 技术栈

### 后端框架
- **Web框架**: Flask 3.0
- **数据库ORM**: SQLAlchemy 2.0+
- **认证**: Flask-JWT-Extended 4.6
- **实时通信**: Flask-SocketIO 5.3 + WebSocket (threading模式)

### 数据库
- **数据库**: MySQL (PyMySQL)
- **连接池**: 大小10，回收时间3600秒

### 图片处理
- **库**: Pillow 10.2.0
- **功能**: 自动压缩、智能缩放、质量优化

### 文件存储
- **方式**: 本地文件系统
- **位置**: uploads/ 目录
- **限制**: 最大50MB

### 兼容性
- **Python**: 3.10-3.14
- **异步模式**: threading（兼容Python 3.14）

---

## 安装与配置

### 1. 环境要求

```bash
Python 3.10+
MySQL 5.7+ 或 8.0+
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

**主要依赖：**
- Flask==3.0.0
- Flask-SQLAlchemy==3.1.1
- Flask-JWT-Extended==4.6.0
- Flask-SocketIO==5.3.6
- PyMySQL==1.1.0
- Pillow==10.2.0
- Werkzeug==3.0.1

### 3. 配置数据库

编辑 `config.py` 文件：

```python
SQLALCHEMY_DATABASE_URI = 'mysql+pymysql://username:password@localhost/facebook_clone'
SQLALCHEMY_TRACK_MODIFICATIONS = False
SECRET_KEY = 'your-secret-key-here'
JWT_SECRET_KEY = 'your-jwt-secret-key-here'
```

### 4. 创建数据库

```sql
CREATE DATABASE facebook_clone CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 5. 运行应用

```bash
python app.py
```

应用将在 `http://localhost:5000` 启动

**注意：** 首次运行时会自动创建所有数据库表。

### 6. 验证安装

```bash
curl http://localhost:5000/health
```

应返回：
```json
{
  "status": "healthy",
  "message": "Facebook Clone API is running"
}
```

---

## API文档

### 基础URL
```
http://localhost:5000/api
```

### 认证方式

所有受保护的端点需要在请求头中包含JWT Token：

```http
Authorization: Bearer <your_jwt_token>
```

---

### 认证相关 (`/api/auth`)

#### 注册用户
```http
POST /api/auth/register
Content-Type: application/json

{
  "username": "john_doe",
  "email": "john@example.com",
  "password": "securepassword",
  "first_name": "John",
  "last_name": "Doe",
  "bio": "Hello World!"
}
```

#### 用户登录
```http
POST /api/auth/login
Content-Type: application/json

{
  "email": "john@example.com",
  "password": "securepassword"
}

响应:
{
  "message": "Login successful",
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "user": {...}
}
```

#### 获取当前用户信息
```http
GET /api/auth/profile
Authorization: Bearer <token>
```

#### 更新用户资料
```http
PUT /api/auth/profile
Authorization: Bearer <token>
Content-Type: application/json

{
  "first_name": "John",
  "last_name": "Doe",
  "bio": "Updated bio",
  "avatar_url": "https://example.com/avatar.jpg"
}
```

#### 获取指定用户信息
```http
GET /api/auth/users/<user_id>
Authorization: Bearer <token>
```

#### 修改密码
```http
POST /api/auth/change-password
Authorization: Bearer <token>
Content-Type: application/json

{
  "current_password": "oldpassword",
  "new_password": "NewPassword123"
}
```

#### 忘记密码（获取重置Token）
```http
POST /api/auth/forgot-password
Content-Type: application/json

{
  "username": "john_doe",
  "email": "john@example.com"
}

响应:
{
  "message": "Reset token generated",
  "reset_token": "eyJhbGciOi..."
}
```

#### 重置密码
```http
PUT /api/auth/reset-password
Content-Type: application/json

{
  "reset_token": "eyJhbGciOi...",
  "new_password": "NewPassword123"
}
```

#### 刷新Token
```http
POST /api/auth/refresh
Authorization: Bearer <token>
```

#### 获取隐私设置
```http
GET /api/auth/privacy
Authorization: Bearer <token>
```

#### 更新隐私设置
```http
PUT /api/auth/privacy
Authorization: Bearer <token>
Content-Type: application/json

{
  "profile_visibility": "public",        // public/friends_only/private
  "post_default_visibility": "friends_only", // public/friends_only
  "show_email": true,
  "allow_search": true,
  "allow_friend_requests": "everyone"     // everyone/friends_of_friends/none
}
```

#### 删除账户
```http
DELETE /api/auth/account
Authorization: Bearer <token>
```

---

### 帖子相关 (`/api/posts`)

#### 创建帖子
```http
POST /api/posts/
Authorization: Bearer <token>
Content-Type: multipart/form-data

content=Hello, this is my first post!
visibility=public  # public/friends/private/custom
visible_user_ids=1,2,3  # 仅 custom 时需要
image=[图片文件]  # 可选，自动压缩优化
```

**可见性类型：**
- `public`: 公开（所有好友可见）
- `friends`: 仅好友可见
- `private`: 私密（仅自己可见）
- `custom`: 自定义（指定用户列表）

**图片优化：**
- 自动压缩60-80%
- 智能缩放至最大1920x1080
- JPEG质量优化（默认85%）

#### 获取动态消息
```http
GET /api/posts/?page=1&per_page=20
Authorization: Bearer <token>

响应:
{
  "posts": [...],
  "has_more": true,  // 用于无限滚动
  "current_page": 1,
  "pages": 10
}
```

#### 获取用户帖子
```http
GET /api/posts/user/<user_id>?page=1&per_page=20
Authorization: Bearer <token>
```

#### 获取单个帖子
```http
GET /api/posts/<post_id>
Authorization: Bearer <token>
```

#### 更新帖子
```http
PUT /api/posts/<post_id>
Authorization: Bearer <token>
Content-Type: application/json

{
  "content": "Updated content"
}
```

#### 删除帖子
```http
DELETE /api/posts/<post_id>
Authorization: Bearer <token>
```

#### 获取用户点赞过的帖子
```http
GET /api/posts/user/<user_id>/liked?page=1&per_page=20
Authorization: Bearer <token>
```

#### 记录帖子浏览
```http
POST /api/posts/<post_id>/view
Authorization: Bearer <token>

同一用户对同一帖子只计一次浏览。
```

#### 获取帖子统计
```http
GET /api/posts/<post_id>/stats
Authorization: Bearer <token>

响应:
{
  "post_id": 123,
  "views": 50,
  "likes": 25,
  "comments": 10
}
```

---

### 好友系统 (`/api/friends`)

#### 发送好友请求
```http
POST /api/friends/request
Authorization: Bearer <token>
Content-Type: application/json

{
  "receiver_id": 2
}
```

#### 接受好友请求
```http
POST /api/friends/request/<request_id>/accept
Authorization: Bearer <token>
```

#### 拒绝好友请求
```http
POST /api/friends/request/<request_id>/reject
Authorization: Bearer <token>
```

#### 获取好友列表
```http
GET /api/friends/
Authorization: Bearer <token>
```

#### 获取待处理请求
```http
GET /api/friends/requests/pending
Authorization: Bearer <token>
```

#### 取消好友请求
```http
DELETE /api/friends/request/<request_id>
Authorization: Bearer <token>
```

#### 删除好友
```http
DELETE /api/friends/<user_id>
Authorization: Bearer <token>
```

#### 检查好友关系状态
```http
GET /api/friends/status/<user_id>
Authorization: Bearer <token>
```

#### 获取用户好友数量
```http
GET /api/friends/count/<user_id>
Authorization: Bearer <token>
```

#### 简单好友推荐
```http
GET /api/friends/recommendations
Authorization: Bearer <token>

随机推荐10个非好友的活跃用户。
```

---

### 互动功能 (`/api`)

#### 点赞帖子
```http
POST /api/posts/<post_id>/like
Authorization: Bearer <token>
```

#### 取消点赞
```http
DELETE /api/posts/<post_id>/like
Authorization: Bearer <token>
```

#### 创建评论
```http
POST /api/posts/<post_id>/comments
Authorization: Bearer <token>
Content-Type: application/json

{
  "content": "Great post!",
  "parent_id": null  // 可选，用于回复评论
}
```

#### 获取评论列表
```http
GET /api/posts/<post_id>/comments?page=1&per_page=20
Authorization: Bearer <token>
```

#### 获取单个评论
```http
GET /api/comments/<comment_id>
Authorization: Bearer <token>

注意：此接口实际返回该评论的回复列表（子评论）。
```

#### 点赞评论
```http
POST /api/comments/<comment_id>/like
Authorization: Bearer <token>
```

#### 取消评论点赞
```http
DELETE /api/comments/<comment_id>/like
Authorization: Bearer <token>
```

#### 更新评论
```http
PUT /api/comments/<comment_id>
Authorization: Bearer <token>
Content-Type: application/json

{
  "content": "Updated comment"
}
```

#### 删除评论
```http
DELETE /api/comments/<comment_id>
Authorization: Bearer <token>

注意：评论作者或帖子作者均可删除评论。
```

#### 获取点赞列表
```http
GET /api/posts/<post_id>/likes
Authorization: Bearer <token>
```

---

### 推荐系统 (`/api/recommendations`)

#### 获取个性化推荐动态
```http
GET /api/recommendations/feed?page=1&per_page=20
Authorization: Bearer <token>

算法考虑因素：
- 好友的帖子（权重最高）
- 互动频繁的用户的帖子
- 热门帖子（点赞和评论多）
- 时间衰减（新帖子权重更高）
- 话题兴趣匹配

响应:
{
  "posts": [...],
  "total": 100,
  "pages": 5,
  "current_page": 1,
  "per_page": 20,
  "algorithm": "personalized_v1"
}
```

#### 获取热门帖子
```http
GET /api/recommendations/trending?limit=10&hours=24
Authorization: Bearer <token>
```

#### 推荐可能认识的人（基础版）
```http
GET /api/recommendations/users/suggest?limit=10
Authorization: Bearer <token>
```

#### 智能推荐好友（增强版）✨ v2.3.0
```http
GET /api/recommendations/friends/recommend?limit=10
Authorization: Bearer <token>

算法考虑因素：
- 二度人脉（好友的好友）- 权重10分/人
- 共同话题数量及热度 - 权重5分 + bonus
- 活跃度匹配（最近7天）- 权重2分
- 话题流行度加分 - 最高20分

响应:
{
  "recommendations": [
    {
      "id": 5,
      "username": "alice",
      "full_name": "Alice Smith",
      "avatar_url": "...",
      "recommendation_score": 45,
      "match_percentage": 90,
      "mutual_friends_count": 3,
      "common_topics_count": 2,
      "recommendation_reasons": [
        "3 个共同好友",
        "2 个共同话题",
        "活跃用户"
      ]
    }
  ],
  "total_candidates": 15,
  "algorithm": "smart_friend_recommendation_v2",
  "factors": [
    "mutual_friends (weight: 10)",
    "common_topics (weight: 5)",
    "activity_level (weight: 2)",
    "topic_popularity (bonus)"
  ]
}
```

#### 获取相关帖子推荐
```http
GET /api/recommendations/posts/<post_id>/related?limit=5
Authorization: Bearer <token>
```

---

### 实时聊天 (`/api/chat` + WebSocket)

#### HTTP API端点

##### 获取会话列表
```http
GET /api/chat/conversations
Authorization: Bearer <token>
```

##### 获取或创建会话
```http
GET /api/chat/conversations/<user_id>
Authorization: Bearer <token>
```

##### 获取消息历史
```http
GET /api/chat/conversations/<conversation_id>/messages?page=1&per_page=50
Authorization: Bearer <token>
```

##### 标记消息已读
```http
POST /api/chat/conversations/<conversation_id>/mark-read
Authorization: Bearer <token>
```

##### 获取在线用户
```http
GET /api/chat/users/online
Authorization: Bearer <token>
```

##### 获取用户在线状态
```http
GET /api/chat/users/<user_id>/status
Authorization: Bearer <token>
```

##### 获取未读消息数
```http
GET /api/chat/unread-count
Authorization: Bearer <token>
```

#### WebSocket事件

**连接地址：** `ws://localhost:5000?token=YOUR_JWT_TOKEN`

**支持的消息类型：**
1. `text` - 文本消息
2. `image` - 图片消息
3. `video` - 视频消息
4. `file` - 文件消息
5. `post` - 帖子卡片 ✨
6. `comment` - 评论卡片 ✨

**客户端 → 服务器：**
- `send_message`: 发送消息
- `mark_as_read`: 标记消息已读
- `typing`: 正在输入状态
- `mark_notification_read`: 标记通知为已读 ✨ v2.5.0

**服务器 → 客户端：**
- `receive_message`: 接收新消息
- `message_sent`: 消息发送确认
- `message_read`: 消息已读通知
- `user_status`: 用户在线状态变化
- `user_typing`: 对方正在输入

**发送帖子卡片示例：**
```javascript
socket.emit('send_message', {
  token: 'YOUR_JWT_TOKEN',
  receiver_id: 2,
  content: '看看这个帖子',
  message_type: 'post',
  related_id: 123  // 帖子ID
});
```

---

### 搜索功能 (`/api/search`)

#### 搜索用户
```http
GET /api/search/users?q=关键词&page=1&per_page=20
Authorization: Bearer <token>
```

#### 搜索帖子
```http
GET /api/search/posts?q=关键词&page=1&per_page=20
Authorization: Bearer <token>
```

#### 全局综合搜索
```http
GET /api/search/global?q=关键词&page=1&per_page=10
Authorization: Bearer <token>
```

#### 标签搜索
```http
GET /api/search/hashtag/python?page=1&per_page=20
Authorization: Bearer <token>
```

#### 获取热门标签
```http
GET /api/search/trending-hashtags?limit=10
Authorization: Bearer <token>
```

#### 用户自动补全建议
```http
GET /api/search/suggest/users?prefix=关键词&limit=5
Authorization: Bearer <token>
```

#### 获取搜索历史
```http
GET /api/search/history?limit=20
Authorization: Bearer <token>
```

#### 保存搜索历史
```http
POST /api/search/history
Authorization: Bearer <token>
Content-Type: application/json

{
  "query": "Python",
  "type": "global"  // users/posts/global/hashtag
}
```

#### 清空搜索历史
```http
DELETE /api/search/history
Authorization: Bearer <token>
```

#### @提及用户建议
```http
GET /api/search/mention-suggestions?prefix=关键词&limit=5
Authorization: Bearer <token>

响应示例:
{
  "suggestions": [
    {
      "id": 2,
      "username": "john_doe",
      "full_name": "John Doe",
      "avatar_url": "/uploads/avatars/2/xxx.jpg",
      "is_friend": true  // 是否为好友
    }
  ],
  "total": 1
}
```

---

### 话题系统 (`/api/topics`)

#### 获取所有话题
```http
GET /api/topics/?page=1&per_page=20&q=搜索词
Authorization: Bearer <token>
```

#### 获取热门话题
```http
GET /api/topics/trending?limit=10
Authorization: Bearer <token>
```

#### 获取单个话题详情
```http
GET /api/topics/<topic_id>
Authorization: Bearer <token>
```

#### 根据名称获取话题
```http
GET /api/topics/name/<topic_name>
Authorization: Bearer <token>
```

#### 获取话题下的帖子
```http
GET /api/topics/<topic_id>/posts?page=1&per_page=20
Authorization: Bearer <token>
```

#### 创建话题
```http
POST /api/topics/
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "Python",
  "description": "Python编程讨论",
  "icon_url": "/uploads/icons/python.png",
  "color": "#3b82f6"
}
```

#### 关注话题
```http
POST /api/topics/<topic_id>/follow
Authorization: Bearer <token>
```

#### 取消关注话题
```http
POST /api/topics/<topic_id>/unfollow
Authorization: Bearer <token>
```

#### 获取关注的话题列表
```http
GET /api/topics/followed?page=1&per_page=20
Authorization: Bearer <token>
```

#### 更新话题
```http
PUT /api/topics/<topic_id>
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "Updated Topic Name",
  "description": "Updated description",
  "color": "#ff5722"
}
```

#### 删除话题
```http
DELETE /api/topics/<topic_id>
Authorization: Bearer <token>
```

#### 获取推荐话题
```http
GET /api/topics/suggest?limit=10
Authorization: Bearer <token>
```

---

### 推荐系统 (`/api/recommendations`) ✨ v2.5.0

#### 获取个性化推荐动态 (v2)
```http
GET /api/recommendations/feed?page=1&per_page=20
Authorization: Bearer <token>
```

**算法特性 (personalized_v2):**
- ✅ 好友帖子优先 (80分权重)
- ✅ 互动频率分析 (点赞1分, 评论2分, 上限30分)
- ✅ 话题兴趣匹配 (40分权重)
- ✅ 帖子热度评估 (上限20分)
- ✅ 指数时间衰减 `1/√(t+1)`
- ✅ 多样性控制 (限制同作者帖子数)
- ✅ 冷启动处理 (新用户30天窗口)

**响应示例:**
```json
{
  "posts": [...],
  "total": 100,
  "pages": 5,
  "current_page": 1,
  "per_page": 20,
  "algorithm": "personalized_v2",
  "is_new_user": false,
  "diversity_applied": true
}
```

---

#### 获取热门帖子 (v2)
```http
GET /api/recommendations/trending?limit=10&hours=24
Authorization: Bearer <token>
```

**参数说明:**
- `limit`: 返回数量 (默认10)
- `hours`: 时间窗口 (默认24小时)

**算法特性 (trending_v2):**
- ✅ 综合评分 = 互动量 × 时间衰减
- ✅ 评论权重×2, 去重防刷
- ✅ 指数衰减因子 `1/(hours+1)^0.7`
- ✅ 多样性控制 (限制同作者)

**响应示例:**
```json
{
  "posts": [
    {
      "id": 123,
      "content": "...",
      "engagement_score": 45.5,
      "time_decay_factor": 0.6234
    }
  ],
  "time_window_hours": 24,
  "algorithm": "trending_v2",
  "diversity_applied": true
}
```

---

#### 基础用户推荐
```http
GET /api/recommendations/users/suggest?limit=10
Authorization: Bearer <token>
```

**算法特性 (social_graph_v1):**
- 二度人脉 (共同好友 ×2)
- 共同话题 (×1)
- 注册时间排序

**响应示例:**
```json
{
  "suggestions": [
    {
      "id": 44,
      "username": "abc",
      "mutual_friends_count": 2,
      "common_topics_count": 3,
      "recommendation_reason": ["2 个共同好友", "3 个共同话题"]
    }
  ],
  "algorithm": "social_graph_v1"
}
```

---

#### 智能好友推荐 (v3) ⭐⭐⭐
```http
GET /api/recommendations/friends/recommend?limit=10
Authorization: Bearer <token>
```

**算法特性 (smart_friend_recommendation_v3):**
- ✅ 二度人脉 (共同好友 ×15分)
- ✅ 共同话题 (×8分 + 热度bonus上限30)
- ✅ 活跃度匹配 (×3分, 上限20)
- ✅ 注册时间相近 (+5分)
- ✅ 探索机制 (随机引入新用户)
- ✅ Sigmoid匹配度计算

**评分因素:**
```
mutual_friends (weight: 15)
common_topics (weight: 8 + popularity bonus)
activity_level (weight: 3, max 20)
registration_time (bonus: 5)
exploration (bonus: 10)
```

**响应示例:**
```json
{
  "recommendations": [
    {
      "id": 44,
      "username": "abc",
      "full_name": "ABC User",
      "avatar_url": "/uploads/avatars/44/xxx.jpg",
      "recommendation_score": 60,
      "match_percentage": 88,
      "mutual_friends_count": 2,
      "common_topics_count": 3,
      "recommendation_reasons": [
        "2 个共同好友",
        "3 个共同话题",
        "活跃用户"
      ]
    }
  ],
  "total_candidates": 15,
  "algorithm": "smart_friend_recommendation_v3",
  "factors": [
    "mutual_friends (weight: 15)",
    "common_topics (weight: 8 + popularity bonus)",
    "activity_level (weight: 3, max 20)",
    "registration_time (bonus: 5)",
    "exploration (bonus: 10)"
  ]
}
```

---

#### 获取相关帖子推荐
```http
GET /api/recommendations/posts/<post_id>/related?limit=5
Authorization: Bearer <token>
```

**算法特性 (related_v1):**
- 相同话题优先 (100分)
- 相同作者
- 时间倒序

**响应示例:**
```json
{
  "posts": [...],
  "algorithm": "related_v1"
}
```

---

### 文件上传 (`/api/upload`)

> **注意：** v2.6.0 已将上传模式迁移至腾讯云 COS 预签名 URL 模式，旧版本地文件上传端点已移除。

#### 生成预签名上传URL（客户端直传COS）
```http
POST /api/upload/presign
Authorization: Bearer <token>
Content-Type: application/json

{
  "file_type": "image",           // image/video/any
  "subfolder": "posts/47",        // 可选，默认为 general/<user_id>
  "count": 1                      // 预签名URL数量，最大20
}

响应:
{
  "message": "1 presigned URL(s) generated",
  "url": "https://xxx.cos.ap-guangzhou.myqcloud.com/...",
  "upload_url": "https://xxx.cos.ap-guangzhou.myqcloud.com/...",
  "access_url": "https://xxx.cos.ap-guangzhou.myqcloud.com/...",
  "cos_key": "uploads/temp/xxx.jpg",
  "items": [...]
}
```

#### 确认上传完成
```http
POST /api/upload/confirm
Authorization: Bearer <token>
Content-Type: application/json

{
  "cos_key": "uploads/temp/xxx.jpg",
  "final_filename": "my_photo.jpg"
}
```

#### 确认头像上传
```http
POST /api/upload/avatar/confirm
Authorization: Bearer <token>
Content-Type: application/json

{
  "url": "https://xxx.cos.ap-guangzhou.myqcloud.com/..."
}
```

#### 确认封面上传
```http
POST /api/upload/cover/confirm
Authorization: Bearer <token>
Content-Type: application/json

{
  "url": "https://xxx.cos.ap-guangzhou.myqcloud.com/..."
}
```

#### 删除文件
```http
POST /api/upload/delete
Authorization: Bearer <token>
Content-Type: application/json

{
  "url": "https://xxx.cos.ap-guangzhou.myqcloud.com/..."
}
```

#### 获取文件信息
```http
GET /api/upload/info?url=https://xxx.cos.ap-guangzhou.myqcloud.com/...
Authorization: Bearer <token>
```

#### 访问上传的文件（302重定向到COS）
```http
GET /api/upload/uploads/<path:filename>
```

---

### 通知系统 (`/api/notifications`)

#### 获取通知列表
```http
GET /api/notifications/?page=1&per_page=20
Authorization: Bearer <token>
```

#### 标记通知为已读
```http
POST /api/notifications/<notification_id>/read
Authorization: Bearer <token>
```

#### 标记所有通知为已读
```http
POST /api/notifications/mark-all-read
Authorization: Bearer <token>
```

#### 删除单个通知
```http
DELETE /api/notifications/<notification_id>
Authorization: Bearer <token>
```

#### 清空所有通知
```http
DELETE /api/notifications/clear-all
Authorization: Bearer <token>
```

#### 获取未读数量
```http
GET /api/notifications/unread-count
Authorization: Bearer <token>
```

#### 获取通知设置
```http
GET /api/notifications/settings
Authorization: Bearer <token>

响应:
{
  "settings": {
    "notify_push": true,
    "notify_message": true,
    "notify_sound": true
  }
}
```

#### 更新通知设置
```http
PUT /api/notifications/settings
Authorization: Bearer <token>
Content-Type: application/json

{
  "notify_push": true,       // 推送通知
  "notify_message": true,     // 消息通知
  "notify_sound": true        // 声音通知
}
```

---

### 屏蔽管理 (`/api/blocks`)

#### 屏蔽用户
```http
POST /api/blocks
Authorization: Bearer <token>
Content-Type: application/json

{
  "blocked_id": 99
}
```

#### 获取屏蔽列表
```http
GET /api/blocks?page=1&per_page=20
Authorization: Bearer <token>
```

#### 取消屏蔽
```http
DELETE /api/blocks/<blocked_id>
Authorization: Bearer <token>
```

---

### 举报系统 (`/api/reports`)

#### 举报帖子
```http
POST /api/reports/post
Authorization: Bearer <token>
Content-Type: application/json

{
  "post_id": 456,
  "reason": "spam",
  "description": "垃圾广告内容"
}
```

#### 举报评论
```http
POST /api/reports/comment
Authorization: Bearer <token>
Content-Type: application/json

{
  "comment_id": 789,
  "reason": "harassment",
  "description": "骚扰言论"
}
```

#### 举报用户
```http
POST /api/reports/user
Authorization: Bearer <token>
Content-Type: application/json

{
  "user_id": 321,
  "reason": "impersonation",
  "description": "冒充他人"
}
```

---

### 管理接口 (`/api/admin`)

#### 获取敏感词列表
```http
GET /api/admin/blocked_words
```

#### 添加敏感词
```http
POST /api/admin/blocked_words
Content-Type: application/json

{
  "word": "敏感词"
}
```

#### 删除敏感词
```http
DELETE /api/admin/blocked_words
Content-Type: application/json

{
  "word": "敏感词"
}
```

---

### 健康检查 (`/`)

#### API健康检查
```http
GET /health
```

#### 数据库健康检查
```http
GET /api/health/db

响应:
{
  "database": "connected",
  "connection_pool": {
    "size": 10,
    "checked_in_connections": 5,
    "overflow": 0,
    "total": 10
  }
}
```

---

## 数据库模型

### User (用户)
- id, username, email, password_hash
- first_name, last_name, bio
- avatar_url, cover_photo_url
- created_at, updated_at

### Post (帖子)
- id, content, image_url, video_url
- post_type, user_id
- **visibility** (public/friends/private/custom) ✨
- is_public (向后兼容)
- created_at, updated_at

**自定义可见范围表：**
- post_visibility (post_id, user_id, created_at)

### Comment (评论)
- id, content, user_id, post_id
- parent_id (用于回复)
- created_at, updated_at

### Like (点赞)
- id, user_id, post_id, created_at

### Friendship (好友关系)
- id, sender_id, receiver_id
- status (pending/accepted/rejected)
- created_at, updated_at

### Conversation (会话)
- id, user1_id, user2_id
- last_message_at
- created_at, updated_at

### Message (消息)
- id, conversation_id, sender_id
- content, message_type, media_url
- **related_id** (相关对象ID) ✨
- is_read, created_at

**支持的消息类型：**
- `text`: 文本消息
- `image`: 图片消息
- `video`: 视频消息
- `file`: 文件消息
- `post`: 帖子卡片 ✨
- `comment`: 评论卡片 ✨

### Notification (通知)
- id, user_id, sender_id
- notification_type, title, content
- related_id, related_type
- is_read, created_at

### Topic (话题)
- id, name, description
- icon_url, color
- post_count, follower_count
- is_trending
- created_at, updated_at

---

## 使用示例

### Python Requests示例

```python
import requests

BASE_URL = 'http://localhost:5000/api'

# 注册
response = requests.post(f'{BASE_URL}/auth/register', json={
    'username': 'testuser',
    'email': 'test@example.com',
    'password': 'password123',
    'first_name': 'Test',
    'last_name': 'User'
})
print(response.json())

# 登录
response = requests.post(f'{BASE_URL}/auth/login', json={
    'email': 'test@example.com',
    'password': 'password123'
})
token = response.json()['access_token']

# 创建帖子
headers = {'Authorization': f'Bearer {token}'}
response = requests.post(f'{BASE_URL}/posts/', 
                        headers=headers,
                        data={'content': 'Hello World!'})
print(response.json())

# 获取智能推荐好友
response = requests.get(
    f'{BASE_URL}/recommendations/friends/recommend?limit=10',
    headers=headers
)
data = response.json()
for user in data['recommendations']:
    print(f"{user['full_name']} - 匹配度: {user['match_percentage']}%")
    print(f"  理由: {', '.join(user['recommendation_reasons'])}")
```

### JavaScript WebSocket示例

```javascript
const socket = io('http://localhost:5000', {
  query: { token: 'YOUR_JWT_TOKEN' }
});

// 连接成功
socket.on('connect', () => {
  console.log('Connected to chat');
});

// 接收消息
socket.on('receive_message', (data) => {
  console.log('New message:', data);
});

// 发送消息
socket.emit('send_message', {
  token: 'YOUR_JWT_TOKEN',
  receiver_id: 2,
  content: '你好！',
  message_type: 'text'
});

// 发送帖子卡片
socket.emit('send_message', {
  token: 'YOUR_JWT_TOKEN',
  receiver_id: 2,
  content: '看看这个帖子',
  message_type: 'post',
  related_id: 123
});
```

---

## 项目结构

```
PyCharmMiscProject/
├── app.py                      # 主应用文件（工厂函数）
├── config.py                   # 配置文件
├── models.py                   # 数据库模型
├── utils.py                    # 工具函数
├── requirements.txt            # 依赖包
│
├── routes/                     # 路由层
│   ├── routes_auth.py          # 认证路由
│   ├── routes_posts.py         # 帖子路由
│   ├── routes_friends.py       # 好友路由
│   ├── routes_interactions.py  # 互动路由
│   ├── routes_chat.py          # 聊天路由
│   ├── routes_notifications.py # 通知路由
│   ├── routes_search.py        # 搜索路由
│   ├── routes_topics.py        # 话题路由
│   ├── routes_upload.py        # 上传路由
│   └── routes_recommendations.py # 推荐路由 ✨
│
├── services/                   # 服务层
│   ├── notification_service.py # 通知服务
│   ├── search_service.py       # 搜索服务
│   ├── topic_service.py        # 话题服务
│   ├── mention_service.py      # @提及服务
│   ├── recommendation_service.py # 推荐算法服务 ✨
│   └── image_optimizer.py      # 图片优化服务 ✨
│
├── tests/                      # 测试目录
│   ├── test_complete_v2.2.py   # 完整功能测试
│   ├── test_friend_recommendations.py # 推荐好友测试 ✨
│   ├── quick_test.py           # 快速验证
│   └── test_api.py             # 基础测试
│
├── docs/                       # 文档目录
│   ├── README.md               # 主文档
│   ├── CHANGELOG.md            # 统一更新日志
│   ├── CHANGELOG_v2.3.0.md     # v2.3.0详细说明
│   ├── FEATURES_IMPLEMENTATION.md # 功能实现详解
│   ├── api_documentation.json  # OpenAPI文档
│   ├── websocket_events.json   # WebSocket事件
│   ├── infinite_scroll_example.js # 无限滚动示例
│   └── message_card_example.js # 消息卡片示例
│
├── uploads/                    # 上传文件目录
└── socket_handlers.py          # WebSocket事件处理
```

---

## 测试指南

### 快速测试

```bash
# 运行完整测试
python tests/test_complete_v2.2.py

# 测试推荐好友功能
python tests/test_friend_recommendations.py

# 快速验证
python tests/quick_test.py
```

### Windows便捷测试

双击运行：
```bash
run_tests.bat
```

### 测试覆盖

- ✅ 健康检查
- ✅ 用户认证（注册、登录）
- ✅ 用户资料管理
- ✅ 帖子管理（含可见性控制）
- ✅ 互动功能（点赞、评论、@提及）
- ✅ 好友系统
- ✅ 搜索功能
- ✅ 话题系统
- ✅ 通知系统
- ✅ 聊天系统
- ✅ 推荐系统（个性化动态、热门帖子）
- ✅ 智能推荐好友 ✨
- ✅ 图片优化服务

---

## 更新日志

### v2.4.0 (2026-04-22) 🔥 最新版本

**Bug修复：**
- ✅ 修复 Post.get_topics() 方法缺失导致的 500 错误
- ✅ 添加 post_visibility 表定义（自定义可见范围）
- ✅ 修复 JWT Identity 类型转换问题（字符串 vs 整数）
- ✅ 修复循环导入问题（notification_service.py）
- ✅ 修复模块导入路径错误（3个文件）
- ✅ 修复 SQLAlchemy 查询顺序错误（suggest_users）
- ✅ 优化测试脚本预期（级联删除处理）

**技术改进：**
- 统一所有路由的 get_jwt_identity() 类型转换（10+处）
- 修复 socketio 实例获取方式（避免循环导入）
- 优化数据库查询性能（子查询显式转换）
- 完善错误处理和异常捕获

**测试覆盖：**
- 测试成功率从 75% 提升至 95.74%
- 47个测试用例，45个通过
- 核心功能 100% 覆盖

**修改的文件：**
- models.py - 添加 get_topics()、post_visibility 表
- services/notification_service.py - 修复 socketio 导入
- services/search_service.py - 修复查询顺序
- services/recommendation_service.py - 修复子查询警告
- routes_posts.py - 6处类型转换
- routes_interactions.py - 2处类型转换
- routes_search.py - 2处类型转换 + 导入修复
- socket_handlers.py - 导入路径修复
- tests/test_complete_api.py - 测试预期调整

---

### v2.3.0 (2026-04-21)

**新增功能：**
- ✅ 智能推荐好友系统（多维度算法、匹配度评分）

**算法特性：**
- 二度人脉分析（好友的好友）
- 共同话题及热度计算
- 活跃度匹配（最近7天）
- 综合评分和匹配度百分比
- 多维度推荐理由

**API端点：**
- `GET /api/recommendations/friends/recommend` - 智能推荐好友

**返回数据增强：**
- recommendation_score: 推荐分数
- match_percentage: 匹配度百分比（0-99%）
- recommendation_reasons: 推荐理由列表
- mutual_friends_count: 共同好友数
- common_topics_count: 共同话题数

---

### v2.2.0 (2026-04-21)

**新增功能：**
- ✅ 推荐算法系统（个性化动态、热门帖子、推荐用户、相关帖子）
- ✅ 朋友圈可见性控制（公开/好友/私密/自定义四种类型）
- ✅ 无限滚动加载支持（has_more字段 + 前端完整示例）
- ✅ 图片压缩优化（自动压缩60-80%、智能缩放、质量自适应）

**技术改进：**
- 修复循环导入问题
- 统一模块导入路径
- Python 3.14兼容性（改用threading模式）

**性能提升：**
- 图片文件大小减少60-80%
- 加载速度提升2-3倍
- 存储空间节省60-80%

---

### v2.1.0 (2026-04-21)

**新增功能：**
- ✅ 私信卡片消息系统（帖子卡片、评论卡片）
- ✅ 6种消息类型支持

**技术改进：**
- Message模型新增 `related_id` 字段
- WebSocket处理增强

---

### v2.0.0 (2026-04-21)

**新增功能：**
- ✅ 消息推送通知系统
- ✅ 全站搜索功能
- ✅ 话题系统
- ✅ @提及功能
- ✅ 文件上传系统

**改进：**
- 数据库从 SQLite 迁移到 MySQL
- 优化数据库查询性能
- 项目文件结构优化

---

### v1.0.0 (2026-04-21)

**初始版本：**
- 用户认证系统
- 帖子管理
- 好友系统
- 点赞和评论
- 实时聊天
- 文件上传

---

## 开发指南

### 添加新功能

1. **创建服务层** (`services/`)
   ```python
   # services/new_feature_service.py
   class NewFeatureService:
       @staticmethod
       def do_something():
           pass
   ```

2. **创建路由** (`routes/`)
   ```python
   # routes_new_feature.py
   from flask import Blueprint
   
   new_feature_bp = Blueprint('new_feature', __name__)
   
   @new_feature_bp.route('/endpoint', methods=['GET'])
   @jwt_required()
   def endpoint():
       pass
   ```

3. **注册蓝图** (`app.py`)
   ```python
   from routes_new_feature import new_feature_bp
   app.register_blueprint(new_feature_bp, url_prefix='/api/new-feature')
   ```

4. **更新文档**
   - 更新 `api_documentation.json`
   - 更新 `docs/README.md`
   - 添加测试用例

### 代码规范

- 使用PEP 8编码风格
- 函数和类添加docstring
- 统一的导入路径：`from PyCharmMiscProject.xxx import yyy`
- 错误处理使用try-except
- 数据库操作使用事务

### 性能优化建议

1. **数据库**
   - 添加必要的索引
   - 使用分页避免大量数据
   - 避免N+1查询问题

2. **缓存**
   - 推荐结果缓存（Redis）
   - 热门数据缓存
   - 用户会话缓存

3. **图片**
   - 使用CDN加速
   - 懒加载
   - 渐进式加载

---

## 📋 完整 API 接口清单

### 认证模块 (`/api/auth`) - 12个接口

| 方法 | 路径 | 描述 | 需要认证 |
|------|------|------|----------|
| POST | `/api/auth/register` | 用户注册 | ❌ |
| POST | `/api/auth/login` | 用户登录 | ❌ |
| POST | `/api/auth/forgot-password` | 忘记密码（获取重置Token） | ❌ |
| PUT | `/api/auth/reset-password` | 使用Token重置密码 | ❌ |
| GET | `/api/auth/profile` | 获取当前用户资料 | ✅ |
| PUT | `/api/auth/profile` | 更新用户资料 | ✅ |
| GET | `/api/auth/users/<user_id>` | 获取指定用户资料 | ✅ |
| POST | `/api/auth/change-password` | 修改密码 | ✅ |
| DELETE | `/api/auth/account` | 删除账户 | ✅ |
| POST | `/api/auth/refresh` | 刷新JWT Token | ✅ |
| GET | `/api/auth/privacy` | 获取隐私设置 | ✅ |
| PUT | `/api/auth/privacy` | 更新隐私设置 | ✅ |

### 帖子模块 (`/api/posts`) - 9个接口

| 方法 | 路径 | 描述 | 需要认证 |
|------|------|------|----------|
| POST | `/api/posts/` | 创建帖子（支持文件上传） | ✅ |
| GET | `/api/posts/` | 获取动态消息 | ✅ |
| GET | `/api/posts/<post_id>` | 获取单个帖子详情 | ✅ |
| PUT | `/api/posts/<post_id>` | 更新帖子 | ✅ |
| DELETE | `/api/posts/<post_id>` | 删除帖子 | ✅ |
| GET | `/api/posts/user/<user_id>` | 获取用户帖子列表 | ✅ |
| GET | `/api/posts/user/<user_id>/liked` | 获取用户点赞过的帖子 | ✅ |
| POST | `/api/posts/<post_id>/view` | 记录帖子浏览 | ✅ |
| GET | `/api/posts/<post_id>/stats` | 获取帖子统计 | ✅ |

### 互动模块 (`/api`) - 12个接口

| 方法 | 路径 | 描述 | 需要认证 |
|------|------|------|----------|
| POST | `/api/posts/<post_id>/like` | 点赞帖子 | ✅ |
| DELETE | `/api/posts/<post_id>/like` | 取消点赞 | ✅ |
| GET | `/api/posts/<post_id>/likes` | 获取点赞列表 | ✅ |
| POST | `/api/comments/<comment_id>/like` | 点赞评论 | ✅ |
| DELETE | `/api/comments/<comment_id>/like` | 取消评论点赞 | ✅ |
| POST | `/api/posts/<post_id>/comments` | 创建评论 | ✅ |
| GET | `/api/posts/<post_id>/comments` | 获取评论列表 | ✅ |
| GET | `/api/comments/<comment_id>` | 获取评论回复列表 | ✅ |
| PUT | `/api/comments/<comment_id>` | 更新评论 | ✅ |
| DELETE | `/api/comments/<comment_id>` | 删除评论 | ✅ |

### 好友模块 (`/api/friends`) - 10个接口

| 方法 | 路径 | 描述 | 需要认证 |
|------|------|------|----------|
| POST | `/api/friends/request` | 发送好友请求 | ✅ |
| POST | `/api/friends/request/<request_id>/accept` | 接受好友请求 | ✅ |
| POST | `/api/friends/request/<request_id>/reject` | 拒绝好友请求 | ✅ |
| DELETE | `/api/friends/request/<request_id>` | 取消好友请求 | ✅ |
| GET | `/api/friends/` | 获取好友列表 | ✅ |
| GET | `/api/friends/requests/pending` | 获取待处理请求 | ✅ |
| DELETE | `/api/friends/<user_id>` | 删除好友 | ✅ |
| GET | `/api/friends/status/<user_id>` | 检查好友关系状态 | ✅ |
| GET | `/api/friends/count/<user_id>` | 获取用户好友数量 | ✅ |
| GET | `/api/friends/recommendations` | 简单好友推荐 | ✅ |

### 聊天模块 (`/api/chat`) - 7个HTTP接口 + WebSocket

#### HTTP API

| 方法 | 路径 | 描述 | 需要认证 |
|------|------|------|----------|
| GET | `/api/chat/conversations` | 获取会话列表 | ✅ |
| GET | `/api/chat/conversations/<user_id>` | 获取或创建会话 | ✅ |
| GET | `/api/chat/conversations/<conversation_id>/messages` | 获取消息历史 | ✅ |
| POST | `/api/chat/conversations/<conversation_id>/mark-read` | 标记消息已读 | ✅ |
| GET | `/api/chat/users/online` | 获取在线用户 | ✅ |
| GET | `/api/chat/users/<user_id>/status` | 获取用户在线状态 | ✅ |
| GET | `/api/chat/unread-count` | 获取未读消息数 | ✅ |

#### WebSocket事件 (ws://localhost:5000?token=xxx)

**客户端 → 服务器：**
- `send_message` - 发送消息
- `mark_as_read` - 标记消息已读
- `typing` - 正在输入状态

**服务器 → 客户端：**
- `receive_message` - 接收新消息
- `message_sent` - 消息发送确认
- `message_read` - 消息已读通知
- `user_status` - 用户在线状态变化
- `user_typing` - 对方正在输入

**支持的消息类型：**
- `text` - 文本消息
- `image` - 图片消息
- `video` - 视频消息
- `file` - 文件消息
- `post` - 帖子卡片 ✨
- `comment` - 评论卡片 ✨

### 通知模块 (`/api/notifications`) - 8个接口

| 方法 | 路径 | 描述 | 需要认证 |
|------|------|------|----------|
| GET | `/api/notifications/` | 获取通知列表 | ✅ |
| POST | `/api/notifications/<notification_id>/read` | 标记通知为已读 | ✅ |
| POST | `/api/notifications/mark-all-read` | 标记所有通知为已读 | ✅ |
| DELETE | `/api/notifications/<notification_id>` | 删除单个通知 | ✅ |
| DELETE | `/api/notifications/clear-all` | 清空所有通知 | ✅ |
| GET | `/api/notifications/unread-count` | 获取未读数量 | ✅ |
| GET | `/api/notifications/settings` | 获取通知设置 | ✅ |
| PUT | `/api/notifications/settings` | 更新通知设置 | ✅ |

### 搜索模块 (`/api/search`) - 6个接口

| 方法 | 路径 | 描述 | 需要认证 |
|------|------|------|----------|
| GET | `/api/search/users?q=关键词` | 搜索用户 | ✅ |
| GET | `/api/search/posts?q=关键词` | 搜索帖子 | ✅ |
| GET | `/api/search/global?q=关键词` | 全局综合搜索 | ✅ |
| GET | `/api/search/hashtag/<hashtag>` | 标签搜索 | ✅ |
| GET | `/api/search/trending-hashtags` | 获取热门标签 | ✅ |
| GET | `/api/search/suggest/users?prefix=xxx` | 用户自动补全建议 | ✅ |

### 话题模块 (`/api/topics`) - 12个接口

| 方法 | 路径 | 描述 | 需要认证 |
|------|------|------|----------|
| GET | `/api/topics/` | 获取所有话题 | ✅ |
| GET | `/api/topics/trending` | 获取热门话题 | ✅ |
| GET | `/api/topics/<topic_id>` | 获取单个话题详情 | ✅ |
| GET | `/api/topics/name/<topic_name>` | 根据名称获取话题 | ✅ |
| GET | `/api/topics/<topic_id>/posts` | 获取话题下的帖子 | ✅ |
| POST | `/api/topics/` | 创建话题 | ✅ |
| POST | `/api/topics/<topic_id>/follow` | 关注话题 | ✅ |
| POST | `/api/topics/<topic_id>/unfollow` | 取消关注话题 | ✅ |
| GET | `/api/topics/followed` | 获取关注的话题列表 | ✅ |
| PUT | `/api/topics/<topic_id>` | 更新话题 | ✅ |
| DELETE | `/api/topics/<topic_id>` | 删除话题 | ✅ |
| GET | `/api/topics/suggest` | 获取推荐话题 | ✅ |

### 上传模块 (`/api/upload`) - 7个接口（COS预签名URL模式）

| 方法 | 路径 | 描述 | 需要认证 |
|------|------|------|----------|
| POST | `/api/upload/presign` | 生成COS预签名上传URL | ✅ |
| POST | `/api/upload/confirm` | 确认上传完成 | ✅ |
| POST | `/api/upload/avatar/confirm` | 确认头像上传并更新资料 | ✅ |
| POST | `/api/upload/cover/confirm` | 确认封面上传并更新资料 | ✅ |
| POST | `/api/upload/delete` | 删除COS文件 | ✅ |
| GET | `/api/upload/info?url=xxx` | 获取文件信息 | ✅ |
| GET | `/api/upload/uploads/<path:filename>` | 访问文件（302重定向到COS） | ❌ |

### 推荐模块 (`/api/recommendations`) - 4个接口

| 方法 | 路径 | 描述 | 需要认证 |
|------|------|------|----------|
| GET | `/api/recommendations/feed` | 获取个性化推荐动态 | ✅ |
| GET | `/api/recommendations/trending` | 获取热门帖子 | ✅ |
| GET | `/api/recommendations/users/suggest` | 推荐可能认识的人 | ✅ |
| GET | `/api/recommendations/friends/recommend` | 智能推荐好友 ✨ | ✅ |
| GET | `/api/recommendations/posts/<post_id>/related` | 获取相关帖子推荐 | ✅ |

---

## 常见问题

### Q1: 启动时出现循环导入错误？

**A:** 确保使用延迟导入：
```python
# 错误
from socket_handlers import online_users

# 正确
def some_function():
    from socket_handlers import online_users
```

### Q2: Python 3.14兼容性？

**A:** 使用threading模式：
```python
socketio = SocketIO(app, async_mode='threading')
```

### Q3: 图片上传失败？

**A:** 检查：
- Pillow是否安装：`pip install Pillow`
- 文件大小是否超过50MB
- 文件格式是否在白名单中

### Q4: WebSocket连接失败？

**A:** 检查：
- Token是否正确
- CORS配置是否正确
- 服务器是否正常运行

### Q5: 推荐结果为空？

**A:** 可能原因：
- 用户没有好友
- 数据库中数据不足
- 检查算法参数

---

## 技术支持

### 文档资源

- **主文档**: docs/README.md
- **API规范**: api_documentation.json
- **更新日志**: docs/CHANGELOG.md
- **WebSocket事件**: docs/websocket_events.json
- **示例代码**: docs/*.js

### 测试脚本

- `tests/test_complete_v2.2.py` - 完整功能测试
- `tests/test_friend_recommendations.py` - 推荐好友测试
- `tests/quick_test.py` - 快速验证

### 核心模块

**服务层：**
- `services/recommendation_service.py` - 推荐算法
- `services/image_optimizer.py` - 图片优化
- `services/notification_service.py` - 通知服务
- `services/search_service.py` - 搜索服务

**路由层：**
- `routes_recommendations.py` - 推荐系统
- `routes_posts.py` - 帖子管理
- `routes_chat.py` - 聊天系统

---

## 扩展建议

### 短期优化
- [ ] 添加 Redis 缓存层
- [ ] 实现帖子分享功能
- [ ] 添加速率限制（Rate Limiting）
- [ ] 优化推荐算法性能
- [ ] 实现图片懒加载

### 中期扩展
- [ ] 群组聊天功能
- [ ] 视频通话集成（WebRTC）
- [ ] 故事（Stories）功能
- [ ] 机器学习推荐模型
- [ ] 内容审核系统

### 长期规划
- [ ] 微服务架构拆分
- [ ] CDN集成
- [ ] AI图片增强
- [ ] WebP/AVIF格式支持
- [ ] 移动端App
- [ ] 多语言支持

---

## 许可证

MIT License

Copyright (c) 2026 Facebook Clone Team

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

**版本**: 2.4.0  
**最后更新**: 2026-04-22  
**开发者**: Facebook Clone Team  
**GitHub**: https://github.com/your-repo/facebook-clone  
**许可证**: MIT

**总计**: 73个 API 接口 + WebSocket 实时通信
