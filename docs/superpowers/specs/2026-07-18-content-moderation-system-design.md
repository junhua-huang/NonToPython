# NanTuPy 内容审核机制设计

日期：2026-07-18
状态：已确认
涉及仓库：`D:\NanTuPy`、`D:\FlutterProject\nonto`

## 1. 背景

当前项目只有局部文本审核：普通帖子和普通评论在创建时调用本地敏感词与正则规则。帖子和评论编辑可以绕过审核，私聊、社群消息、漫展、公开资料及媒体内容没有统一审核。举报只能提交，缺少管理员处理、审计、申诉和处罚闭环。数据库敏感词没有接入运行时过滤器，`hidden_by_admin`、`Report.status`、`User.is_active` 等治理字段也没有完整执行链。

本设计建立统一、可审计、可扩展的内容审核域。系统保留 FastAPI 单体部署，使用数据库任务队列执行异步审核，使用腾讯云内容安全审核公开疑难文本和媒体。私聊正文只做本地审核，不发送第三方。

## 2. 目标

1. 所有用户创建和编辑内容均经过统一审核入口。
2. 明确违规内容立即拒绝；疑难公开文本和媒体先审后发；私聊保持低延迟和隐私。
3. 编辑待审时保留旧公开版本，杜绝先发布后编辑绕过。
4. 建立审核案件、任务、内容版本、违规累计、审计、举报和申诉闭环。
5. 将腾讯云能力隔离在可替换 Provider 接口后。
6. 将媒体先上传到 COS 隔离路径，通过审核后才转为公开对象。
7. 提供完整管理员 API，但本阶段不建设管理员 UI。
8. 对历史公开内容执行可暂停、可限速、幂等的增量回扫。
9. 日志和持久化遵循数据最小化原则，不泄露正文、凭据和签名 URL。

## 3. 非目标

1. 不新建独立审核微服务或引入 Redis、Kafka 等基础设施。
2. 不开发独立 Web 管理后台或 Flutter 管理界面。
3. 不把私聊正文发送给腾讯云。
4. 不主动回扫历史私聊。
5. 不支持任意通用文件上传；第一版只处理受控图片和视频。
6. 不根据风险分自动永久封号；永久停用必须由管理员 API 执行。
7. 不在客户端实现可被绕过的核心审核规则；后端始终是安全边界。

## 4. 审核范围

所有用户可提交或修改的内容必须调用统一审核服务：

- 帖子正文及编辑版本
- 普通评论、漫展评论及编辑版本
- HTTP 私聊、WebSocket 私聊和社群消息
- 社群名称、简介、规则、公告和入群申请附言
- 漫展名称、简介、场地和票务说明
- 用户昵称、展示名称和简介等公开资料
- 帖子图片和视频、头像、社群封面、漫展媒体

系统生成的通知、撤回提示和其他内部事件不审核。管理员创建的用户可见内容也不绕过审核。

## 5. 审核结果和发布策略

统一内部结果：

- `approved`：审核通过，可发布。
- `pending_review`：等待云端或人工审核，不公开。
- `rejected`：审核拒绝，不发布或隐藏。

案件状态使用更完整的状态机：

- `pending`
- `approved`
- `rejected`
- `manual_review`
- `cancelled`

### 5.1 公开文本

1. 本地规则明确安全时立即通过。
2. 本地规则明确违规则立即拒绝。
3. 本地结果可疑或无法确定时，创建案件与任务，提交腾讯云文本审核。
4. 云端结果出来前内容不对其他用户公开。

### 5.2 私聊和实时文本消息

1. 私聊与实时社群文本只进行本地同步审核，不发送腾讯云。
2. 通过后正常持久化、fanout、ACK 和推送。
3. 拒绝时不创建消息、不写 WebSocket 序列日志、不发送推送。
4. HTTP 返回稳定错误码 `CONTENT_REJECTED`。
5. WebSocket 返回包含原 `client_msg_id` 的失败 ACK，Flutter 将本地消息标记为发送失败。
6. 本地审核服务异常时失败关闭，返回 `MODERATION_UNAVAILABLE`，不发送正文。

### 5.3 媒体内容

1. 媒体先上传 COS 隔离路径。
2. 引用未审核媒体的内容进入 `pending_review`。
3. Worker 调用腾讯云图片或视频审核。
4. 所有媒体通过后才转入公开路径并发布内容。
5. 任一媒体明确违规则拒绝整个待发布版本，并隔离或删除违规对象。
6. 模糊结果进入 `manual_review`。

### 5.4 创建和编辑

- 新内容待审时只对作者显示“审核中”，不进入公共 Feed、搜索、推荐和 fanout。
- 已发布内容被编辑时，旧版本继续公开，新版本写入 `content_revisions`。
- 新版本通过后，在事务中原子替换公开字段。
- 新版本拒绝后保留旧版本。
- 同一目标出现更新版本时，较旧待审版本进入 `cancelled`。
- 异步结果必须校验目标版本和内容哈希，旧结果不能覆盖新内容。

## 6. 架构

```text
业务路由
  -> ModerationService
       |- LocalTextModerator
       |- ModerationPolicy
       |- TencentCloudModerationProvider
       |- ModerationRepository
       `- ModerationTaskWorker
```

### 6.1 `ModerationService`

审核唯一业务入口，提供以下类型的方法：

- 同步审核实时文本
- 审核公开文本并决定立即发布或创建待审版本
- 创建媒体审核案件和任务
- 应用审核结果
- 取消旧版本案件
- 根据确认违规结果创建违规事件

业务路由不能直接调用敏感词过滤器或腾讯云 SDK，也不能自行修改审核状态。

### 6.2 `LocalTextModerator`

整合现有 `content_filter.py` 和 `content_moderation.py`：

- 字符归一化
- 系统基线词库
- 数据库动态敏感词
- 垃圾、引流、仇恨和不适当内容规则
- 标准化风险分类、严重度和置信度
- 规则版本号

结果不得向普通用户暴露命中词、正则表达式或内部阈值。

### 6.3 `ModerationPolicy`

根据以下输入作出统一策略决策：

- 内容类型
- 是否公开
- 是否包含媒体
- 本地结果及置信度
- 用户当前风险等级
- 当前审核策略版本

策略输出只能是通过、拒绝、云端复核或人工复核，不允许业务路由自定义例外。

### 6.4 `TencentCloudModerationProvider`

封装腾讯云文本、图片和视频审核。Provider 负责：

- SDK/HTTP 请求构建
- 厂商标签和分数标准化
- 请求 ID 提取
- 可重试和不可重试错误分类
- 图片同步结果和视频异步任务轮询

业务层只处理内部分类：

- `sexual`
- `violence`
- `illegal`
- `abuse`
- `hate`
- `spam`
- `privacy`
- `other`

所有腾讯云凭据、地域、超时和策略阈值通过环境变量注入，禁止写入源码、日志或审核记录。

### 6.5 `ModerationRepository`

集中执行：

- 案件和任务创建
- 条件状态更新
- 内容版本应用
- 审计写入
- 违规分累计与补偿
- 举报结案

Repository 使用版本号、内容哈希、claim token 和条件更新保证并发安全和幂等。

### 6.6 `ModerationTaskWorker`

Worker 作为与 API 进程分离的命令运行，但复用同一代码库和数据库：

- 认领到期任务
- 设置租约和 claim token
- 调用 Provider
- 指数退避并加入随机抖动
- 完成、重试或转入死信/人工复核
- 崩溃后由租约超时恢复任务

宝塔部署增加单独 worker 启动命令和健康检查，不依赖进程内 `BackgroundTasks` 保证交付。

## 7. 数据模型

### 7.1 `moderation_cases`

主要字段：

- `id`
- `target_type`
- `target_id`
- `revision_id`
- `author_id`
- `source`：`create/edit/report/backfill`
- `content_hash`
- `status`
- `local_decision`
- `provider_decision`
- `risk_category`
- `severity`
- `confidence`
- `policy_version`
- `rule_version`
- `provider_request_id`
- `created_at`
- `completed_at`

同一目标、版本、内容哈希和来源建立合适的唯一幂等约束。

### 7.2 `moderation_tasks`

主要字段：

- `id`
- `case_id`
- `task_type`：`text/image/video/video_poll/backfill/cleanup`
- `provider`
- `status`：`pending/processing/succeeded/failed/dead`
- `idempotency_key`
- `attempt_count`
- `max_attempts`
- `next_attempt_at`
- `claim_token`
- `claimed_at`
- `lease_expires_at`
- `last_error_code`
- `last_error_message`
- `created_at`
- `updated_at`

错误字段只保存脱敏、限长信息。

### 7.3 `content_revisions`

主要字段：

- `id`
- `target_type`
- `target_id`
- `author_id`
- `version`
- `content_hash`
- `payload_json`
- `status`
- `created_at`
- `applied_at`
- `rejected_at`

`payload_json` 只保存目标允许修改的字段。公开 URL 只能来自已验证的上传会话，不能保存客户端任意 URL。

### 7.4 `moderation_actions`

主要字段：

- `id`
- `case_id`
- `actor_type`：`system/admin`
- `actor_id`
- `action`
- `reason_code`
- `note`
- `before_status`
- `after_status`
- `created_at`

所有系统处置、管理员操作、申诉推翻和风险分补偿均追加记录，不改写历史。

### 7.5 `user_violations` 和 `user_moderation_profiles`

`user_violations` 保存每次确认违规及其分值、关联案件、是否已补偿。`user_moderation_profiles` 保存聚合风险分、最近违规时间以及发帖、评论、聊天限制截止时间。

只有本地明确拒绝、腾讯云高置信度拒绝和管理员确认违规才累计。待审、低置信度结果和最终恢复的内容不累计。

### 7.6 `reports` 扩展

增加：

- `assignee_id`
- `moderation_case_id`
- `resolution`
- `resolution_note`
- `action_taken`
- `resolved_by`
- `resolved_at`

禁止自举报，校验目标存在，并对同一举报人和目标建立重复提交约束。扩展支持消息、社群内容、漫展和漫展评论。

### 7.7 上传会话

新增上传会话与对象记录，绑定：

- 用户
- 上传用途
- 隔离对象 key
- 声明文件类型
- 最大尺寸
- 实际 COS 元数据
- 审核状态
- 过期时间
- 关联案件

客户端不能选择任意对象 key 或公开 URL。

## 8. 状态机与并发约束

允许的案件转换：

```text
pending -> approved
pending -> rejected
pending -> manual_review
pending -> cancelled
manual_review -> approved
manual_review -> rejected
approved -> rejected
```

`approved -> rejected` 只用于历史回扫或举报复核后隐藏已发布内容。

任务完成时必须同时满足：

1. claim token 匹配；
2. 案件仍处于可完成状态；
3. 目标版本匹配；
4. 内容哈希匹配；
5. 目标未删除；
6. 没有更新版本取代当前版本。

不满足时记录任务结果，但不得应用内容或处罚用户。

## 9. COS 隔离上传

### 9.1 对象路径

```text
quarantine/<user_id>/<upload_session_id>/<object>
public/<business_type>/<date>/<object>
```

预签名只能写入当前用户的隔离前缀。

### 9.2 上传确认

确认时后端查询 COS 元数据并验证：

- 对象存在
- 对象 key 属于当前上传会话和用户
- 实际大小不超过用途限制
- Content-Type 在白名单内
- 扩展名、MIME 和真实格式一致
- 会话未过期且未被使用

图片增加真实解码、像素总量和格式检查；视频检查容器及基础媒体元数据。未知类型一律拒绝。

### 9.3 发布和清理

审核通过后，服务端复制或移动到公开前缀，再把公开 URL 应用到业务内容。拒绝、过期和孤儿对象由幂等清理任务删除。腾讯云临时访问 URL 按任务即时生成，不持久化。

## 10. 管理员和用户 API

本阶段只实现 API，不开发管理 UI。

### 10.1 管理员 API

- `GET /api/admin/moderation/cases`
- `GET /api/admin/moderation/cases/{id}`
- `POST /api/admin/moderation/cases/{id}/approve`
- `POST /api/admin/moderation/cases/{id}/reject`
- `POST /api/admin/moderation/content/{type}/{id}/hide`
- `POST /api/admin/moderation/content/{type}/{id}/restore`
- `GET /api/admin/reports`
- `GET /api/admin/reports/{id}`
- `POST /api/admin/reports/{id}/resolve`
- `POST /api/admin/users/{id}/restrict`
- `POST /api/admin/users/{id}/deactivate`
- `POST /api/admin/users/{id}/reactivate`
- `GET /api/admin/moderation/audit`
- 敏感词和审核策略管理接口

权限规则：

- `admin` 可处理普通案件和举报。
- `super_admin` 才能永久停用/恢复账号和修改审核策略。
- 管理员不能处理自己创建或被举报的内容；超级管理员例外操作必须填写原因。
- 所有状态改变必须提供原因码，重要操作要求备注，并写审计记录。

列表支持状态、目标类型、风险等级、时间、作者和举报人筛选。

### 10.2 用户 API

- 查询自己的待审与已拒绝案件
- 查询待审版本状态
- 对可申诉案件提交一次申诉
- 查询当前发布限制及到期时间

普通响应不包含命中词、正则、厂商阈值或完整 Provider 响应。

## 11. 动态敏感词

系统内置词作为不可删除的基线，数据库保存可管理词。动态词字段包括：

- 词或正则
- 匹配类型
- 分类
- 严重度
- 是否启用
- 词表版本
- 创建人与更新时间

启动时加载启用词。进程周期性检查版本，发现变更后构建新快照并原子替换。增删改词必须增加全局版本号。

正则写入时进行语法和复杂度检查；不接受已知容易灾难性回溯的表达式。审核案件保存规则版本，以便追踪误判。

## 12. 用户风险和限制

默认分值：

- 轻微：1 分
- 中等：3 分
- 严重：8 分

默认阈值：

- 5 分：公开内容进入更严格审核
- 10 分：暂停发帖和评论 24 小时
- 15 分：暂停所有用户内容发布 72 小时
- 20 分：进入管理员重点复核名单，不自动永久封号

30 天无新违规后风险分逐步衰减。阈值存入审核策略，只允许 `super_admin` 调整。

所有发布入口在后端统一检查限制。永久停用后，登录和现有 Token 鉴权都拒绝；恢复账号同样通过审计 API 完成。

管理员推翻误判时追加补偿记录、撤销对应风险分并恢复内容，不删除原始违规或处置记录。

## 13. 举报、人工复核与申诉

1. 举报提交时校验目标存在、可见性和举报权限。
2. 禁止举报自己；同一举报人不能重复举报同一目标。
3. 举报进入管理员队列，可关联已有案件或创建 `report` 来源案件。
4. 管理员可批准、驳回、隐藏、恢复、限制用户或转交。
5. 处理举报必须同步更新 `Report`、案件和审计记录。
6. 被拒绝公开内容保留期内允许作者申诉一次。
7. 申诉进入 `manual_review`。
8. 申诉批准后恢复内容或应用编辑版本，并撤销风险分。
9. 申诉驳回后案件终结。
10. 未发送的私聊不保存完整正文，因此不提供逐条申诉。

## 14. 故障策略

- 网络超时、限流和腾讯云 5xx：指数退避重试并加入随机抖动。
- 明确 Provider 拒绝：案件 `rejected`。
- 模糊、低置信度或未知标签：`manual_review`。
- 超过最大重试次数：`manual_review`，不得因故障自动公开。
- Worker 崩溃：租约超时后重新认领。
- 数据库提交失败：不得提前向客户端报告审核成功。
- 本地实时审核异常：失败关闭并返回可重试错误。
- 上传元数据检查异常：不确认上传，不创建公开 URL。
- 已删除或已被新版本取代的内容：旧审核结果不得重新发布。

健康检查分别报告 API、数据库、审核任务积压和 Provider 配置/可用性，且不输出凭据。

## 15. 数据保留与隐私

默认保留期：

- 被拒绝公开内容版本和隔离媒体：30 天
- 案件哈希、分类、状态和请求 ID：180 天
- 管理员操作审计：365 天
- 通过内容：按正常业务数据生命周期保存

私聊审核日志不保存正文，只保存用户、内容类型、规则版本、结果和时间。审核日志不得保存：

- JWT
- 云端密钥
- 完整用户正文
- 完整 Provider 响应
- 长效或带签名 COS URL
- 无需持久化的标题、消息体和媒体内容

管理员访问举报内容需要角色权限，并记录访问审计。

## 16. 历史内容回扫

上线后低优先级增量回扫：

1. 扫描公开帖子、评论、公开资料、社群资料/公告、漫展和公开媒体。
2. 不主动扫描历史私聊。
3. 按目标类型和主键游标分批读取。
4. 本地明确安全内容不调用腾讯云。
5. 可疑公开文本和公开媒体提交云审核。
6. 高置信度违规内容自动隐藏并创建案件。
7. 模糊结果进入人工复核。
8. 保存游标，支持暂停、恢复和限速。
9. 目标类型、ID、版本和哈希形成幂等键。
10. 先提供 dry-run，只统计不隐藏；确认误判率后切换执行模式。

回扫导致的处罚只对高置信度或管理员确认违规生效，重复扫描不得重复计分。

## 17. API 和 Flutter 兼容

创建、编辑和详情响应统一增加：

```json
{
  "moderation_status": "approved | pending_review | rejected",
  "case_id": 123,
  "content": {}
}
```

对已有接口尽量保持向后兼容。Flutter 必须支持：

- 新内容“审核中”状态
- 编辑版本“审核中”，旧内容仍公开
- 审核拒绝提示
- 媒体审核进度轮询或刷新
- HTTP `CONTENT_REJECTED` 和 `MODERATION_UNAVAILABLE`
- WebSocket 含 `client_msg_id` 的审核失败 ACK
- 当前账号发布限制提示

审核原因只展示标准化用户文案，不展示命中规则。

## 18. 可观测性

记录非敏感指标：

- 按内容类型统计审核数量和耗时
- `approved/rejected/pending/manual_review` 比例
- 本地规则和腾讯云命中率
- 任务积压、重试、死信和租约恢复数量
- 隔离对象数量和清理失败数
- 管理员平均处理时长
- 申诉数量和推翻率
- 历史回扫游标和处理速率

日志使用案件 ID、任务 ID、目标类型、内部错误码和请求 ID关联，不记录完整正文。

## 19. 测试策略

### 19.1 单元与契约测试

- 本地规则、归一化、动态词库和规则版本
- Policy 对每种内容类型的决策
- Provider 标签标准化和错误分类
- 风险分累计、衰减和补偿
- 状态机非法转换拒绝
- 数据最小化和日志脱敏

腾讯云 SDK 全部通过 mock/fixture 做契约测试，测试数据只使用虚构占位内容。

### 19.2 后端集成测试

- 每种内容创建和编辑路径均不能绕过审核
- 旧版本保留与新版本原子应用
- 并发编辑、旧回调、删除竞态
- HTTP 与 WebSocket 拒绝语义
- 拒绝消息不持久化、不 fanout、不推送、不写序列日志
- 任务认领、租约、重试、死信和幂等
- 上传越权、伪 MIME、超限文件、过期会话和任意 URL
- 举报、管理员权限、审计和申诉
- 违规分补偿及账号停用鉴权
- 历史回扫重复执行
- MySQL 和 Alembic 迁移验证

### 19.3 Flutter 测试

- 审核中、拒绝、旧版本保留的界面状态
- HTTP 审核错误映射
- WebSocket 审核失败 ACK 与本地消息状态
- 媒体待审状态恢复
- 账号限制提示
- 现有帖子、评论、聊天、社群、屏蔽、隐私、推送回归

## 20. 分阶段交付

### 阶段 1：统一文本审核基础

- 统一服务、Policy 和本地审核器
- 动态敏感词
- 覆盖创建、编辑、私聊、社群、漫展和公开资料
- 修复帖子/评论编辑绕过
- 修复 `is_active` 登录和 Token 鉴权
- 建立统一错误码和 Flutter 兼容

### 阶段 2：审核状态和治理闭环

- 案件、内容版本、任务、违规和审计表
- 举报扩展、管理员 API、用户案件与申诉 API
- 自动隐藏、恢复和临时发布限制
- Worker 基础框架与健康检查

### 阶段 3：COS 隔离与腾讯云媒体审核

- 上传会话、对象所有权和真实元数据校验
- 图片、视频和疑难公开文本 Provider
- 任务重试、视频轮询、清理和待审发布
- Flutter 媒体审核中状态

### 阶段 4：历史回扫与运营能力

- dry-run、游标回扫、限速和恢复
- 指标、积压告警和运维文档
- 保留期清理和风险分衰减任务
- 全量回归、迁移和部署验证

## 21. 验收标准

1. 任一用户内容创建或编辑路径都不能绕过统一审核服务。
2. 帖子和评论编辑违规内容不会替换公开版本。
3. 私聊正文不会发送腾讯云，拒绝消息不会产生业务副作用。
4. 未通过媒体审核的对象不会获得可持久公开 URL。
5. 重复任务、重复回调和 Worker 崩溃不会重复发布或重复处罚。
6. 举报可完整受理、处理、审计和结案。
7. 管理员可通过 API隐藏、恢复、限制和停用账号，权限边界可验证。
8. 动态敏感词修改能在所有后端进程生效并可追踪规则版本。
9. 审核故障不会导致待审内容自动公开。
10. 历史回扫可 dry-run、暂停、恢复并重复执行。
11. 日志、数据库审核记录和 API 响应不泄露正文、JWT、云密钥和签名 URL。
12. 后端自包含测试、Flutter 全量测试和静态分析通过，Alembic 只有一个 head。
