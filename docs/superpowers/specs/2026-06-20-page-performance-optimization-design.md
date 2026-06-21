# Page Performance Optimization Design

Date: 2026-06-20

## Goal

Optimize the data-loading performance for four high-traffic areas:

1. Home feed
2. Explore/search
3. Conversations/messages
4. Profile pages

This is a full optimization pass, but changes should land in separable batches so each batch can be tested and reverted independently.

## Current Problems

### Home

The home feed depends mainly on `/api/recommendations/feed`, with `/api/posts/` as a fallback. The recommendation endpoint is likely expensive because it combines friendship data, joins, aggregate ranking, computed ordering, and offset pagination. The fallback posts endpoint may also be slow when visibility and friendship filters lack suitable composite indexes.

### Explore and Search

Explore loads several independent modules together. A slow optional module can delay the whole page state. Search endpoints such as `/api/search/global`, `/api/search/posts`, and `/api/search/users` are likely vulnerable to large scans when using `%query%` matching. Explore side modules such as comic events and trending topics can also produce N+1 query patterns for follow state, cover images, and counts.

### Conversations

The messages page does too much on entry: conversation loading, unread count loading, local cache probing, message preloading, and WebSocket state handling. `/api/chat/sessions` has no clear pagination and may sort a large user conversation set. Message preloading can make first paint slower and can miss cache because cache keys are inconsistent between preload and room loading.

### Profile

Profile screens have duplicate and serial loads. User posts, liked posts, friend stats, and profile data are not consistently parallelized. Some refresh paths bypass the intended cache layer while still hitting lower-level request dedupe, which can produce stale or surprising results. Backend liked-post loading may contain N+1 patterns.

## Architecture

The optimization will be split into four implementation batches.

### Batch 1: Observability

Add backend request timing middleware or equivalent logging. It will record method, path, status code, elapsed milliseconds, and safe user context when available. Slow requests above a configurable threshold should be logged at warning level.

Priority paths:

- `/api/recommendations/feed`
- `/api/posts/`
- `/api/search/global`
- `/api/search/posts`
- `/api/search/users`
- `/api/chat/sessions`
- `/api/chat/messages/batch`
- `/api/posts/user/{id}`
- `/api/posts/user/{id}/liked`
- `/api/comic/events`
- `/api/topics/trending`

This batch must not change response behavior.

### Batch 2: Frontend First-Paint Reduction

#### Conversations

The messages tab should prioritize first paint. On entry it should load the conversation list and unread counts, then defer optional message preloading until after the list is visible. Preloading should be limited to the most recent conversations. Duplicate unread count calls should be avoided. Cache keys used by message preload and room loading should be unified.

#### Profile

Profile screens should remove duplicate post loads. Independent requests such as profile info, posts, liked posts, and friendship statistics should run in parallel where safe. Refresh behavior should be made explicit: force-refresh paths should either truly bypass stale caches or update the shared cache after fetching.

#### Explore

Explore modules should load independently so one slow module does not block the whole page. Search suggestions should reduce unnecessary multi-endpoint requests. Comic and topic services should use the same request dedupe conventions as other GET services.

### Batch 3: Backend Query Optimization

#### Home Feed

Simplify or cache expensive recommendation work. The first page should avoid excessive aggregate sorting when possible. Short TTL caching may be used for feed data if it preserves correctness for privacy and visibility rules. The fallback posts query should use eager loading or batch loading for related data as needed.

#### Search

Reduce expensive broad scans. Enforce practical query limits and minimum useful query length where applicable. Keep result limits small for global search. If compatible with the deployed MySQL/MariaDB version, add full-text support for post content as a later migration-safe improvement.

#### Conversations

Add pagination to `/api/chat/sessions`, defaulting to recent sessions. Optimize sorting by last activity. Convert read marking to bulk update where possible. Ensure message and conversation queries can use composite indexes.

#### Profile

Optimize user posts and liked posts. Liked posts should load posts and authors in batch rather than row-by-row. Friendship/follow state should be queried in batches where relevant.

#### Explore Side Modules

Optimize `/api/comic/events` by batching follow state, cover images, and counts. Optimize `/api/topics/trending` by batching topic follow checks and avoiding per-topic database calls.

### Batch 4: Database Indexes

Add missing indexes only after checking the current schema and existing migrations. Candidate indexes include:

- `posts(is_public, created_at)`
- `posts(user_id, is_public, created_at)`
- `posts(visibility, created_at)`
- `friendships(sender_id, status, receiver_id)`
- `friendships(receiver_id, status, sender_id)`
- `likes(user_id, created_at)`
- `likes(post_id, user_id)`
- `conversations(user1_id, last_message_at)`
- `conversations(user2_id, last_message_at)`
- `messages(conversation_id, created_at)`
- `messages(conversation_id, is_read, sender_id)`
- `post_topics(topic_id, post_id)`
- `topic_followers(topic_id, user_id)`
- `comic_events(status, start_date)`
- `comic_events(city_id, status, start_date)`
- `comic_event_follows(event_id, user_id)`
- `comic_event_images(event_id, is_cover, sort_order)`

Indexes should be added through the project's existing migration or SQL-script pattern. If no safe migration path exists, produce an explicit SQL script and document how to apply it.

## Data Flow

### Conversation Page

1. Load conversations and unread count for first paint.
2. Render list as soon as core data is available.
3. Schedule limited recent-message preload after first paint or as a low-priority background task.
4. Use unified cache keys for preload and chat-room reads.
5. Fetch older messages on demand.

### Explore Page

1. Start independent module loads.
2. Render each module as it resolves.
3. Keep failed optional modules isolated.
4. Search suggestions use a reduced request set and dedupe repeated queries.

### Profile Page

1. Load profile header and posts as first-priority data.
2. Load liked posts and stats in parallel when possible.
3. Refresh updates the shared cache or intentionally bypasses it consistently.

### Home Feed

1. Request recommended feed.
2. Backend uses optimized query or short-lived cached result.
3. If recommendations fail or are too slow, frontend can fall back to regular posts without blocking indefinitely.

## Error Handling

- Observability logging must never break requests.
- Optional explore modules may fail independently without failing the whole tab.
- Deferred message preload failures should not show blocking UI errors.
- Cache misses should fall back to network requests.
- Backend pagination parameters should be bounded to prevent abusive page sizes.

## Testing Strategy

### Backend

Add or update tests for:

- Request timing middleware does not alter successful responses.
- Chat sessions pagination parameters and default limits.
- Bulk mark-read behavior.
- Liked posts avoid row-by-row loading where practical to assert.
- Comic events and trending topics batch follow-state behavior.
- SQL/index generation if the project uses migration scripts.

Run focused backend tests and Python compile checks for changed files.

### Frontend

Add or update tests for:

- Messages tab does not synchronously preload all conversations before first paint.
- Message preload and room loading use consistent cache keys.
- Profile screens do not duplicate initial post loads.
- Explore modules can resolve independently.
- Search suggestions reduce duplicate requests.

Run focused Flutter tests and a web debug build with production API/WS dart defines.

## Rollout and Safety

- Keep each batch independently reviewable.
- Do not change HTTP or WebSocket token transport in this optimization pass.
- Do not remove existing caches without replacing their behavior.
- Prefer bounded pagination and short TTL caches over unbounded data loads.
- If an index or query optimization depends on production database version, document the assumption before applying it.

## Success Criteria

- Conversation tab first content appears without waiting for all message preloads.
- Profile pages issue fewer duplicate requests during initial load and refresh.
- Explore page can show available modules even if one optional endpoint is slow.
- Backend logs expose slow endpoint timings for the target paths.
- Chat sessions and liked posts have bounded or batched query behavior.
- Candidate high-volume queries have matching indexes or a documented migration plan.
