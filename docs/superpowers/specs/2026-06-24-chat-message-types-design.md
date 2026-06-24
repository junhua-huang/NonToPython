# 聊天消息类型完善设计

## 背景

当前聊天系统能发送和展示基础消息，但消息类型契约不统一：

- 后端模型注释声明 `text / image / system`。
- 社群聊天接口显式允许 `text / image / video`。
- 一对一 HTTP 和 WebSocket 发送接口没有统一白名单，任意 `message_type` 字符串都有机会入库。
- Flutter 模型包含 `text / image / video / file / post / comment`，但 UI 实际主要支持文本、图片，社群额外支持视频。
- 批量消息接口存在 `file_url`，其他路径主要使用 `media_url`。
- 普通用户理论上可以通过未校验入口伪造 `system` 类型。

本设计收敛消息类型，完善图片、视频、帖子卡片和系统消息 UI，不支持文件消息和评论卡片消息。

## 目标

1. 建立统一聊天消息类型契约。
2. 后端拒绝非法消息类型，禁止普通用户发送系统消息。
3. 一对一和社群消息支持范围保持一致。
4. Flutter 私聊补齐视频消息能力。
5. Flutter 支持帖子卡片消息，卡片可包含帖子图片。
6. Flutter 支持系统消息专门 UI。
7. 会话预览不再展示图片/视频 URL，而是展示清晰类型文案。
8. 保持必要兼容：读取旧 `file_url` 时映射到 `media_url`。

## 非目标

1. 不支持文件消息。
2. 不支持评论卡片消息。
3. 不实现完整“分享到聊天”的全局选择器；本次保证消息契约、后端发送能力和 UI 渲染能力。
4. 不把系统消息开放给普通用户发送。
5. 不做大规模聊天架构重写。

## 统一消息类型

正式支持的消息类型为：

| 类型 | 发送方 | 含义 |
| --- | --- | --- |
| `text` | 用户 | 文本消息 |
| `image` | 用户 | 图片消息 |
| `video` | 用户 | 视频消息 |
| `post` | 用户 | 帖子卡片消息 |
| `system` | 系统 | 系统提示消息 |

用户可发送类型：

```text
text / image / video / post
```

系统内部可创建类型：

```text
system
```

不支持类型：

```text
file / comment
```

如果收到不支持类型，后端返回 400。

## 后端契约

### 常量与校验

后端增加统一常量：

```py
USER_MESSAGE_TYPES = {"text", "image", "video", "post"}
SYSTEM_MESSAGE_TYPES = {"system"}
MESSAGE_TYPES = USER_MESSAGE_TYPES | SYSTEM_MESSAGE_TYPES
MEDIA_MESSAGE_TYPES = {"image", "video"}
CARD_MESSAGE_TYPES = {"post"}
```

普通用户发送消息时调用统一校验函数，规则如下：

| 类型 | 校验规则 |
| --- | --- |
| `text` | `content` 必须非空 |
| `image` | `media_url` 必须非空；兼容旧写法：如果 `media_url` 为空但 `content` 是 URL，则用 `content` 补齐 |
| `video` | 同 `image` |
| `post` | `related_id` 必须存在，并且对应帖子存在；`content` 和 `media_url` 可由后端从帖子补齐 |
| `system` | 普通用户接口禁止发送 |

系统内部创建系统消息时使用单独 helper 或显式参数，避免绕过规则。

### 帖子卡片消息

帖子消息存储格式：

```json
{
  "message_type": "post",
  "related_id": 123,
  "content": "帖子标题或正文摘要",
  "media_url": "帖子封面图或第一张图片，可为空"
}
```

规则：

1. `related_id` 是帖子 ID，必填。
2. 后端校验帖子存在。
3. 如果前端未传 `content`，后端使用帖子标题、内容摘要或兜底文案生成。
4. 如果前端未传 `media_url`，后端使用帖子封面图或第一张图片。
5. 如果帖子没有图片，`media_url` 为 `null`。
6. 点击行为由客户端根据 `related_id` 跳转帖子详情。

### 字段统一

消息响应主字段统一使用：

```json
"media_url"
```

批量消息接口可以继续兼容输出 `file_url`，但必须同时输出 `media_url`。Flutter 读取时也兼容 `file_url` 到 `mediaUrl`。

### 接口范围

统一校验覆盖：

1. 一对一 HTTP 发送消息。
2. WebSocket `send_message`。
3. 社群聊天发送消息。

一对一和社群均支持：

```text
text / image / video / post
```

系统消息只允许后端内部创建。

## Flutter 契约

### 消息模型

Flutter `MessageType` 调整为正式类型：

```dart
enum MessageType {
  text,
  image,
  video,
  post,
  system,
}
```

解析规则：

1. 优先读取 `message_type`。
2. 兼容读取旧路径可能出现的 `type`。
3. `mediaUrl` 优先读取 `media_url`，兼容 `file_url`。
4. 未知类型降级为 `text`。
5. 旧 `file`、`comment` 不作为正式能力；如果旧数据出现，按 `text` 兜底展示。

### 私聊媒体发送

私聊支持：

- 文本消息。
- 图片消息。
- 视频消息。

选择媒体时需要识别图片和视频：

- 图片走图片上传与 `message_type=image`。
- 视频走视频上传与 `message_type=video`。
- 不再把视频当图片发送。

### 帖子卡片 UI

`message_type=post` 渲染为帖子卡片，不作为普通文本气泡。

有图片时：

```text
┌────────────────────────┐
│ ┌──────┐  帖子          │
│ │ 图片 │  标题/摘要      │
│ └──────┘  点击查看详情   │
└────────────────────────┘
```

无图片时：

```text
┌────────────────────────┐
│ 帖子                    │
│ 标题/摘要                │
│ 点击查看详情              │
└────────────────────────┘
```

点击卡片：

- 如果 `related_id` 存在，跳转帖子详情。
- 如果 `related_id` 缺失，禁用跳转并显示普通不可点击卡片。

### 系统消息 UI

`message_type=system` 渲染为居中灰色提示，不显示头像，不作为左右气泡：

```text
—— 你加入了群聊 ——
```

适用于：

- 社群欢迎消息。
- 后续群成员加入、退出、管理动作提示。
- 其他后端生成的聊天内系统提示。

### 会话预览

会话预览统一规则：

| 类型 | 预览 |
| --- | --- |
| `text` | 文本内容 |
| `image` | `[图片]` |
| `video` | `[视频]` |
| `post` | `[帖子] 标题/摘要` |
| `system` | 系统消息内容 |

图片和视频不展示 URL。

### 本地缓存

本地消息恢复需要保留关键字段：

- `messageType`
- `mediaUrl`
- `relatedId`
- `isRecalled`
- `quoteMessageId`
- `quotePreview`
- `status`

本次至少修复会话 lastMessage 恢复时强制 `text` 的问题，避免媒体和帖子预览错误。

## 错误处理

1. 非法 `message_type`：后端返回 400，提示不支持的消息类型。
2. 普通用户发送 `system`：后端返回 403，提示系统消息不能由用户发送。
3. `text` 缺少内容：后端返回 400。
4. `image` / `video` 缺少媒体地址：后端返回 400。
5. `post` 缺少 `related_id`：后端返回 400。
6. `post` 对应帖子不存在：后端返回 404。
7. Flutter 遇到未知旧类型：降级为文本展示，不崩溃。

## 测试策略

### 后端测试

新增或更新契约测试覆盖：

1. 统一消息类型常量包含 `text/image/video/post/system`。
2. 用户发送非法类型会被拒绝。
3. 用户发送 `system` 会被拒绝。
4. 用户发送 `text` 必须有内容。
5. 用户发送 `image` / `video` 必须有媒体地址。
6. 用户发送 `post` 必须有有效 `related_id`。
7. 帖子消息可自动补齐 `content` 和 `media_url`。
8. 批量消息响应包含 `media_url` 并兼容 `file_url`。
9. 一对一 HTTP、WebSocket、社群发送路径都使用统一校验。

### Flutter 测试

新增或更新测试覆盖：

1. `Message.fromJson` 支持 `message_type`、兼容 `file_url`。
2. `MessageType` 正式类型为 `text/image/video/post/system`。
3. 会话预览对图片、视频、帖子、系统消息显示正确。
4. 私聊视频消息不会被当作图片消息发送。
5. 帖子卡片渲染图片、摘要和跳转入口。
6. 无图片帖子卡片能正常展示。
7. 系统消息居中提示展示。
8. 本地 lastMessage 恢复保留真实消息类型。

## 兼容性

1. 旧图片消息仍按 `image` 渲染。
2. 旧视频消息如果已有 `video` 类型，按视频渲染。
3. 旧 `file_url` 字段被 Flutter 映射到 `mediaUrl`。
4. 旧未知类型不会导致崩溃，会降级为文本。
5. 后端开始拒绝新写入的非法类型，但不迁移历史非法消息。

## 实施顺序建议

1. 后端先加消息类型常量、统一校验和契约测试。
2. 后端接入一对一 HTTP、WebSocket、社群发送路径。
3. 后端补齐帖子消息自动摘要和图片。
4. Flutter 更新消息模型和解析兼容。
5. Flutter 补齐私聊视频发送。
6. Flutter 增加帖子卡片和系统消息 UI。
7. Flutter 修正会话预览与本地恢复。
8. 运行后端和 Flutter 相关测试、分析器。
