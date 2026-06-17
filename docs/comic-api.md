# 漫展模块 API 文档

基础路径：`/api/comic`

---

## 目录

- [1. 基础数据](#1-基础数据)
  - [城市列表](#get-cities)
  - [标签列表](#get-tags)
- [2. 漫展列表 & 详情](#2-漫展列表--详情)
  - [漫展列表](#get-events)
  - [漫展详情](#get-eventsid)
- [3. 发布 & 编辑](#3-发布--编辑)
  - [发布漫展](#post-events)
  - [编辑漫展](#put-eventsid)
- [4. 关注](#4-关注)
  - [关注 / 取消关注](#post-eventsidfollow)
- [5. 我的漫展](#5-我的漫展)
  - [我发布的](#get-my-events)
  - [我关注的](#get-my-followed)
- [6. 评论](#6-评论)
  - [评论列表](#get-eventsidcomments)
  - [发表评论](#post-eventsidcomments)
  - [删除评论](#delete-eventscommentsid)
  - [子回复列表](#get-eventscommentsidreplies)
- [7. 点赞](#7-点赞)
  - [评论点赞](#post-eventscommentsidlike)
  - [漫展点赞](#post-eventsidlike)
- [8. 搜索 — 近期漫展](#8-搜索--近期漫展)

---

## 公共约定

- **认证**：标注 `需登录` 的接口必须在 Header 中携带 `Authorization: Bearer {token}`
- **可选登录**：标注 `可选登录` 的接口，登录后返回 `isFollowed` / `isLiked` 等个人状态字段
- **状态码**：`0=即将开始`, `1=进行中`, `2=已结束`
- **响应格式**：所有字段采用 camelCase

---

## 1. 基础数据

### GET /cities

城市列表。

**请求**：无需认证

```
GET /api/comic/cities
```

**响应** (200)：

```json
[
  { "id": 1, "name": "上海", "province": "上海", "sortOrder": 1 },
  { "id": 2, "name": "北京", "province": "北京", "sortOrder": 2 }
]
```

---

### GET /tags

标签列表，按类型分组排序。

**请求**：无需认证

```
GET /api/comic/tags
```

**响应** (200)：

```json
[
  { "id": 1, "name": "cosplay", "tagType": "主题" },
  { "id": 2, "name": "同人", "tagType": "主题" },
  { "id": 3, "name": "摄影", "tagType": "活动" }
]
```

---

## 2. 漫展列表 & 详情

### GET /events

漫展列表，仅返回"即将开始"和"进行中"的漫展。

**请求**：可选登录

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `city` | string | 否 | "" | 城市名称筛选（如"上海"），为空返回全部 |
| `page` | int | 否 | 1 | 页码 |
| `size` | int | 否 | 10 | 每页条数，最大 50 |

```
GET /api/comic/events?city=上海&page=1&size=10
```

**响应** (200)：

```json
{
  "records": [
    {
      "id": 1,
      "name": "CP30",
      "startDate": "2026-07-01",
      "endDate": "2026-07-03",
      "venue": "国家会展中心",
      "status": 0,
      "statusText": "即将开始",
      "cityName": "上海",
      "cityId": 1,
      "coverImage": "/uploads/poster.jpg",
      "followCount": 128,
      "tags": ["cosplay", "同人"],
      "isOwner": false,
      "isFollowed": true,
      "creatorName": "小白·漫展君",
      "creatorAvatar": "/uploads/avatar/user1.jpg",
      "createdAt": "2026-05-20T10:30:00"
    }
  ],
  "total": 50,
  "page": 1,
  "size": 10,
  "pages": 5
}
```

---

### GET /events/{id}

漫展详情。

**请求**：可选登录

```
GET /api/comic/events/1
```

**响应** (200)：

```json
{
  "id": 1,
  "name": "CP30",
  "cityId": 1,
  "cityName": "上海",
  "venue": "国家会展中心",
  "startDate": "2026-07-01",
  "endDate": "2026-07-03",
  "startTime": "09:00",
  "endTime": "18:00",
  "ticketInfo": "50元/人",
  "website": "https://cp30.example.com",
  "intro": "CP30 同人漫展...",
  "status": 0,
  "statusText": "即将开始",
  "coverImage": "/uploads/poster.jpg",
  "followCount": 128,
  "likeCount": 42,
  "commentCount": 15,
  "isFollowed": true,
  "isLiked": false,
  "isOwner": false,
  "tags": ["cosplay", "同人", "摄影"],
  "images": [
    { "id": 1, "imageUrl": "/uploads/poster.jpg", "isCover": true, "sortOrder": 0 },
    { "id": 2, "imageUrl": "/uploads/photo1.jpg", "isCover": false, "sortOrder": 1 }
  ],
  "creatorName": "小白·漫展君",
  "creatorAvatar": "/uploads/avatar/user1.jpg",
  "createdAt": "2026-05-20T10:30:00"
}
```

**错误**：

| 状态码 | 说明 |
|--------|------|
| 404 | 漫展不存在 |

---

## 3. 发布 & 编辑

### POST /events

发布漫展。

**请求**：需登录

```
POST /api/comic/events
Content-Type: application/json
```

```json
{
  "name": "CP30",
  "cityId": 1,
  "venue": "国家会展中心",
  "startDate": "2026-07-01",
  "endDate": "2026-07-03",
  "startTime": "09:00",
  "endTime": "18:00",
  "ticketInfo": "50元/人",
  "website": "https://cp30.example.com",
  "intro": "CP30 同人漫展...",
  "tagIds": [1, 2, 3],
  "imageUrls": ["/uploads/poster.jpg", "/uploads/photo1.jpg"]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 漫展名称 |
| `cityId` | int | 是 | 城市 ID |
| `venue` | string | 否 | 场馆 |
| `startDate` | string | 否 | 开始日期 `YYYY-MM-DD` |
| `endDate` | string | 否 | 结束日期 `YYYY-MM-DD` |
| `startTime` | string | 否 | 每日开始时间 `HH:MM` |
| `endTime` | string | 否 | 每日结束时间 `HH:MM` |
| `ticketInfo` | string | 否 | 票务信息 |
| `website` | string | 否 | 官网链接 |
| `intro` | string | 否 | 介绍 |
| `tagIds` | int[] | 否 | 标签 ID 数组 |
| `imageUrls` | string[] | 否 | 图片 URL 数组，第一张自动设为封面 |

> 兼容下划线命名：`city_id`, `start_date`, `end_date` 等同样支持。

**响应** (201)：

```json
{ "id": 1, "message": "发布成功" }
```

**错误**：

| 状态码 | 说明 |
|--------|------|
| 400 | 漫展名称不能为空 / 请选择城市 |
| 401 | 未登录 |

---

### PUT /events/{id}

编辑漫展。仅创建者可编辑。

**请求**：需登录

```
PUT /api/comic/events/1
Content-Type: application/json
```

请求体格式同发布接口。

**响应** (200)：

```json
{ "message": "保存成功" }
```

**错误**：

| 状态码 | 说明 |
|--------|------|
| 403 | 无权编辑 |
| 404 | 漫展不存在 |

---

## 4. 关注

### POST /events/{id}/follow

关注或取消关注。已关注 → 取消，未关注 → 关注。

**请求**：需登录

```
POST /api/comic/events/1/follow
Content-Type: application/json

{ "userId": 5 }
```

> `userId` 可选，默认使用当前登录用户。

**响应** (200)：

```json
{ "followed": true, "followCount": 129 }
```

**错误**：

| 状态码 | 说明 |
|--------|------|
| 404 | 漫展不存在 |
| 401 | 未登录 |

---

## 5. 我的漫展

### GET /my-events

当前用户发布的漫展（分页）。

**请求**：需登录

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `page` | int | 1 | 页码 |
| `size` | int | 10 | 每页条数，最大 50 |

```
GET /api/comic/my-events?page=1&size=10
```

**响应** (200)：字段同 [漫展列表](#get-events)，`isOwner` 恒为 `true`。

---

### GET /my-followed

当前用户关注的漫展（分页）。

**请求**：需登录

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `page` | int | 1 | 页码 |
| `size` | int | 10 | 每页条数，最大 50 |

```
GET /api/comic/my-followed?page=1&size=10
```

**响应** (200)：字段同 [漫展列表](#get-events)，`isFollowed` 恒为 `true`。

---

## 6. 评论

### GET /events/{id}/comments

漫展一级评论列表，每条含前 3 条子回复。按时间倒序排列。

**请求**：可选登录

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `page` | int | 1 | 页码 |
| `size` | int | 20 | 每页条数，最大 100 |

```
GET /api/comic/events/1/comments?page=1&size=20
```

**响应** (200)：

```json
{
  "comments": [
    {
      "id": 1,
      "content": "期待！",
      "userId": 5,
      "eventId": 1,
      "parentId": null,
      "replyToUserId": null,
      "replyToUser": null,
      "author": { "id": 5, "username": "coser_x", "avatarUrl": "/uploads/avatar/5.jpg" },
      "likeCount": 3,
      "replyCount": 2,
      "createdAt": "2026-06-01T12:00:00",
      "updatedAt": "2026-06-01T12:00:00",
      "replies": [
        {
          "id": 2,
          "content": "同期待！",
          "userId": 6,
          "eventId": 1,
          "parentId": 1,
          "replyToUserId": 5,
          "replyToUser": { "id": 5, "username": "coser_x" },
          "author": { "id": 6, "username": "用户2" },
          "likeCount": 1,
          "replyCount": 0,
          "createdAt": "2026-06-01T13:00:00",
          "replies": []
        }
      ],
      "repliesHasMore": false,
      "repliesPage": 1
    }
  ],
  "total": 15,
  "page": 1,
  "size": 20,
  "pages": 1
}
```

**错误**：

| 状态码 | 说明 |
|--------|------|
| 404 | 漫展不存在 |

---

### POST /events/{id}/comments

发表评论或回复。

**请求**：需登录

```
POST /api/comic/events/1/comments
Content-Type: application/json
```

**发表一级评论**：

```json
{ "content": "期待这个漫展！" }
```

**回复某条评论**：

```json
{
  "content": "同期待！",
  "parentId": 1,
  "replyToUserId": 5
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `content` | string | 是 | 评论内容，最长 2000 字 |
| `parentId` | int | 否 | 父评论 ID，回复时使用 |
| `replyToUserId` | int | 否 | 被回复用户 ID |

> 兼容下划线：`parent_id`、`reply_to_user_id` 同样支持。

**响应** (201)：

```json
{
  "comment": {
    "id": 3,
    "content": "期待！",
    "userId": 5,
    "eventId": 1,
    "parentId": null,
    "replyToUserId": null,
    "author": { "id": 5, "username": "coser_x" },
    "likeCount": 0,
    "replyCount": 0,
    "createdAt": "2026-06-09T12:00:00",
    "replies": []
  }
}
```

**错误**：

| 状态码 | 说明 |
|--------|------|
| 400 | 评论内容不能为空且不超过 2000 字 |
| 404 | 漫展不存在 / 父评论不存在 |

---

### DELETE /events/comments/{id}

删除评论。仅评论作者可删除。

**请求**：需登录

```
DELETE /api/comic/events/comments/3
```

**响应** (200)：

```json
{ "message": "ok" }
```

**错误**：

| 状态码 | 说明 |
|--------|------|
| 403 | 无权限 |
| 404 | 评论不存在 |

---

### GET /events/comments/{id}/replies

获取某条评论的子回复列表（分页）。

**请求**：可选登录

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `page` | int | 1 | 页码 |
| `size` | int | 20 | 每页条数，最大 100 |

```
GET /api/comic/events/comments/1/replies?page=1&size=20
```

**响应** (200)：

```json
{
  "replies": [
    {
      "id": 2,
      "content": "同期待！",
      "userId": 6,
      "eventId": 1,
      "parentId": 1,
      "replyToUserId": 5,
      "replyToUser": { "id": 5, "username": "coser_x" },
      "author": { "id": 6, "username": "用户2" },
      "likeCount": 1,
      "replyCount": 0,
      "createdAt": "2026-06-01T13:00:00",
      "replies": []
    }
  ],
  "total": 2,
  "page": 1,
  "size": 20
}
```

---

## 7. 点赞

### POST /events/comments/{id}/like

点赞或取消点赞一条评论。

**请求**：需登录

```
POST /api/comic/events/comments/1/like
```

**响应** (200)：

```json
{ "isLiked": true, "likeCount": 4 }
```

| 字段 | 说明 |
|------|------|
| `isLiked` | 操作后是否已点赞 |
| `likeCount` | 操作后该评论的总点赞数 |

---

### POST /events/{id}/like

点赞或取消点赞一个漫展。

**请求**：需登录

```
POST /api/comic/events/1/like
```

**响应** (200)：

```json
{ "isLiked": true, "likeCount": 43 }
```

| 字段 | 说明 |
|------|------|
| `isLiked` | 操作后是否已点赞 |
| `likeCount` | 操作后该漫展的总点赞数 |

---

## 8. 搜索 — 近期漫展

通过全局搜索接口触发。

**请求**：

```
GET /api/search/global?q=近期漫展&per_page=20
Authorization: Bearer {token}
```

**响应** (200)：

```json
{
  "query": "近期漫展",
  "type": "comic_events",
  "events": [
    {
      "id": 1,
      "name": "CP30",
      "cityName": "上海",
      "venue": "国家会展中心",
      "startDate": "2026-07-01T00:00:00",
      "endDate": "2026-07-03T00:00:00",
      "status": 0,
      "statusText": "即将开始",
      "ticketInfo": "50元/人",
      "tags": ["cosplay", "同人", "摄影"],
      "images": [
        { "id": 1, "imageUrl": "/uploads/poster.jpg", "isCover": true, "sortOrder": 0 },
        { "id": 2, "imageUrl": "/uploads/photo1.jpg", "isCover": false, "sortOrder": 1 }
      ],
      "isFollowed": false,
      "followCount": 128,
      "creatorName": "小白·漫展君",
      "creatorAvatar": "/uploads/avatar/user1.jpg",
      "createdAt": "2026-05-20T10:30:00"
    }
  ],
  "total": 20,
  "current_page": 1,
  "per_page": 20
}
```

**规则**：筛选结束日期未过、开始日期在 3 个月内的漫展，按开始日期升序排列。

---

## 接口速查表

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/cities` | — | 城市列表 |
| GET | `/tags` | — | 标签列表 |
| GET | `/events` | 可选 | 漫展列表（分页+城市筛选） |
| GET | `/events/{id}` | 可选 | 漫展详情 |
| POST | `/events` | 需登录 | 发布漫展 |
| PUT | `/events/{id}` | 需登录 | 编辑漫展（仅创建者） |
| POST | `/events/{id}/follow` | 需登录 | 关注/取消关注 |
| GET | `/my-events` | 需登录 | 我发布的漫展 |
| GET | `/my-followed` | 需登录 | 我关注的漫展 |
| GET | `/events/{id}/comments` | 可选 | 评论列表（含前3条子回复） |
| POST | `/events/{id}/comments` | 需登录 | 发表评论/回复 |
| DELETE | `/events/comments/{id}` | 需登录 | 删除评论（仅作者） |
| GET | `/events/comments/{id}/replies` | 可选 | 子回复列表（分页） |
| POST | `/events/comments/{id}/like` | 需登录 | 评论点赞/取消 |
| POST | `/events/{id}/like` | 需登录 | 漫展点赞/取消 |
| GET | `/search/global?q=近期漫展` | 需登录 | 搜索-近期漫展 |