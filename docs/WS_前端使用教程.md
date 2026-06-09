# NanTuPy WebSocket 前端接入教程

> 端点：`ws://<host>:5000/ws`
> 协议版本：v3.0.0 — 序号协议

---

## 1. 快速上手

```js
const ws = new WebSocket('ws://192.168.1.5:5000/ws');
let lastReceivedSeq = 0; // 唯一需要维护的变量，用于断线补发

ws.onopen = () => {
  // ① 连接成功后立即认证
  ws.send(JSON.stringify({ type: 'auth', token: '你的JWT' }));
};

ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);

  switch (msg.type) {
    case 'auth_result':
      if (msg.success) {
        console.log('认证成功, user_id:', msg.user_id);
        // 服务端会自动推送 session_list（会话列表）
      }
      break;

    case 'message':
      if (msg.seq > 0) lastReceivedSeq = Math.max(lastReceivedSeq, msg.seq);
      handlePayload(msg.payload);
      break;

    case 'ack':
      // 发送消息的确认回执
      onMessageAck(msg.clientMsgId, msg.message_id);
      break;

    case 'sync_result':
      // 断线补发的遗漏消息
      msg.messages.forEach(m => handlePayload(m.payload));
      break;

    case 'pong':
      // 心跳回复，无需处理
      break;

    case 'error':
      console.error('WS 错误:', msg.message);
      break;
  }
};

ws.onclose = (e) => {
  // 4001 = Token 过期，跳转登录页
  if (e.code !== 4001) setTimeout(reconnect, 3000);
};
```

---

## 2. 心跳保活

```js
setInterval(() => {
  if (ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'ping' }));
  }
}, 30000); // 每30秒
```

---

## 3. 发送聊天消息

```js
function sendMessage(convId, text, msgType = 'text') {
  const clientMsgId = crypto.randomUUID();
  ws.send(JSON.stringify({
    type: 'send',
    clientMsgId: clientMsgId,
    payload: {
      conversation_id: convId,
      content: text,
      message_type: msgType, // text | image | file
    }
  }));
  return clientMsgId;
}

// 示例
const msgId = sendMessage(2, '你好！');
```

> - `clientMsgId` 用于幂等去重，网络重试不会产生重复消息
> - 发送后收到 `ack` 才算成功：`{ type: "ack", clientMsgId, serverSeq, message_id }`

---

## 4. 消息推送处理

```js
function handlePayload(payload) {
  switch (payload.event) {
    // ── 聊天消息 ──
    case 'new_message':
      // payload.conversation_id, payload.data(消息对象), payload.unread_count
      appendMessage(payload.conversation_id, payload.data);
      updateUnread(payload.conversation_id, payload.unread_count);
      break;

    // ── 会话列表（认证后自动推送） ──
    case 'session_list':
      // payload.sessions[]
      renderSessionList(payload.sessions);
      break;

    // ── 通知（点赞/评论/加好友/发消息） ──
    case 'new_notification':
      // payload.notification, payload.unread_count
      showToast(payload.notification.title);
      updateNotificationBadge(payload.unread_count);
      break;

    // ── 对方正在输入（seq=0，不需记录） ──
    case 'typing':
      showTypingIndicator(payload.conversation_id, payload.user_id);
      break;

    case 'stop_typing':
      hideTypingIndicator(payload.conversation_id, payload.user_id);
      break;

    // ── 已读回执 ──
    case 'message_read':
      markMessageRead(payload.conversation_id, payload.read_by);
      break;

    case 'conversation_read':
      clearUnread(payload.conversation_id);
      break;
  }
}
```

---

## 5. 标记已读

```js
// 标记会话已读
function markConversationRead(convId) {
  ws.send(JSON.stringify({
    type: 'send',
    clientMsgId: crypto.randomUUID(),
    payload: { event: 'conversation_read', conversation_id: convId }
  }));
}

// 标记通知已读
function markNotificationsRead(notificationIds) {
  ws.send(JSON.stringify({
    type: 'send',
    clientMsgId: crypto.randomUUID(),
    payload: { event: 'notifications_read', notification_ids: notificationIds }
  }));
}
```

---

## 6. 输入状态（typing）

```js
// 进入聊天页
function enterChat(convId) {
  ws.send(JSON.stringify({ type: 'join', conversation_id: convId }));
}

// 用户开始输入
function onTyping(convId) {
  ws.send(JSON.stringify({ type: 'typing', conversation_id: convId }));
}

// 用户停止输入
function onStopTyping(convId) {
  ws.send(JSON.stringify({ type: 'stop_typing', conversation_id: convId }));
}

// 离开聊天页
function leaveChat(convId) {
  ws.send(JSON.stringify({ type: 'leave', conversation_id: convId }));
}
```

---

## 7. 断线重连

```js
function reconnect() {
  const newWs = new WebSocket('ws://192.168.1.5:5000/ws');

  newWs.onopen = () => {
    // 重新认证
    newWs.send(JSON.stringify({ type: 'auth', token: getToken() }));

    // 补发断线期间遗漏的消息
    newWs.send(JSON.stringify({ type: 'sync', lastReceivedSeq: lastReceivedSeq }));
  };

  // ... 其他 onmessage、onclose 逻辑同上
}
```

---

## 8. 上行消息速查

| type | 用途 | 示例 |
|------|------|------|
| `auth` | 认证 | `{ type: "auth", token: "..." }` |
| `send` | 发送消息/标记已读 | `{ type: "send", clientMsgId: "...", payload: {...} }` |
| `sync` | 断线补发 | `{ type: "sync", lastReceivedSeq: 42 }` |
| `ping` | 心跳 | `{ type: "ping" }` |
| `join` | 进入聊天页 | `{ type: "join", conversation_id: 2 }` |
| `leave` | 离开聊天页 | `{ type: "leave", conversation_id: 2 }` |
| `typing` | 正在输入 | `{ type: "typing", conversation_id: 2 }` |
| `stop_typing` | 停止输入 | `{ type: "stop_typing", conversation_id: 2 }` |

---

## 9. 下行消息速查

| type | 说明 |
|------|------|
| `auth_result` | 认证结果 `{ success, user_id }` |
| `message` | 统一推送 `{ seq, payload: { event, ... } }` |
| `ack` | 发送确认 `{ clientMsgId, serverSeq, message_id }` |
| `sync_result` | 补发结果 `{ messages[], count }` |
| `pong` | 心跳回复 |
| `error` | 错误 `{ message, clientMsgId }` |

---

## 10. 关闭码

| code | 含义 | 处理 |
|------|------|------|
| `4001` | JWT 无效/过期 | 跳转登录页 |
| `1000` | 多设备登录被踢 | 不重连 |

---

## 11. 完整示例（Vue 3 composable）

```js
// useWebSocket.js
import { ref, onUnmounted } from 'vue';

export function useWebSocket(token) {
  const ws = ref(null);
  const lastSeq = ref(0);
  const isConnected = ref(false);

  function connect() {
    ws.value = new WebSocket('ws://192.168.1.5:5000/ws');

    ws.value.onopen = () => {
      ws.value.send(JSON.stringify({ type: 'auth', token: token }));
    };

    ws.value.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'auth_result' && msg.success) {
        isConnected.value = true;
      } else if (msg.type === 'message' && msg.seq > 0) {
        lastSeq.value = Math.max(lastSeq.value, msg.seq);
        // 通过 EventBus 或 store 分发 payload
        window.dispatchEvent(new CustomEvent('ws-payload', { detail: msg.payload }));
      } else if (msg.type === 'ack') {
        window.dispatchEvent(new CustomEvent('ws-ack', { detail: msg }));
      } else if (msg.type === 'sync_result') {
        msg.messages.forEach(m =>
          window.dispatchEvent(new CustomEvent('ws-payload', { detail: m.payload }))
        );
      }
    };

    ws.value.onclose = (e) => {
      isConnected.value = false;
      if (e.code !== 4001) setTimeout(connect, 3000);
    };
  }

  function send(payload) {
    const clientMsgId = crypto.randomUUID();
    ws.value.send(JSON.stringify({ type: 'send', clientMsgId, payload }));
    return clientMsgId;
  }

  onUnmounted(() => ws.value?.close());
  connect();

  return { ws, lastSeq, isConnected, send };
}
```

**使用：**

```js
const { isConnected, send } = useWebSocket(jwtToken);

// 监听推送
window.addEventListener('ws-payload', (e) => {
  const { event, ...data } = e.detail;
  if (event === 'new_message') {
    chatStore.addMessage(data.conversation_id, data.data);
  } else if (event === 'new_notification') {
    notiStore.addNotification(data.notification);
  }
});

// 发送消息
send({ conversation_id: 2, content: '你好', message_type: 'text' });
```