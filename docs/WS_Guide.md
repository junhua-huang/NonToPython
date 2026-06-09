# NanTuPy WebSocket 前端接入指南

> 协议版本：**v3.0.0** — 原生 WebSocket 序号协议  
> 端点：`ws://<host>:5000/ws`  
> 最后更新：2026-06-08

---

## 目录

1. [快速开始](#1-快速开始)
2. [核心概念](#2-核心概念)
3. [连接与认证](#3-连接与认证)
4. [心跳保活](#4-心跳保活)
5. [上行消息（客户端 → 服务端）](#5-上行消息客户端--服务端)
6. [下行消息（服务端 → 客户端）](#6-下行消息服务端--客户端)
7. [完整交互流程](#7-完整交互流程)
8. [错误处理](#8-错误处理)
9. [TypeScript 类型定义](#9-typescript-类型定义)
10. [注意事项](#10-注意事项)

---

## 1. 快速开始

```typescript
// ① 建立连接（不带 token）
const ws = new WebSocket('ws://localhost:5000/ws');

// ② 连接成功后发送认证消息
ws.onopen = () => {
  ws.send(JSON.stringify({ type: 'auth', token: jwtToken }));
};

// ③ 监听所有下行消息
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);

  switch (msg.type) {
    case 'auth_result':
      // 认证成功，自动收到会话列表
      break;
    case 'message':
      // 带序号的推送（新消息、通知、已读等）
      // 记录 seq 用于断线补发
      if (msg.seq > 0) lastReceivedSeq = Math.max(lastReceivedSeq, msg.seq);
      handlePayloadEvent(msg.payload);
      break;
    case 'ack':
      // 发送消息的确认回执
      break;
    case 'sync_result':
      // 断线补发的遗漏消息
      break;
    case 'pong':
      // 心跳回复
      break;
    case 'error':
      // 错误通知
      break;
  }
};

// ④ 断线重连
ws.onclose = (e) => {
  // e.code === 4001 → JWT 失效，需重新登录
  // 其他 → 自动重连
  if (e.code !== 4001) setTimeout(reconnect, 3000);
};
```

### 前端只需维护一个变量

```
lastReceivedSeq: number  // 初始值 0
```

每条 `type: "message"` 的推送都带 `seq` 字段。记录最大值，重连时发送 `sync` 即可补发遗漏消息。

---

## 2. 核心概念

### 序号 (seq)

- 每个用户有独立的序号计数器，从 1 单调递增
- 服务端每推送一条持久化消息，seq +1
- `seq = 0` 表示非持久化的实时事件（typing 等），**无需记录**
- 客户端只维护 `lastReceivedSeq`，断线重连时传给 `sync`

### 幂等去重 (clientMsgId)

- 客户端为每条发送消息生成 UUID 作为 `clientMsgId`
- 服务端用 `clientMsgId` 做幂等检查：重复请求直接返回已有 ACK（`duplicate: true`）
- 24 小时内有效，超时自动清理

### 消息信封 (Message Envelope)

所有持久化推送统一包装在 `{type: "message", seq, payload}` 中：

```json
{
  "type": "message",
  "seq": 42,
  "payload": {
    "event": "new_message",
    "conversation_id": 42,
    "data": { ... }
  }
}
```

`payload.event` 标识具体事件类型，共 7 种（见下方）。

---

## 3. 连接与认证

### 建立连接

```
new WebSocket('ws://<host>:5000/ws')
```

> **不带 token**，认证通过 `auth` 消息完成。

### 认证流程

```
C→S  {"type": "auth", "token": "eyJhbGciOi..."}
S→C  {"type": "auth_result", "success": true, "user_id": 7}
S→C  {"type": "message", "seq": 1, "payload": {"event": "session_list", "sessions": [...]}}
```

认证成功后，服务端**自动推送会话列表**。

### 关闭码

| code | 含义 | 前端处理 |
|------|------|---------|
| `4001` | JWT 无效/过期/用户不存在 | 跳转登录页 |
| `1000` | 同用户新连接替换旧连接 | 无需重连（已在其他设备登录） |

---

## 4. 心跳保活

建议每 **30 秒**发送一次 ping：

```typescript
setInterval(() => {
  if (ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'ping' }));
  }
}, 30000);
```

服务端立即回复 `{"type": "pong"}`。

---

## 5. 上行消息（客户端 → 服务端）

共 **9 种**类型。所有消息均为 JSON 格式，通过 `ws.send(JSON.stringify(msg))` 发送。

### 5.1 auth — 认证

```json
{ "type": "auth", "token": "<JWT>" }
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `type` | ✅ | 固定 `"auth"` |
| `token` | ✅ | JWT Token（HS256，payload 含 `sub` 字段） |

### 5.2 send — 发送消息（通用）

```json
{
  "type": "send",
  "clientMsgId": "550e8400-e29b-41d4-a716-446655440000",
  "payload": {
    "conversation_id": 42,
    "content": "你好，明天几点见面？",
    "message_type": "text"
  }
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `type` | ✅ | 固定 `"send"` |
| `clientMsgId` | 建议 | UUID，幂等去重 |
| `payload.event` | ❌ | 默认 `"new_message"`，见下表 |
| `payload.conversation_id` | 二选一 | 会话 ID |
| `payload.receiver_id` | 二选一 | 目标用户 ID（自动创建会话） |
| `payload.content` | ✅ | 消息内容（text 类型时） |
| `payload.message_type` | ❌ | `text` / `image` / `file` / `post` / `comment` |
| `payload.media_url` | ❌ | 媒体文件 URL |
| `payload.related_id` | ❌ | 关联帖子/评论 ID |

#### payload.event 枚举

| event | 说明 | 额外必填字段 |
|-------|------|-------------|
| `new_message`（默认） | 发送聊天消息 | `conversation_id` 或 `receiver_id` |
| `conversation_read` | 标记会话已读 | `conversation_id` |
| `notifications_read` | 标记通知已读 | `notification_ids: number[]` |

```json
// 标记会话已读
{"type":"send", "clientMsgId":"...", "payload":{"event":"conversation_read", "conversation_id":42}}

// 标记通知已读
{"type":"send", "clientMsgId":"...", "payload":{"event":"notifications_read", "notification_ids":[56,57]}}
```

### 5.3 sync — 断线补发

```json
{ "type": "sync", "lastReceivedSeq": 42 }
```

服务端返回 `seq > 42` 的所有遗漏消息。

### 5.4 ack_receive — 接收确认（可选）

```json
{ "type": "ack_receive", "seq": 42 }
```

客户端确认收到某条消息。服务端仅记录日志，可用于流量统计。**不影响消息投递**。

### 5.5 ping — 心跳

```json
{ "type": "ping" }
```

### 5.6 join — 加入会话房间

```json
{ "type": "join", "conversation_id": 42 }
```

进入聊天页面时调用。订阅该会话的 typing / stop_typing 广播。

> **注意**：`new_message` 推送不依赖 join，服务端直接按 user_id 推送。

### 5.7 leave — 离开会话房间

```json
{ "type": "leave", "conversation_id": 42 }
```

离开聊天页面时调用。断开连接时自动离开所有房间。

### 5.8 typing — 正在输入

```json
{ "type": "typing", "conversation_id": 42 }
```

广播给房间内除发送者外的所有用户。

### 5.9 stop_typing — 停止输入

```json
{ "type": "stop_typing", "conversation_id": 42 }
```

---

## 6. 下行消息（服务端 → 客户端）

共 **6 种**外层类型：

### 6.1 auth_result — 认证结果

```json
{"type": "auth_result", "success": true, "user_id": 7}
{"type": "auth_result", "success": false, "error": "Invalid token"}
```

> 非序号消息（不走 message 信封）

### 6.2 message — 统一推送信封（带序号）

```json
{"type": "message", "seq": 42, "payload": {"event": "new_message", ...}}
```

`seq = 0` 表示非持久化事件（typing），**不需要记录**。`seq > 0` 时需更新 `lastReceivedSeq`。

#### payload.event 枚举（7 种）

| event | 说明 | 关键字段 |
|-------|------|---------|
| `new_message` | 新消息推送 | `conversation_id`, `data`(消息对象), `unread_count` |
| `session_list` | 会话列表推送 | `sessions[]` |
| `typing` | 对方正在输入 | `conversation_id`, `user_id`（seq=0） |
| `stop_typing` | 对方停止输入 | `conversation_id`, `user_id`（seq=0） |
| `message_read` | 消息已读回执 | `conversation_id`, `read_by` |
| `conversation_read` | 会话已读确认 | `conversation_id`, `unread_count` |
| `new_notification` | 新通知推送 | `notification`(通知对象), `unread_count` |

#### new_message 完整示例

```json
{
  "type": "message",
  "seq": 42,
  "payload": {
    "event": "new_message",
    "conversation_id": 42,
    "data": {
      "id": 128,
      "conversation_id": 42,
      "sender_id": 7,
      "content": "你好",
      "message_type": "text",
      "media_url": null,
      "related_id": null,
      "is_read": false,
      "created_at": "2026-06-08T10:30:00"
    },
    "unread_count": 3
  }
}
```

#### session_list 完整示例

```json
{
  "type": "message",
  "seq": 1,
  "payload": {
    "event": "session_list",
    "sessions": [
      {
        "id": 4,
        "conversation_id": 4,
        "partner_id": 143,
        "partner": { "id": 143, "username": "Alice", "display_name": "Alice", "avatar_url": "..." },
        "last_message": { "id": 13, "sender_id": 143, "content": "hi", "message_type": "text", "created_at": "..." },
        "unread_count": 1,
        "created_at": "2026-06-08T09:18:09",
        "updated_at": "2026-06-08T09:18:09"
      }
    ]
  }
}
```

#### new_notification 完整示例

```json
{
  "type": "message",
  "seq": 46,
  "payload": {
    "event": "new_notification",
    "notification": {
      "id": 56,
      "user_id": 7,
      "sender_id": 12,
      "notification_type": "like",
      "title": "Alice 赞了你的帖子",
      "content": "Alice 赞了你的帖子",
      "related_id": 99,
      "related_type": "post",
      "is_read": false,
      "created_at": "2026-06-08T10:30:00"
    },
    "unread_count": 5
  }
}
```

### 6.3 ack — 发送确认

```json
{"type": "ack", "clientMsgId": "550e8400-...", "serverSeq": 43, "message_id": 128}
```

| 字段 | 说明 |
|------|------|
| `clientMsgId` | 与请求中的 UUID 对应 |
| `serverSeq` | 服务端分配的序号（当前用户的 seq） |
| `message_id` | 数据库中的消息 ID |

重复请求时：

```json
{"type": "ack", "clientMsgId": "550e8400-...", "serverSeq": 0, "duplicate": true}
```

### 6.4 sync_result — 补发结果

```json
{
  "type": "sync_result",
  "messages": [
    { "seq": 43, "payload": { "event": "new_message", "conversation_id": 42, "data": {...} } },
    { "seq": 44, "payload": { "event": "new_notification", "notification": {...} } }
  ],
  "count": 2
}
```

按 seq 升序排列，每条消息格式与 `message` 信封内的 payload 相同。

### 6.5 pong — 心跳回复

```json
{"type": "pong"}
```

### 6.6 error — 错误通知

```json
{"type": "error", "message": "Cannot send message to this user", "clientMsgId": "550e8400-..."}
```

---

## 7. 完整交互流程

### 7.1 连接 + 认证 + 会话列表

```
C→S   new WebSocket('ws://host:5000/ws')
C→S   {"type":"auth","token":"eyJhbGci..."}
S→C   {"type":"auth_result","success":true,"user_id":7}
S→C   {"type":"message","seq":1,"payload":{"event":"session_list","sessions":[...]}}
```

### 7.2 断线重连 + 补发

```
C→S   new WebSocket('ws://host:5000/ws')
C→S   {"type":"auth","token":"eyJhbGci..."}
S→C   {"type":"auth_result","success":true,"user_id":7}
C→S   {"type":"sync","lastReceivedSeq":42}
S→C   {"type":"sync_result","messages":[{"seq":43,...},{"seq":44,...}],"count":2}
```

### 7.3 发送消息完整流程

```
C→S          {"type":"join","conversation_id":42}              进入聊天页
C→S          {"type":"typing","conversation_id":42}            开始输入
S→C(others)  {"type":"message","seq":0,"payload":{"event":"typing","conversation_id":42,"user_id":7}}
C→S          {"type":"stop_typing","conversation_id":42}       停止输入
C→S          {"type":"send","clientMsgId":"uuid","payload":{"conversation_id":42,"content":"你好！"}}
S→C(sender)  {"type":"ack","clientMsgId":"uuid","serverSeq":43,"message_id":128}
S→C(receiver){"type":"message","seq":15,"payload":{"event":"new_message","conversation_id":42,"data":{...}}}
C→S          {"type":"leave","conversation_id":42}             离开聊天页
```

### 7.4 标记已读流程

**WS 通道：**

```
C→S   {"type":"send","payload":{"event":"conversation_read","conversation_id":42}}
S→C(sender)  {"type":"message","seq":44,"payload":{"event":"message_read","conversation_id":42,"read_by":7}}
```

**HTTP 通道（两种方式均可）：**

```
C→S(HTTP)  POST /api/chat/conversations/42/mark-read
S→C(via WS) {"type":"message","seq":45,"payload":{"event":"conversation_read","conversation_id":42,"unread_count":0}}
```

### 7.5 通知推送

```
S→C   {"type":"message","seq":46,"payload":{"event":"new_notification","notification":{...},"unread_count":5}}
```

他人点赞/评论/加好友/发消息时，服务端主动推送。

### 7.6 幂等去重

```
C→S   {"type":"send","clientMsgId":"uuid","payload":{"conversation_id":42,"content":"你好"}}
S→C   {"type":"ack","clientMsgId":"uuid","serverSeq":43,"message_id":128}     首次确认

// 网络重试，相同 clientMsgId
C→S   {"type":"send","clientMsgId":"uuid","payload":{"conversation_id":42,"content":"你好"}}
S→C   {"type":"ack","clientMsgId":"uuid","serverSeq":0,"duplicate":true}      不重复执行
```

### 7.7 心跳保活

```
C→S   {"type":"ping"}      每 30 秒
S→C   {"type":"pong"}      立即回复
```

---

## 8. 错误处理

### error 消息格式

```json
{"type": "error", "message": "错误描述", "clientMsgId": "uuid"}
```

### 常见错误原因

| message | 原因 | 前端处理 |
|---------|------|---------|
| `Not authenticated. Send auth first.` | 未认证就发业务消息 | 重新认证 |
| `Already authenticated` | 重复发送 auth | 忽略 |
| `receiver_id or conversation_id required` | send 缺目标参数 | 检查参数 |
| `Conversation not found` | 会话 ID 不存在 | 提示用户 |
| `Cannot send message to this user` | 对方拒绝陌生人消息 | 回滚乐观消息，显示错误 |
| `Message send failed` | 服务端异常 | 提示重试 |
| `Unknown message type: xxx` | 未知上行类型 | 检查 type 字段 |

### 发送失败处理流程

```
C→S   {"type":"send","clientMsgId":"uuid","payload":{"conversation_id":42,"content":"你好"}}
S→C   {"type":"error","clientMsgId":"uuid","message":"Cannot send message to this user"}

前端：根据 clientMsgId 找到临时消息 → 移除/标记为失败
```

---

## 9. TypeScript 类型定义

```typescript
// ── 上行消息 ──────────────────────────────────

type UpstreamMessage =
  | { type: 'auth'; token: string }
  | { type: 'send'; clientMsgId?: string; payload: SendPayload }
  | { type: 'sync'; lastReceivedSeq: number }
  | { type: 'ack_receive'; seq: number }
  | { type: 'ping' }
  | { type: 'join'; conversation_id: number }
  | { type: 'leave'; conversation_id: number }
  | { type: 'typing'; conversation_id: number }
  | { type: 'stop_typing'; conversation_id: number };

interface SendPayload {
  event?: 'new_message' | 'conversation_read' | 'notifications_read';
  conversation_id?: number;
  receiver_id?: number;
  content?: string;
  message_type?: 'text' | 'image' | 'file' | 'post' | 'comment';
  media_url?: string | null;
  related_id?: number | null;
  notification_ids?: number[];  // notifications_read 时使用
}

// ── 下行消息 ──────────────────────────────────

type DownstreamMessage =
  | { type: 'auth_result'; success: boolean; user_id?: number; error?: string }
  | { type: 'message'; seq: number; payload: PayloadEvent }
  | { type: 'ack'; clientMsgId: string; serverSeq: number; message_id?: number; duplicate?: boolean }
  | { type: 'sync_result'; messages: Array<{ seq: number; payload: PayloadEvent }>; count: number }
  | { type: 'pong' }
  | { type: 'error'; message: string; clientMsgId?: string };

// ── Payload 事件 ──────────────────────────────

type PayloadEvent =
  | { event: 'new_message'; conversation_id: number; data: MessageObject; unread_count: number }
  | { event: 'session_list'; sessions: SessionObject[] }
  | { event: 'typing'; conversation_id: number; user_id: number }
  | { event: 'stop_typing'; conversation_id: number; user_id: number }
  | { event: 'message_read'; conversation_id: number; read_by: number }
  | { event: 'conversation_read'; conversation_id: number; unread_count: number }
  | { event: 'new_notification'; notification: NotificationObject; unread_count: number };

// ── 数据对象 ──────────────────────────────────

interface MessageObject {
  id: number;
  conversation_id: number;
  sender_id: number;
  content: string;
  message_type: 'text' | 'image' | 'file' | 'post' | 'comment';
  media_url: string | null;
  related_id: number | null;
  is_read: boolean;
  created_at: string;  // ISO8601
}

interface SessionObject {
  id: number;
  conversation_id: number;
  partner_id: number;
  partner: UserObject | null;
  last_message: MessageObject | null;
  unread_count: number;
  created_at: string;
  updated_at: string;
}

interface NotificationObject {
  id: number;
  user_id: number;
  sender_id: number;
  notification_type: 'like' | 'comment' | 'friend_request' | 'friend_accept' | 'message' | 'mention';
  title: string;
  content: string;
  related_id: number | null;
  related_type: 'post' | 'friendship' | 'conversation';
  is_read: boolean;
  created_at: string;
}

interface UserObject {
  id: number;
  username: string;
  display_name: string;
  bio: string;
  avatar_url: string;
  cover_photo_url: string;
  created_at: string;
}
```

---

## 10. 注意事项

1. **连接不带 token** — 认证通过 `auth` 消息完成，不在 URL 中传递
2. **同一用户单连接** — 新连接会自动替换旧连接（旧连接收到 `code=1000`）
3. **HTTP 发消息也会触发 WS 推送** — `POST /api/chat/conversations/{id}/messages` 同样会推送带序号的 `new_message`
4. **发送方也会收到推送** — 用于多端同步（同一用户在多个设备登录时）
5. **seq=0 无需记录** — typing / stop_typing 等非持久化事件
6. **所有消息均为 JSON** — `ws.send(JSON.stringify(msg))`
7. **ack_receive 可选** — 不影响消息投递，仅用于流量统计
8. **clientMsgId 建议使用 UUID v4** — 确保全局唯一性
9. **建议封装 WS 管理类** — 统一管理连接状态、重连逻辑、seq 记录
