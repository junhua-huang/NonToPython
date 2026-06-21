# Small Circle Feed Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the home recommendation feed from friend-first ranking to a fresh-content-first small-circle feed that lightly boosts familiar authors, user interests, and engagement while preventing author spam.

**Architecture:** Keep the public `/recommendations/feed` API unchanged and modify only backend recommendation ranking internals plus focused regression tests. Ranking remains SQL-based inside `RecommendationService.get_personalized_feed()` with small helper methods for interest topics and author diversity. No ML, no new tables, and no frontend changes.

**Tech Stack:** FastAPI, SQLAlchemy ORM, MySQL/MariaDB via PyMySQL, Python `unittest` source/contract tests.

---

## File Structure

- Modify: `D:/NanTuPy/app/services/recommendation_service.py`
  - Update `RecommendationService.get_personalized_feed()` scoring and candidate selection.
  - Add small helper methods on `RecommendationService` for interest topics and author diversity.
  - Keep `_batch_load_post_data()` and `_serialize_posts()` behavior compatible.
- Create: `D:/NanTuPy/tests/test_small_circle_feed_ranking.py`
  - Source-level and helper-level regression tests for the small-circle ranking contract.
- Existing verification:
  - `D:/NanTuPy/tests/test_search_guardrails.py` continues to verify `/recommendations/feed` clamps `per_page` to 20.

No commits should be created unless the human explicitly asks for commits.

---

## Task 1: Add Small-Circle Feed Contract Tests

**Files:**
- Create: `D:/NanTuPy/tests/test_small_circle_feed_ranking.py`
- Read/verify: `D:/NanTuPy/app/services/recommendation_service.py`

- [ ] **Step 1: Write failing source contract tests**

Create `D:/NanTuPy/tests/test_small_circle_feed_ranking.py` with:

```python
import unittest


class SmallCircleFeedRankingSourceTest(unittest.TestCase):
    def _source(self):
        with open('app/services/recommendation_service.py', 'r', encoding='utf-8') as f:
            return f.read()

    def _feed_source(self):
        source = self._source()
        return source.split('def get_personalized_feed')[1].split('def get_trending_posts')[0]

    def test_feed_uses_small_circle_algorithm_marker(self):
        feed_source = self._feed_source()
        self.assertIn("'algorithm': 'small_circle_fresh_v1'", feed_source)
        self.assertNotIn("'algorithm': 'personalized_v3'", feed_source)

    def test_friends_are_light_score_not_first_sort_layer(self):
        feed_source = self._feed_source()
        self.assertIn('friend_score_expr', feed_source)
        self.assertIn('feed_score_expr', feed_source)
        self.assertIn('friend_score_expr +', feed_source)
        self.assertNotIn('case((Post.user_id.in_(friend_ids), 80), else_=0).desc()', feed_source)

    def test_interest_topics_include_authored_liked_and_commented_posts(self):
        source = self._source()
        self.assertIn('def _get_interest_topic_ids', source)
        helper_source = source.split('def _get_interest_topic_ids')[1].split('def get_personalized_feed')[0]
        self.assertIn('Post.user_id == user_id', helper_source)
        self.assertIn('Like.user_id == user_id', helper_source)
        self.assertIn('Comment.user_id == user_id', helper_source)

    def test_candidate_pool_overfetches_before_author_diversity(self):
        feed_source = self._feed_source()
        self.assertIn('candidate_limit = per_page * 3 + 1', feed_source)
        self.assertIn('.limit(candidate_limit)', feed_source)
        self.assertIn('RecommendationService._apply_author_diversity(', feed_source)

    def test_engagement_score_is_lightweight_and_capped(self):
        feed_source = self._feed_source()
        self.assertIn('engagement_score_expr', feed_source)
        self.assertIn('func.least(func.coalesce(engagement_expr, 0), 8)', feed_source)


class AuthorDiversityHelperTest(unittest.TestCase):
    class PostStub:
        def __init__(self, post_id, user_id):
            self.id = post_id
            self.user_id = user_id

    def test_author_diversity_filters_spam_and_backfills_page(self):
        from app.services.recommendation_service import RecommendationService

        posts = [
            self.PostStub(1, 1),
            self.PostStub(2, 1),
            self.PostStub(3, 1),
            self.PostStub(4, 1),
            self.PostStub(5, 2),
            self.PostStub(6, 3),
        ]

        diversified, diversity_applied = RecommendationService._apply_author_diversity(
            posts,
            per_page=5,
            max_posts_per_author=2,
        )

        self.assertTrue(diversity_applied)
        self.assertEqual([p.id for p in diversified], [1, 2, 5, 6])
        self.assertLessEqual(sum(1 for p in diversified if p.user_id == 1), 2)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_small_circle_feed_ranking
```

Expected: FAIL because `small_circle_fresh_v1`, `_get_interest_topic_ids`, `_apply_author_diversity`, candidate overfetch, and capped lightweight scoring are not implemented yet.

---

## Task 2: Add Helper Methods for Interest Topics and Author Diversity

**Files:**
- Modify: `D:/NanTuPy/app/services/recommendation_service.py`
- Test: `D:/NanTuPy/tests/test_small_circle_feed_ranking.py`

- [ ] **Step 1: Add `_get_interest_topic_ids()` before `get_personalized_feed()`**

In `RecommendationService`, insert this method after `_serialize_posts()` and before `get_personalized_feed()`:

```python
    @staticmethod
    def _get_interest_topic_ids(db: Session, user_id: int) -> list[int]:
        """Return topic IDs from posts the user authored, liked, or commented on."""
        from app.models.models import post_topics

        topic_rows = (
            db.query(post_topics.c.topic_id)
            .join(Post, Post.id == post_topics.c.post_id)
            .outerjoin(Like, Like.post_id == Post.id)
            .outerjoin(Comment, Comment.post_id == Post.id)
            .filter(
                or_(
                    Post.user_id == user_id,
                    Like.user_id == user_id,
                    Comment.user_id == user_id,
                )
            )
            .distinct()
            .all()
        )
        return [row[0] for row in topic_rows]
```

- [ ] **Step 2: Add `_apply_author_diversity()` after `_get_interest_topic_ids()`**

Insert:

```python
    @staticmethod
    def _apply_author_diversity(posts: list, per_page: int, max_posts_per_author: int):
        """Limit repeated authors while preserving ranking order and filling the page when possible."""
        diversified_posts = []
        author_count = {}
        diversity_applied = False

        for post in posts:
            author_id = post.user_id
            if author_count.get(author_id, 0) >= max_posts_per_author:
                diversity_applied = True
                continue

            diversified_posts.append(post)
            author_count[author_id] = author_count.get(author_id, 0) + 1
            if len(diversified_posts) >= per_page:
                break

        return diversified_posts, diversity_applied
```

- [ ] **Step 3: Run focused tests and verify partial progress**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_small_circle_feed_ranking
```

Expected: The helper behavior test passes. Source contract tests for algorithm marker and scoring still fail until Task 3.

---

## Task 3: Replace Friend-First Sorting With Fresh-First Composite Score

**Files:**
- Modify: `D:/NanTuPy/app/services/recommendation_service.py`
- Test: `D:/NanTuPy/tests/test_small_circle_feed_ranking.py`

- [ ] **Step 1: Replace interaction author score query**

Inside `get_personalized_feed()`, replace the existing `interaction_scores` and `user_interaction_scores` block with separate like/comment author aggregates:

```python
        like_author_scores = (
            db.query(
                Post.user_id.label('author_id'),
                func.count(func.distinct(Like.id)).label('like_score'),
            )
            .join(Like, Like.post_id == Post.id)
            .filter(Like.user_id == user_id)
            .group_by(Post.user_id)
            .subquery()
        )

        comment_author_scores = (
            db.query(
                Post.user_id.label('author_id'),
                func.count(func.distinct(Comment.id)).label('comment_score'),
            )
            .join(Comment, Comment.post_id == Post.id)
            .filter(Comment.user_id == user_id)
            .group_by(Post.user_id)
            .subquery()
        )
```

- [ ] **Step 2: Replace topic extraction**

Replace the current authored-only topic block:

```python
        user_topics = db.query(post_topics.c.topic_id).join(Post, Post.id == post_topics.c.post_id).filter(
            Post.user_id == user_id
        ).distinct().all()
        topic_ids = [t[0] for t in user_topics]
```

with:

```python
        topic_ids = RecommendationService._get_interest_topic_ids(db, user_id)
```

- [ ] **Step 3: Add fresh-first score expressions**

After `engagement_expr`, add:

```python
        age_hours_expr = (
            (func.unix_timestamp(now) - func.unix_timestamp(Post.created_at)) / 3600.0 + 1
        )
        freshness_score_expr = (40.0 / func.sqrt(age_hours_expr)).label('freshness_score')
        friend_score_expr = case((Post.user_id.in_(friend_ids), 12), else_=0)
        interacted_author_score_expr = func.least(
            func.coalesce(like_author_scores.c.like_score, 0)
            + func.coalesce(comment_author_scores.c.comment_score, 0) * 2,
            10,
        )
        topic_score_expr = func.max(case((post_topics.c.topic_id.in_(topic_ids), 8), else_=0))
        engagement_score_expr = func.least(func.coalesce(engagement_expr, 0), 8)
        feed_score_expr = (
            freshness_score_expr
            + friend_score_expr
            + interacted_author_score_expr
            + topic_score_expr
            + engagement_score_expr
        ).label('feed_score')
```

- [ ] **Step 4: Update `posts_query` joins and order**

Change the query joins from `user_interaction_scores` to the two new score subqueries and order by the composite score:

```python
        posts_query = db.query(Post).outerjoin(
            Like, Post.id == Like.post_id
        ).outerjoin(
            Comment, Post.id == Comment.post_id
        ).outerjoin(
            like_author_scores, Post.user_id == like_author_scores.c.author_id
        ).outerjoin(
            comment_author_scores, Post.user_id == comment_author_scores.c.author_id
        ).outerjoin(
            post_topics, Post.id == post_topics.c.post_id
        ).filter(
            Post.is_public == True,
            Post.created_at >= time_decay_threshold
        ).group_by(Post.id).order_by(
            feed_score_expr.desc(),
            Post.created_at.desc()
        )
```

- [ ] **Step 5: Run focused tests and verify remaining failures are only candidate/diversity related**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_small_circle_feed_ranking
```

Expected: Algorithm marker/candidate overfetch may still fail until Task 4. No syntax/import errors.

---

## Task 4: Overfetch Candidates, Apply Diversity, and Update Return Metadata

**Files:**
- Modify: `D:/NanTuPy/app/services/recommendation_service.py`
- Test: `D:/NanTuPy/tests/test_small_circle_feed_ranking.py`

- [ ] **Step 1: Replace single-page fetch and inline diversity block**

Replace:

```python
        offset = (page - 1) * per_page
        # 多取 1 条判断 has_more，替代 expensive COUNT
        posts = posts_query.offset(offset).limit(per_page + 1).all()
        has_more = len(posts) > per_page
        if has_more:
            posts = posts[:per_page]

        diversified_posts = []
        author_count = {}
        max_posts_per_author = max(3, per_page // 5)
        for post in posts:
            author_id = post.user_id
            if author_count.get(author_id, 0) < max_posts_per_author:
                diversified_posts.append(post)
                author_count[author_id] = author_count.get(author_id, 0) + 1
            if len(diversified_posts) >= per_page:
                break
```

with:

```python
        candidate_limit = per_page * 3 + 1
        offset = (page - 1) * candidate_limit
        candidate_posts = posts_query.offset(offset).limit(candidate_limit).all()
        has_more = len(candidate_posts) > per_page
        max_posts_per_author = max(3, per_page // 5)
        diversified_posts, diversity_applied = RecommendationService._apply_author_diversity(
            candidate_posts[:candidate_limit - 1],
            per_page=per_page,
            max_posts_per_author=max_posts_per_author,
        )
```

- [ ] **Step 2: Update return metadata**

Replace:

```python
            'algorithm': 'personalized_v3',
            'is_new_user': is_new_user,
            'diversity_applied': len(diversified_posts) < len(posts)
```

with:

```python
            'algorithm': 'small_circle_fresh_v1',
            'is_new_user': is_new_user,
            'diversity_applied': diversity_applied
```

- [ ] **Step 3: Run focused tests and verify GREEN**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_small_circle_feed_ranking
```

Expected: All tests in `tests.test_small_circle_feed_ranking` pass.

---

## Task 5: Run Regression Verification

**Files:**
- Verify: backend tests and compile only.

- [ ] **Step 1: Run focused recommendation and guardrail tests**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_small_circle_feed_ranking tests.test_search_guardrails tests.test_performance_observability tests.test_posts_liked_performance_contract
```

Expected: All tests pass.

- [ ] **Step 2: Run existing backend regression subset**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_performance_observability tests.test_chat_performance_contracts tests.test_posts_liked_performance_contract tests.test_explore_batching_contracts tests.test_search_guardrails tests.test_ws_validation_unit tests.test_ws_manager_scheduling tests.test_notification_service_unit tests.test_cors_config tests.test_community_service_sql tests.test_small_circle_feed_ranking
```

Expected: All tests pass.

- [ ] **Step 3: Compile changed backend files**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m py_compile app/services/recommendation_service.py app/routers/recommendations.py
```

Expected: exit code 0. Existing unrelated `comic.py` warning is not expected because this compile command does not compile `comic.py`.

---

## Task 6: Summarize Behavior Change

**Files:**
- No file changes.

- [ ] **Step 1: Summarize the new feed behavior**

Report to the human:

```text
首页推荐流已改为 small_circle_fresh_v1：新内容优先，好友/互动作者/兴趣话题/热度只做轻量加分；候选池多取后再做作者多样性过滤，减少刷屏并尽量补满页面。接口路径和返回结构保持兼容。
```

- [ ] **Step 2: Include verification evidence**

Include exact test command results from Task 5.

---

## Self-Review

- Spec coverage: The plan covers fresh-first ranking, light friend boost, light engagement, authored/liked/commented topic interests, author diversity overfetch, return compatibility, and verification.
- Placeholder scan: No placeholder steps remain; every implementation step includes concrete code or a concrete command.
- Type consistency: Helper names `_get_interest_topic_ids` and `_apply_author_diversity` are consistent across tests and implementation tasks. Algorithm marker is consistently `small_circle_fresh_v1`.
- Scope: The plan stays backend-only and does not introduce ML, new tables, frontend changes, exposure logs, or cursor pagination.
