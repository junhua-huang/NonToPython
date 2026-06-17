# NanTuPy WebSocket API 文档

> 连接地址: `ws://192.168.1.7:5000/ws`

---

## 目录

- [工作流程](#工作流程)
- [上行消息（客户端 → 服务端）](#上行消息客户端--服务端)
- [下行消息（服务端 → 客户端）](#下行消息服务端--客户端)
- [序号系统](#序号系统)

---

## 工作流程

```
Client                          Server
  |                                |
  |--- connect ------------------>|  accept()
  |                                |
  |--- auth {token} ------------->|  验证 JWT，查用户
  |                                |  注册连接 + 查会话列表 + 加入房间
  |                                |  通知在线好友
  |<-- auth_result ----------------|
  |<-- session_list ---------------|
  |                                |
  |--- ping --------------------->|
  |<-- pong ----------------------|
  |                                |
  |--- send {payload} ----------->|  发消息 / 标记已读
  |<-- ack -----------------------|
  |<-- message {seq, payload} ----|  (推送)
  |                                |
  |--- sync {lastReceivedSeq} --->|  断线补发
  |<-- sync_result ----------------|
  |                                |
  |--- disconnect --------------->|  ws_manager.disconnect()
```

**鉴权**: 连接后第一条消息必须是 `auth`，携带 JWT Token。未认证发其他消息会收到错误，Token 无效直接 `close(4001)`。

---

## 上行消息（客户端 → 服务端）

### 1. auth — 认证

```json
{
    "type": "auth",
    "token": "<jwt_access_token>"
}
```

### 2. send — 发送聊天消息

```json
{
    "type": "send",
    "clientMsgId": "550e8400-e29b-41d4-a716-446655440000",
    "payload": {
        "receiver_id": 128,
        "content": "你好，周末漫展去吗？",
        "message_type": "text",
        "media_url": null,
        "related_id": null
    }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|:--:|------|
| `clientMsgId` | string | 否 | UUID v4，幂等去重 |
| `payload.receiver_id` | int | 条件 | 接收者 ID（与 conversation_id 二选一） |
| `payload.conversation_id` | int | 条件 | 会话 ID |
| `payload.content` | string | 是 | 消息内容 |
| `payload.message_type` | string | 否 | `text` / `image` / `video`，默认 `text` |
| `payload.media_url` | string | 否 | 媒体文件 URL |
| `payload.related_id` | int | 否 | 关联 ID（如引用消息） |

### 3. send — 标记会话已读

```json
{
    "type": "send",
    "payload": {
        "event": "conversation_read",
        "conversation_id": 42
    }
}
```

### 4. send — 标记通知已读

```json
{
    "type": "send",
    "payload": {
        "event": "notifications_read",
        "notification_ids": [1, 2, 3]
    }
}
```

### 5. sync — 断线补发

```json
{
    "type": "sync",
    "lastReceivedSeq": 41
}
```

### 6. ping — 心跳

```json
{ "type": "ping" }
```

### 7. join / leave — 会话房间

```json
{ "type": "join", "conversation_id": 42 }
{ "type": "leave", "conversation_id": 42 }
```

### 8. typing / stop_typing — 输入状态

```json
{ "type": "typing", "conversation_id": 42 }
{ "type": "stop_typing", "conversation_id": 42 }
```

### 9. ack_receive — 确认收到

```json
{ "type": "ack_receive", "seq": 42 }
```

---

## 下行消息（服务端 → 客户端）

### 1. auth_result — 认证结果

```json
{
    "type": "auth_result",
    "success": true,
    "user_id": 147
}
```

### 2. session_list — 会话列表（auth 后自动推送）

```json
{
    "type": "session_list",
    "sessions": [
        {
            "conversation_id": 42,
            "partner_id": 128,
            "partner": {
                "id": 128,
                "username": "zhangsan",
                "bio": "...",
                "avatar_url": "https://api.dicebear.com/7.x/initials/svg?seed=zhangsan",
                "roles": ["coser", "photographer"],
                "role_labels": ["Coser", "摄影师"]
            },
            "last_message": {
                "id": 99,
                "conversation_id": 42,
                "sender_id": 128,
                "content": "周末漫展去吗",
                "message_type": "text",
                "media_url": null,
                "related_id": null,
                "is_read": false,
                "created_at": "2026-06-12T10:00:00"
            },
            "unread_count": 3,
            "created_at": "2026-06-01T08:00:00",
            "updated_at": "2026-06-12T10:00:00"
        }
    ]
}
```

### 3. pong — 心跳响应

```json
{ "type": "pong" }
```

### 4. ack — 消息发送确认

```json
{
    "type": "ack",
    "clientMsgId": "550e8400-e29b-41d4-a716-446655440000",
    "serverSeq": 43,
    "message_id": 100
}
```

重复发送时：
```json
{
    "type": "ack",
    "clientMsgId": "550e8400-e29b-41d4-a716-446655440000",
    "serverSeq": 0,
    "duplicate": true
}
```

### 5. sync_result — 补发结果

```json
{
    "type": "sync_result",
    "messages": [
        { "seq": 42, "payload": { "event": "new_message", "conversation_id": 42, "data": {...} } },
        { "seq": 43, "payload": { "event": "new_notification", "notification": {...}, "unread_count": 5 } }
    ],
    "count": 2
}
```

### 6. error — 错误

```json
{
    "type": "error",
    "message": "receiver_id or conversation_id required",
    "clientMsgId": "550e8400-e29b-41d4-a716-446655440000"
}
```

---

## 序号消息（message 通道）

所有业务推送走 `type: "message"` 通道，每条带单调递增 `seq`。`payload.event` 区分事件类型：

### 6a. new_notification — 系统通知

```json
{
    "type": "message",
    "seq": 44,
    "payload": {
        "event": "new_notification",
        "notification": {
            "id": 1,
            "user_id": 144,
            "sender_id": 128,
            "sender": {
                "username": "zhangsan",
                "avatar_url": "https://api.dicebear.com/7.x/initials/svg?seed=zhangsan"
            },
            "notification_type": "like",
            "title": "zhangsan 赞了你的帖子",
            "content": "zhangsan 赞了你的帖子",
            "related_id": 42,
            "related_type": "post",
            "is_read": false,
            "created_at": "2026-06-12T10:00:00"
        },
        "unread_count": 5
    }
}
```

`sender` 为 `null` 表示系统通知（无具体发送人）。

**notification_type 枚举：**

| 值 | 含义 | related_type |
|----|------|-------------|
| `like` | 赞了你的帖子 | `post` |
| `comment` | 评论了你的帖子 | `post` |
| `friend_request` | 好友请求 | `friendship` |
| `friend_accept` | 好友请求通过 | `friendship` |
| `message` | 新私信 | `conversation` |
| `mention` | @了你 | `post` |

### 6b. new_message — 新聊天消息

```json
{
    "type": "message",
    "seq": 45,
    "payload": {
        "event": "new_message",
        "conversation_id": 42,
        "data": {
            "id": 100,
            "conversation_id": 42,
            "sender_id": 128,
            "content": "周末漫展去吗",
            "message_type": "text",
            "media_url": null,
            "related_id": null,
            "is_read": false,
            "created_at": "2026-06-12T10:01:00"
        }
    }
}
```

### 6c. friend_online — 好友上线

```json
{
    "type": "message",
    "seq": 46,
    "payload": {
        "event": "friend_online",
        "user_id": 128
    }
}
```

### 6d. conversation_read — 对方已读

```json
{
    "type": "message",
    "seq": 47,
    "payload": {
        "event": "conversation_read",
        "conversation_id": 42,
        "read_by": 128
    }
}
```

### 6e. typing / stop_typing — 输入状态

```json
{
    "type": "message",
    "seq": 0,
    "payload": {
        "event": "typing",
        "conversation_id": 42,
        "user_id": 128
    }
}
```

输入状态 `seq: 0`，不持久化，不补发。

---

## 非序号消息

### friend_accepted_chat — 好友通过 + 创建聊天

好友请求被接受后，向双方推送此消息。不走序号通道。

发送方收到：
```json
{
    "type": "friend_accepted_chat",
    "conversation": {
        "id": 42,
        "user1_id": 128,
        "user2_id": 144,
        "last_message_at": "2026-06-12T10:02:00",
        "other_user_id": 144
    },
    "message": {
        "id": 101,
        "conversation_id": 42,
        "sender_id": 144,
        "content": "Hi",
        "message_type": "text",
        "is_read": false,
        "created_at": "2026-06-12T10:02:00"
    }
}
```

接收方收到（`other_user_id` 和 `message.sender_id` 互换）。

### notifications_read — 通知已读数

由 POST 标记已读触发，推送更新后的未读数。不走序号通道。

```json
{
    "type": "notifications_read",
    "unread_count": 3
}
```

---

## 序号系统

- 每个用户维护独立自增 `seq`，写入 `ws_message_log` 表
- 在线：实时推送 `message` 带序号
- 离线：仅写日志，重连后 `sync` 补发
- 断线重连：24h ACK 去重（`clientMsgId` 幂等），缓存命中直接返回确认
```json