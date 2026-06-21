# 首页推荐流 Cursor 分页与 Seen 去重性能设计

日期：2026-06-20

## 背景

首页推荐流已经调整为 `small_circle_fresh_v1`：新内容优先，好友、互动作者、兴趣话题和热度只做轻量加分，并通过作者多样性避免单个作者刷屏。

当前剩余风险是：如果客户端继续使用很大的 `page`，服务端为了保证作者多样性和不跳页，需要从前缀候选中逐步取数。在 100 万帖子规模或恶意大页码请求下，深 `OFFSET` 和多轮查询会导致响应时间不可控。

本设计目标是让首页推荐流在小圈子内容场景下保持低延迟，并避免用户连续刷到重复帖子。

## 目标

1. 首页推荐接口不能因为极端大页码拖慢数据库。
2. 用户连续刷首页时，7 天内已经曝光过的帖子不重复展示。
3. 小圈子内容不多时，不做永久去重，避免首页过快刷空。
4. 首页优先展示最近新内容，不和探索页的热门内容定位冲突。
5. 旧前端的 `page/per_page` 参数保持兼容，新前端可逐步切到 cursor。
6. 查询路径必须有明确索引支撑，避免 100 万帖子规模下全表扫描或深 offset。

## 非目标

1. 不引入机器学习、向量召回或复杂推荐系统。
2. 不改变探索页热门/搜索/分类职责。
3. 不要求“永不重复展示”，只保证短期去重。
4. 不在本阶段实现复杂曝光衰减模型。

## 推荐行为

### 7 天短期 seen 去重

首页推荐返回帖子后，后端记录曝光：

```text
user_id
post_id
seen_at
source = home_feed
```

后续首页推荐排除：

```text
source = home_feed
AND user_id = 当前用户
AND seen_at >= now - 7 days
```

超过 7 天的 seen 记录不再强排除。它们可以作为兜底内容低优先级回流。

### 时间窗口分层

推荐候选按优先级分三层：

1. 最近 30 天 + 7 天内未曝光。
2. 最近 90 天 + 7 天内未曝光。
3. 最近 90 天 + 曝光超过 7 天的帖子，低优先级回流。

接口优先填满第一层。第一层不足时再扩展到第二层。第二层仍不足时，允许第三层兜底。

### 内容耗尽状态

如果可推荐内容不足或为空，接口返回明确状态：

```json
{
  "posts": [],
  "has_more": false,
  "feed_status": "exhausted_recent",
  "message": "最近的新内容已经看完了"
}
```

前端可以引导用户去探索页，而不是无限重复旧内容。

## 分页设计

### 旧 page 兼容保护

服务层必须限制：

```python
page = max(1, min(page, 50))
per_page = max(1, min(per_page, 20))
```

这保证旧前端或恶意请求不能触发极端深分页。

### Cursor 优先

新增可选参数：

```text
cursor
per_page
```

当请求带 `cursor` 时，使用 cursor 流；否则使用旧 page 兼容流。

cursor 中需要包含：

```text
last_score
last_created_at
last_post_id
window_stage
issued_at
```

排序条件保持稳定：

```text
feed_score DESC
created_at DESC
id DESC
```

下一页使用 seek 条件，而不是深 `OFFSET`：

```text
feed_score < last_score
OR feed_score = last_score AND created_at < last_created_at
OR feed_score = last_score AND created_at = last_created_at AND id < last_post_id
```

实际实现时 cursor 应做 base64/json 编码，并带版本字段，方便后续升级。

## 性能约束

### 必须避免的行为

1. 不允许首页推荐接口对用户传入的大 page 做无上限 prefix 扫描。
2. 不允许 cursor 流使用深 `OFFSET`。
3. 不允许 seen 过滤通过 Python 全量列表排除。
4. 不允许推荐查询因为 seen 表缺索引而扫用户所有历史曝光。

### 候选窗口限制

推荐查询必须限制最近窗口：

```text
30 天窗口优先
90 天窗口兜底
```

不在首页推荐主查询中扫描全历史公开帖子。

### 查询批量限制

每次请求最多返回：

```text
per_page <= 20
```

候选 overfetch 可以用于作者多样性，但应有上限，例如：

```text
candidate_limit <= per_page * 10
```

cursor 模式不应该为了补齐一页无限循环查询。若当前窗口不足，则进入下一窗口或返回耗尽状态。

## 数据库结构

新增表建议命名：`post_feed_seen`。

字段：

```text
id BIGINT PRIMARY KEY AUTO_INCREMENT
user_id BIGINT NOT NULL
post_id BIGINT NOT NULL
source VARCHAR(32) NOT NULL DEFAULT 'home_feed'
seen_at DATETIME NOT NULL
created_at DATETIME NOT NULL
updated_at DATETIME NOT NULL
```

唯一约束：

```text
UNIQUE KEY uq_post_feed_seen_user_post_source (user_id, post_id, source)
```

推荐索引：

```text
INDEX idx_post_feed_seen_user_source_seen_post (user_id, source, seen_at, post_id)
INDEX idx_post_feed_seen_post_source (post_id, source)
```

用途：

- `idx_post_feed_seen_user_source_seen_post` 支撑按用户、来源、7 天窗口查 seen。
- `uq_post_feed_seen_user_post_source` 支撑重复曝光时 upsert 更新时间。
- `idx_post_feed_seen_post_source` 便于后续按帖子分析曝光。

## 数据流

### 请求流程

1. 解析 `cursor/page/per_page`。
2. 限制 `per_page <= 20`，旧 page 限制 `page <= 50`。
3. 构造 30 天未 seen 候选查询。
4. 使用综合分排序和作者多样性选择一页。
5. 如果不足，扩展到 90 天未 seen。
6. 如果仍不足，允许 90 天内但 seen 超过 7 天的内容低优先级回流。
7. 返回帖子、`has_more`、`next_cursor`、`feed_status`。
8. 对本次返回的帖子做 seen upsert。

### 返回字段兼容

保留现有字段：

```text
posts
has_more
current_page
per_page
is_new_user
diversity_applied
algorithm
```

新增字段：

```text
next_cursor
feed_status
```

`algorithm` 可升级为：

```text
small_circle_fresh_cursor_seen_v1
```

## 错误处理

1. cursor 无效、过期或解析失败：忽略 cursor，按第一页刷新处理。
2. seen 记录写入失败：不影响主响应，但记录 warning；不能让用户等待或失败。
3. 当前窗口无内容：进入下一窗口或返回 `exhausted_recent`。
4. 数据库异常：保持现有接口错误处理，不暴露内部 SQL 信息。

## 测试要求

### 单元/源码级保护测试

1. page 被限制到最大 50。
2. per_page 被限制到最大 20。
3. cursor 模式不使用深 `OFFSET`。
4. seen 查询必须使用 7 天窗口过滤。
5. 推荐候选必须限制 30/90 天时间窗口。
6. 返回结构包含 `next_cursor` 和 `feed_status`。

### 行为测试

1. 用户刚看过的帖子 7 天内不再返回。
2. 8 天前看过的帖子可以作为低优先级兜底回流。
3. 30 天窗口不足时扩展到 90 天。
4. 90 天窗口也不足时返回 `exhausted_recent`。
5. 作者多样性在 cursor 模式下仍生效。

### 性能回归测试

1. 模拟超大 `page` 请求，确认服务层 clamp 到 50。
2. 确认 cursor 查询不包含无上限 offset。
3. 确认 seen 表索引声明存在。
4. 可选：在测试库或低峰期对推荐 SQL 执行 `EXPLAIN`，确认 posts 时间窗口索引和 seen 复合索引被使用。

## 推进顺序

### 阶段一：快速保护

1. 服务层限制 `page <= 50`。
2. 保持 `per_page <= 20`。
3. 加测试防止退化。

### 阶段二：Seen 去重

1. 新增 `post_feed_seen` model/table。
2. 新增 seen upsert 逻辑。
3. 推荐查询排除 7 天内 seen。
4. 添加 30/90 天窗口和 exhausted 状态。

### 阶段三：Cursor 分页

1. 新增 cursor encode/decode。
2. 推荐接口支持 cursor 参数和 `next_cursor`。
3. cursor 模式使用 seek 分页。
4. 旧 page 流继续兼容，但不推荐深翻页。

## 验收标准

1. 首页推荐不接受无上限深 page。
2. 用户连续刷新或下拉时，7 天内不会重复看到同一帖子。
3. 内容不足时返回明确耗尽状态，而不是长时间等待。
4. cursor 模式不依赖深 offset。
5. 推荐查询限定 30/90 天窗口。
6. seen 写入失败不会拖慢或中断主响应。
7. 后端相关测试通过。
