# Home Feed Cursor Seen Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not commit unless the user explicitly asks; this workspace is being edited in-place.

**Goal:** Make the homepage recommendation feed fast and repeat-safe by adding hard pagination guards, 7-day seen-post de-duplication, recent-window candidate limits, and cursor pagination.

**Architecture:** Keep the current `RecommendationService` as the orchestration point, but split feed concerns into small helpers: cursor encode/decode, seen recording, scored query construction, and cursor/page selection. Add a `PostFeedSeen` SQLAlchemy model plus Alembic migration with indexes that support fast per-user 7-day seen filtering. The API remains backward compatible with `page/per_page` and adds optional `cursor` plus `next_cursor/feed_status` response fields.

**Tech Stack:** FastAPI, SQLAlchemy ORM, Alembic, MySQL/MariaDB, Python `unittest`.

---

## File map

- Modify: `D:\NanTuPy\app\models\models.py`
  - Add `PostFeedSeen` model with unique constraint and indexes.
- Create: `D:\NanTuPy\alembic\versions\2026_06_20_1200-7f3c2a9b8d10_add_post_feed_seen.py`
  - Add `post_feed_seen` table and indexes.
- Modify: `D:\NanTuPy\app\routers\recommendations.py`
  - Accept optional `cursor` query parameter and pass it to the service.
  - Clamp `page` at the route layer as a first line of defense.
- Modify: `D:\NanTuPy\app\services\recommendation_service.py`
  - Add constants for max page, per-page, seen TTL, candidate windows, and cursor version.
  - Add cursor helpers.
  - Add seen query/record helpers.
  - Rework `get_personalized_feed()` to support cursor first, old page second.
- Modify: `D:\NanTuPy\tests\test_small_circle_feed_ranking.py`
  - Update existing source guards for max page, cursor fields, 30/90-day windows, seen filtering, and no deep offset in cursor mode.
- Create: `D:\NanTuPy\tests\test_home_feed_cursor_seen.py`
  - Add helper-level tests for cursor round trip, invalid cursor handling, page/per-page clamps, and seen-record helper behavior using stubs/mocks where possible.

---

## Task 1: Add DB model and migration for seen-post tracking

**Files:**
- Modify: `D:\NanTuPy\app\models\models.py`
- Create: `D:\NanTuPy\alembic\versions\2026_06_20_1200-7f3c2a9b8d10_add_post_feed_seen.py`
- Test: `D:\NanTuPy\tests\test_home_feed_cursor_seen.py`

- [ ] **Step 1: Write failing source tests for the seen model and migration**

Create `D:\NanTuPy\tests\test_home_feed_cursor_seen.py` with:

```python
import unittest


class HomeFeedSeenModelSourceTest(unittest.TestCase):
    def _models_source(self):
        with open('app/models/models.py', 'r', encoding='utf-8') as f:
            return f.read()

    def _migration_source(self):
        with open('alembic/versions/2026_06_20_1200-7f3c2a9b8d10_add_post_feed_seen.py', 'r', encoding='utf-8') as f:
            return f.read()

    def test_post_feed_seen_model_declares_required_indexes(self):
        source = self._models_source()
        self.assertIn('class PostFeedSeen(Base):', source)
        self.assertIn("__tablename__ = 'post_feed_seen'", source)
        self.assertIn("UniqueConstraint('user_id', 'post_id', 'source', name='uq_post_feed_seen_user_post_source')", source)
        self.assertIn("Index('idx_post_feed_seen_user_source_seen_post', 'user_id', 'source', 'seen_at', 'post_id')", source)
        self.assertIn("Index('idx_post_feed_seen_post_source', 'post_id', 'source')", source)

    def test_post_feed_seen_migration_creates_table_and_indexes(self):
        source = self._migration_source()
        self.assertIn("revision: str = '7f3c2a9b8d10'", source)
        self.assertIn("down_revision: Union[str, Sequence[str], None] = '9e5c87b1dde7'", source)
        self.assertIn("op.create_table('post_feed_seen'", source)
        self.assertIn("op.create_index('idx_post_feed_seen_user_source_seen_post'", source)
        self.assertIn("op.create_index('idx_post_feed_seen_post_source'", source)
        self.assertIn("op.drop_table('post_feed_seen')", source)
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_home_feed_cursor_seen
```

Expected: FAIL because `PostFeedSeen` and the migration file do not exist yet.

- [ ] **Step 3: Add `PostFeedSeen` model**

In `D:\NanTuPy\app\models\models.py`, update the import block if needed; it already imports `Index` and `UniqueConstraint`. Add this model after `Post` and before `Comment`:

```python
class PostFeedSeen(Base):
    """首页推荐流曝光记录，用于短期去重。"""
    __tablename__ = 'post_feed_seen'
    __table_args__ = (
        UniqueConstraint('user_id', 'post_id', 'source', name='uq_post_feed_seen_user_post_source'),
        Index('idx_post_feed_seen_user_source_seen_post', 'user_id', 'source', 'seen_at', 'post_id'),
        Index('idx_post_feed_seen_post_source', 'post_id', 'source'),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    post_id = Column(Integer, ForeignKey('posts.id', ondelete='CASCADE'), nullable=False)
    source = Column(String(32), nullable=False, default='home_feed')
    seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

- [ ] **Step 4: Add the Alembic migration**

Create `D:\NanTuPy\alembic\versions\2026_06_20_1200-7f3c2a9b8d10_add_post_feed_seen.py`:

```python
"""add_post_feed_seen

Revision ID: 7f3c2a9b8d10
Revises: 9e5c87b1dde7
Create Date: 2026-06-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7f3c2a9b8d10'
down_revision: Union[str, Sequence[str], None] = '9e5c87b1dde7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'post_feed_seen',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.Column('seen_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['post_id'], ['posts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'post_id', 'source', name='uq_post_feed_seen_user_post_source'),
    )
    op.create_index(
        'idx_post_feed_seen_user_source_seen_post',
        'post_feed_seen',
        ['user_id', 'source', 'seen_at', 'post_id'],
        unique=False,
    )
    op.create_index(
        'idx_post_feed_seen_post_source',
        'post_feed_seen',
        ['post_id', 'source'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_post_feed_seen_post_source', table_name='post_feed_seen')
    op.drop_index('idx_post_feed_seen_user_source_seen_post', table_name='post_feed_seen')
    op.drop_table('post_feed_seen')
```

- [ ] **Step 5: Run tests and compile affected files**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_home_feed_cursor_seen
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m py_compile app/models/models.py alembic/versions/2026_06_20_1200-7f3c2a9b8d10_add_post_feed_seen.py
```

Expected: tests PASS, py_compile exits 0.

- [ ] **Step 6: Review diff, do not commit**

Run:

```bash
cd /d/NanTuPy && git diff -- app/models/models.py alembic/versions/2026_06_20_1200-7f3c2a9b8d10_add_post_feed_seen.py tests/test_home_feed_cursor_seen.py
```

Expected: only the model, migration, and new tests changed. Do not commit unless the user explicitly asks.

---

## Task 2: Add cursor helpers and pagination clamps

**Files:**
- Modify: `D:\NanTuPy\app\services\recommendation_service.py`
- Modify: `D:\NanTuPy\app\routers\recommendations.py`
- Test: `D:\NanTuPy\tests\test_home_feed_cursor_seen.py`
- Test: `D:\NanTuPy\tests\test_small_circle_feed_ranking.py`

- [ ] **Step 1: Add failing tests for cursor helpers and max page clamp**

Append to `D:\NanTuPy\tests\test_home_feed_cursor_seen.py`:

```python
from datetime import datetime, timezone


class HomeFeedCursorHelperTest(unittest.TestCase):
    def test_cursor_round_trip_preserves_stable_sort_fields(self):
        from app.services.recommendation_service import RecommendationService

        cursor = RecommendationService._encode_feed_cursor({
            'version': 1,
            'last_score': 12.5,
            'last_created_at': '2026-06-20T12:00:00',
            'last_post_id': 123,
            'window_stage': '30d',
            'issued_at': '2026-06-20T12:00:00',
        })
        decoded = RecommendationService._decode_feed_cursor(cursor)

        self.assertEqual(decoded['version'], 1)
        self.assertEqual(decoded['last_score'], 12.5)
        self.assertEqual(decoded['last_created_at'], '2026-06-20T12:00:00')
        self.assertEqual(decoded['last_post_id'], 123)
        self.assertEqual(decoded['window_stage'], '30d')

    def test_invalid_cursor_decodes_to_none(self):
        from app.services.recommendation_service import RecommendationService

        self.assertIsNone(RecommendationService._decode_feed_cursor('not-a-valid-cursor'))
        self.assertIsNone(RecommendationService._decode_feed_cursor(''))
        self.assertIsNone(RecommendationService._decode_feed_cursor(None))
```

Append to `SmallCircleFeedRankingSourceTest` in `D:\NanTuPy\tests\test_small_circle_feed_ranking.py`:

```python
    def test_feed_clamps_page_and_exposes_cursor_fields(self):
        feed_source = self._feed_source()
        self.assertIn('MAX_HOME_FEED_PAGE = 50', self._source())
        self.assertIn('page = max(1, min(page, RecommendationService.MAX_HOME_FEED_PAGE))', feed_source)
        self.assertIn('cursor: str | None = None', feed_source)
        self.assertIn("'next_cursor':", feed_source)
        self.assertIn("'feed_status':", feed_source)
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_home_feed_cursor_seen tests.test_small_circle_feed_ranking
```

Expected: FAIL because cursor helpers, route parameter, and `MAX_HOME_FEED_PAGE` do not exist yet.

- [ ] **Step 3: Add imports, constants, and cursor helpers**

At the top of `D:\NanTuPy\app\services\recommendation_service.py`, change imports to:

```python
import base64
import json
import logging
from app.models.models import Post, User, Like, Comment, Friendship, PostFeedSeen
from sqlalchemy import func, case, or_, and_, exists
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from datetime import datetime, timedelta


logger = logging.getLogger(__name__)
```

Inside `class RecommendationService:`, immediately after the docstring, add:

```python
    HOME_FEED_SEEN_SOURCE = 'home_feed'
    HOME_FEED_CURSOR_VERSION = 1
    HOME_FEED_SEEN_TTL_DAYS = 7
    HOME_FEED_PRIMARY_WINDOW_DAYS = 30
    HOME_FEED_FALLBACK_WINDOW_DAYS = 90
    MAX_HOME_FEED_PAGE = 50
    MAX_HOME_FEED_PER_PAGE = 20
```

Add these helpers before `_batch_load_post_data`:

```python
    @staticmethod
    def _encode_feed_cursor(cursor_data: dict) -> str:
        """Encode cursor data as URL-safe base64 JSON."""
        payload = json.dumps(cursor_data, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
        return base64.urlsafe_b64encode(payload.encode('utf-8')).decode('ascii')

    @staticmethod
    def _decode_feed_cursor(cursor: str | None) -> dict | None:
        """Decode a feed cursor; invalid cursors are ignored as a fresh first page."""
        if not cursor:
            return None
        try:
            raw = base64.urlsafe_b64decode(cursor.encode('ascii')).decode('utf-8')
            data = json.loads(raw)
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

        if data.get('version') != RecommendationService.HOME_FEED_CURSOR_VERSION:
            return None
        required_fields = {'last_score', 'last_created_at', 'last_post_id', 'window_stage', 'issued_at'}
        if not required_fields.issubset(data.keys()):
            return None
        return data
```

- [ ] **Step 4: Clamp route `page` and pass cursor**

In `D:\NanTuPy\app\routers\recommendations.py`, change the feed route to:

```python
@router.get("/feed")
def get_personalized_feed(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取个性化推荐动态"""
    page = min(page, 50)
    per_page = min(per_page, 20)
    result = RecommendationService.get_personalized_feed(
        db,
        user_id=user.id,
        page=page,
        per_page=per_page,
        cursor=cursor,
    )
    return result
```

- [ ] **Step 5: Update service signature and return compatibility fields**

Change the service signature and clamp lines in `get_personalized_feed()`:

```python
    @staticmethod
    def get_personalized_feed(db: Session, user_id: int, page: int = 1, per_page: int = 20, cursor: str | None = None):
        from app.models.models import post_topics

        page = max(1, min(page, RecommendationService.MAX_HOME_FEED_PAGE))
        per_page = max(1, min(per_page, RecommendationService.MAX_HOME_FEED_PER_PAGE))
        decoded_cursor = RecommendationService._decode_feed_cursor(cursor)
```

For this task only, keep the existing page behavior but add placeholder response fields with safe values:

```python
        return {
            'posts': RecommendationService._serialize_posts(diversified_posts, batch_data),
            'has_more': has_more,
            'current_page': page,
            'per_page': per_page,
            'algorithm': 'small_circle_fresh_v1',
            'is_new_user': is_new_user,
            'diversity_applied': diversity_applied,
            'next_cursor': None,
            'feed_status': 'active' if diversified_posts else 'exhausted_recent',
        }
```

- [ ] **Step 6: Run tests and compile**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_home_feed_cursor_seen tests.test_small_circle_feed_ranking
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m py_compile app/services/recommendation_service.py app/routers/recommendations.py
```

Expected: tests PASS, py_compile exits 0.

---

## Task 3: Add seen filtering and non-blocking seen recording

**Files:**
- Modify: `D:\NanTuPy\app\services\recommendation_service.py`
- Test: `D:\NanTuPy\tests\test_small_circle_feed_ranking.py`
- Test: `D:\NanTuPy\tests\test_home_feed_cursor_seen.py`

- [ ] **Step 1: Add failing source tests for 7-day seen filtering and non-blocking writes**

Append to `SmallCircleFeedRankingSourceTest`:

```python
    def test_feed_filters_recent_seen_posts_and_records_seen_non_blocking(self):
        source = self._source()
        feed_source = self._feed_source()
        self.assertIn('HOME_FEED_SEEN_TTL_DAYS = 7', source)
        self.assertIn('def _recent_seen_exists_expr', source)
        self.assertIn('PostFeedSeen.seen_at >= recent_seen_threshold', source)
        self.assertIn('~RecommendationService._recent_seen_exists_expr(', feed_source)
        self.assertIn('def _record_feed_seen', source)
        self.assertIn('except SQLAlchemyError', source)
        self.assertIn('logger.warning', source)
        self.assertIn('RecommendationService._record_feed_seen(', feed_source)
```

Append to `D:\NanTuPy\tests\test_home_feed_cursor_seen.py`:

```python
class HomeFeedSeenRecordHelperTest(unittest.TestCase):
    class PostStub:
        def __init__(self, post_id):
            self.id = post_id

    def test_extract_post_ids_for_seen_recording(self):
        from app.services.recommendation_service import RecommendationService

        posts = [self.PostStub(1), self.PostStub(2), self.PostStub(2), self.PostStub(None)]
        self.assertEqual(RecommendationService._extract_post_ids(posts), [1, 2])
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_home_feed_cursor_seen tests.test_small_circle_feed_ranking
```

Expected: FAIL because seen helpers are not implemented.

- [ ] **Step 3: Add seen helper methods**

Add these methods before `_get_interest_topic_ids` in `RecommendationService`:

```python
    @staticmethod
    def _recent_seen_exists_expr(user_id: int, recent_seen_threshold: datetime):
        """SQL EXISTS expression for posts seen in the 7-day home-feed de-dupe window."""
        return exists().where(
            and_(
                PostFeedSeen.user_id == user_id,
                PostFeedSeen.source == RecommendationService.HOME_FEED_SEEN_SOURCE,
                PostFeedSeen.post_id == Post.id,
                PostFeedSeen.seen_at >= recent_seen_threshold,
            )
        )

    @staticmethod
    def _extract_post_ids(posts: list) -> list[int]:
        """Return unique non-null post IDs in display order."""
        post_ids = []
        seen_ids = set()
        for post in posts:
            post_id = getattr(post, 'id', None)
            if post_id is None or post_id in seen_ids:
                continue
            post_ids.append(post_id)
            seen_ids.add(post_id)
        return post_ids

    @staticmethod
    def _record_feed_seen(db: Session, user_id: int, posts: list, now: datetime | None = None) -> None:
        """Record home-feed exposure without failing the feed response if the write fails."""
        post_ids = RecommendationService._extract_post_ids(posts)
        if not post_ids:
            return

        now = now or datetime.utcnow()
        try:
            existing_rows = (
                db.query(PostFeedSeen)
                .filter(
                    PostFeedSeen.user_id == user_id,
                    PostFeedSeen.source == RecommendationService.HOME_FEED_SEEN_SOURCE,
                    PostFeedSeen.post_id.in_(post_ids),
                )
                .all()
            )
            existing_by_post_id = {row.post_id: row for row in existing_rows}

            for post_id in post_ids:
                existing = existing_by_post_id.get(post_id)
                if existing:
                    existing.seen_at = now
                    existing.updated_at = now
                else:
                    db.add(PostFeedSeen(
                        user_id=user_id,
                        post_id=post_id,
                        source=RecommendationService.HOME_FEED_SEEN_SOURCE,
                        seen_at=now,
                        created_at=now,
                        updated_at=now,
                    ))
            db.flush()
        except SQLAlchemyError as exc:
            db.rollback()
            logger.warning('Failed to record home feed seen posts for user %s: %s', user_id, exc)
```

- [ ] **Step 4: Apply recent seen exclusion in the feed query**

In `get_personalized_feed()`, after `now = datetime.utcnow()`, add:

```python
        recent_seen_threshold = now - timedelta(days=RecommendationService.HOME_FEED_SEEN_TTL_DAYS)
```

In `score_subquery.filter(...)`, add:

```python
                ~RecommendationService._recent_seen_exists_expr(user_id, recent_seen_threshold),
```

- [ ] **Step 5: Record seen after serialization data is loaded**

After `batch_data = RecommendationService._batch_load_post_data(db, diversified_posts, user_id)`, add:

```python
        serialized_posts = RecommendationService._serialize_posts(diversified_posts, batch_data)
        RecommendationService._record_feed_seen(db, user_id, diversified_posts, now=now)
```

Then change the return field:

```python
            'posts': serialized_posts,
```

- [ ] **Step 6: Run focused tests and compile**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_home_feed_cursor_seen tests.test_small_circle_feed_ranking
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m py_compile app/services/recommendation_service.py
```

Expected: tests PASS, py_compile exits 0.

---

## Task 4: Add 30/90-day candidate windows and cursor seek mode

**Files:**
- Modify: `D:\NanTuPy\app\services\recommendation_service.py`
- Test: `D:\NanTuPy\tests\test_small_circle_feed_ranking.py`
- Test: `D:\NanTuPy\tests\test_home_feed_cursor_seen.py`

- [ ] **Step 1: Add failing tests for recent windows and no deep offset in cursor mode**

Append to `SmallCircleFeedRankingSourceTest`:

```python
    def test_feed_limits_candidates_to_30_and_90_day_windows(self):
        source = self._source()
        feed_source = self._feed_source()
        self.assertIn('HOME_FEED_PRIMARY_WINDOW_DAYS = 30', source)
        self.assertIn('HOME_FEED_FALLBACK_WINDOW_DAYS = 90', source)
        self.assertIn("window_stage = '30d'", feed_source)
        self.assertIn("window_stage = '90d'", feed_source)
        self.assertIn("feed_status = 'exhausted_recent'", feed_source)

    def test_cursor_mode_uses_seek_not_offset(self):
        source = self._source()
        self.assertIn('def _apply_cursor_seek', source)
        cursor_helper_source = source.split('def _apply_cursor_seek')[1].split('def get_personalized_feed')[0]
        self.assertIn('score_column < last_score', cursor_helper_source)
        self.assertIn('Post.created_at < last_created_at', cursor_helper_source)
        self.assertIn('Post.id < last_post_id', cursor_helper_source)
        self.assertNotIn('.offset(', cursor_helper_source)
```

Append to `HomeFeedCursorHelperTest`:

```python
    def test_cursor_seek_values_are_parsed(self):
        from app.services.recommendation_service import RecommendationService

        cursor = RecommendationService._encode_feed_cursor({
            'version': 1,
            'last_score': 9.25,
            'last_created_at': '2026-06-20T12:00:00',
            'last_post_id': 42,
            'window_stage': '90d',
            'issued_at': '2026-06-20T12:00:00',
        })
        decoded = RecommendationService._decode_feed_cursor(cursor)
        self.assertEqual(decoded['window_stage'], '90d')
        self.assertEqual(decoded['last_post_id'], 42)
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_home_feed_cursor_seen tests.test_small_circle_feed_ranking
```

Expected: FAIL because `_apply_cursor_seek` and window stages are missing.

- [ ] **Step 3: Add cursor seek helper**

Add this helper before `get_personalized_feed`:

```python
    @staticmethod
    def _parse_cursor_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _apply_cursor_seek(query, score_column, cursor_data: dict | None):
        """Apply stable seek pagination for feed_score DESC, created_at DESC, id DESC."""
        if not cursor_data:
            return query

        last_created_at = RecommendationService._parse_cursor_datetime(cursor_data.get('last_created_at'))
        if last_created_at is None:
            return query

        last_score = float(cursor_data.get('last_score'))
        last_post_id = int(cursor_data.get('last_post_id'))

        return query.filter(or_(
            score_column < last_score,
            and_(
                score_column == last_score,
                Post.created_at < last_created_at,
            ),
            and_(
                score_column == last_score,
                Post.created_at == last_created_at,
                Post.id < last_post_id,
            ),
        ))
```

- [ ] **Step 4: Refactor scoring query into a helper**

Extract the score/query construction from `get_personalized_feed()` into a helper before `get_personalized_feed()`:

```python
    @staticmethod
    def _build_home_feed_query(
        db: Session,
        user_id: int,
        friend_ids: list[int],
        topic_ids: list[int],
        window_start: datetime,
        recent_seen_threshold: datetime,
        ranking_now: datetime,
        cursor_data: dict | None = None,
    ):
        from app.models.models import post_topics

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

        engagement_expr = (
            func.count(func.distinct(Like.id)) + func.count(func.distinct(Comment.id)) * 2
        ).label('engagement')
        age_hours_expr = (
            (func.unix_timestamp(ranking_now) - func.unix_timestamp(Post.created_at)) / 3600.0 + 1
        )
        freshness_score_expr = (40.0 / func.sqrt(age_hours_expr)).label('freshness_score')
        friend_score_expr = case((Post.user_id.in_(friend_ids), 12), else_=0)
        like_author_score_expr = func.max(func.coalesce(like_author_scores.c.like_score, 0))
        comment_author_score_expr = func.max(func.coalesce(comment_author_scores.c.comment_score, 0))
        interacted_author_score_expr = func.least(
            like_author_score_expr + comment_author_score_expr * 2,
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

        score_subquery = (
            db.query(
                Post.id.label('post_id'),
                feed_score_expr,
            )
            .outerjoin(Like, Post.id == Like.post_id)
            .outerjoin(Comment, Post.id == Comment.post_id)
            .outerjoin(like_author_scores, Post.user_id == like_author_scores.c.author_id)
            .outerjoin(comment_author_scores, Post.user_id == comment_author_scores.c.author_id)
            .outerjoin(post_topics, Post.id == post_topics.c.post_id)
            .filter(
                Post.is_public == True,
                Post.created_at >= window_start,
                ~RecommendationService._recent_seen_exists_expr(user_id, recent_seen_threshold),
            )
            .group_by(Post.id, Post.user_id, Post.created_at)
            .subquery()
        )

        query = (
            db.query(Post, score_subquery.c.feed_score)
            .join(score_subquery, Post.id == score_subquery.c.post_id)
        )
        query = RecommendationService._apply_cursor_seek(query, score_subquery.c.feed_score, cursor_data)
        return query.order_by(score_subquery.c.feed_score.desc(), Post.created_at.desc(), Post.id.desc())
```

- [ ] **Step 5: Add selection helper that returns posts and next cursor**

Add this helper before `get_personalized_feed()`:

```python
    @staticmethod
    def _select_cursor_feed_page(query, per_page: int, max_posts_per_author: int, window_stage: str, ranking_now: datetime):
        rows = query.limit(per_page * 10).all()
        posts = [row[0] for row in rows]
        score_by_post_id = {row[0].id: float(row[1] or 0) for row in rows}
        diversified_posts, diversity_applied = RecommendationService._apply_author_diversity(
            posts,
            max_posts_per_author=max_posts_per_author,
        )
        page_posts = diversified_posts[:per_page]
        has_more = len(diversified_posts) > per_page or len(rows) == per_page * 10

        next_cursor = None
        if page_posts and has_more:
            last_post = page_posts[-1]
            next_cursor = RecommendationService._encode_feed_cursor({
                'version': RecommendationService.HOME_FEED_CURSOR_VERSION,
                'last_score': score_by_post_id.get(last_post.id, 0.0),
                'last_created_at': last_post.created_at.isoformat(),
                'last_post_id': last_post.id,
                'window_stage': window_stage,
                'issued_at': ranking_now.isoformat(),
            })

        return page_posts, has_more, diversity_applied, next_cursor
```

- [ ] **Step 6: Rework `get_personalized_feed()` to use 30/90 windows and cursor flow**

Inside `get_personalized_feed()`, keep friendship/topic setup. Replace the existing score/query/paging block from `now = datetime.utcnow()` through `diversified_posts...` with:

```python
        now = datetime.utcnow()
        decoded_cursor = RecommendationService._decode_feed_cursor(cursor)
        cursor_issued_at = RecommendationService._parse_cursor_datetime(decoded_cursor.get('issued_at')) if decoded_cursor else None
        ranking_now = cursor_issued_at or now
        recent_seen_threshold = now - timedelta(days=RecommendationService.HOME_FEED_SEEN_TTL_DAYS)
        max_posts_per_author = max(3, per_page // 5)

        start_stage = decoded_cursor.get('window_stage') if decoded_cursor else '30d'
        stages = ['30d', '90d'] if start_stage == '30d' else ['90d']

        diversified_posts = []
        has_more = False
        diversity_applied = False
        next_cursor = None
        feed_status = 'active'

        for window_stage in stages:
            window_days = (
                RecommendationService.HOME_FEED_PRIMARY_WINDOW_DAYS
                if window_stage == '30d'
                else RecommendationService.HOME_FEED_FALLBACK_WINDOW_DAYS
            )
            window_start = ranking_now - timedelta(days=window_days)
            stage_cursor = decoded_cursor if decoded_cursor and decoded_cursor.get('window_stage') == window_stage else None
            posts_query = RecommendationService._build_home_feed_query(
                db=db,
                user_id=user_id,
                friend_ids=friend_ids,
                topic_ids=topic_ids,
                window_start=window_start,
                recent_seen_threshold=recent_seen_threshold,
                ranking_now=ranking_now,
                cursor_data=stage_cursor,
            )
            diversified_posts, has_more, diversity_applied, next_cursor = RecommendationService._select_cursor_feed_page(
                posts_query,
                per_page=per_page,
                max_posts_per_author=max_posts_per_author,
                window_stage=window_stage,
                ranking_now=ranking_now,
            )
            if diversified_posts:
                break

        if not diversified_posts:
            feed_status = 'exhausted_recent'
```

This initially makes both fresh requests and cursor requests use cursor-style selection. The old `page` parameter remains clamped and returned for compatibility, but no longer drives deep selection.

- [ ] **Step 7: Preserve return shape and update algorithm marker**

Return:

```python
        batch_data = RecommendationService._batch_load_post_data(db, diversified_posts, user_id)
        serialized_posts = RecommendationService._serialize_posts(diversified_posts, batch_data)
        RecommendationService._record_feed_seen(db, user_id, diversified_posts, now=now)

        return {
            'posts': serialized_posts,
            'has_more': has_more,
            'current_page': page,
            'per_page': per_page,
            'algorithm': 'small_circle_fresh_cursor_seen_v1',
            'is_new_user': is_new_user,
            'diversity_applied': diversity_applied,
            'next_cursor': next_cursor,
            'feed_status': feed_status,
        }
```

Update the existing algorithm marker test in `test_small_circle_feed_ranking.py` from `small_circle_fresh_v1` to `small_circle_fresh_cursor_seen_v1`.

- [ ] **Step 8: Run focused tests and compile**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_home_feed_cursor_seen tests.test_small_circle_feed_ranking
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m py_compile app/services/recommendation_service.py app/routers/recommendations.py
```

Expected: tests PASS, py_compile exits 0.

---

## Task 5: Add compatibility and performance regression guards

**Files:**
- Modify: `D:\NanTuPy\tests\test_small_circle_feed_ranking.py`
- Modify: `D:\NanTuPy\tests\test_home_feed_cursor_seen.py`
- Modify if tests reveal gaps: `D:\NanTuPy\app\services\recommendation_service.py`

- [ ] **Step 1: Add source-level regression tests for route and query constraints**

Append to `D:\NanTuPy\tests\test_home_feed_cursor_seen.py`:

```python
class HomeFeedRouteAndPerformanceSourceTest(unittest.TestCase):
    def _route_source(self):
        with open('app/routers/recommendations.py', 'r', encoding='utf-8') as f:
            return f.read()

    def _service_source(self):
        with open('app/services/recommendation_service.py', 'r', encoding='utf-8') as f:
            return f.read()

    def test_route_accepts_cursor_and_clamps_page(self):
        source = self._route_source()
        self.assertIn('cursor: str | None = Query(None)', source)
        self.assertIn('page = min(page, 50)', source)
        self.assertIn('cursor=cursor', source)

    def test_cursor_selection_has_bounded_candidate_limit(self):
        source = self._service_source()
        helper_source = source.split('def _select_cursor_feed_page')[1].split('def get_personalized_feed')[0]
        self.assertIn('query.limit(per_page * 10).all()', helper_source)
        self.assertNotIn('while True', helper_source)
        self.assertNotIn('.offset(', helper_source)

    def test_seen_write_occurs_after_serialization(self):
        source = self._service_source()
        feed_source = source.split('def get_personalized_feed')[1].split('def get_trending_posts')[0]
        serialize_index = feed_source.index('serialized_posts = RecommendationService._serialize_posts')
        seen_index = feed_source.index('RecommendationService._record_feed_seen')
        self.assertLess(serialize_index, seen_index)
```

- [ ] **Step 2: Run tests and verify they fail if implementation missed any guard**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_home_feed_cursor_seen tests.test_small_circle_feed_ranking
```

Expected: PASS if previous tasks were implemented exactly; otherwise FAIL with a specific missing guard.

- [ ] **Step 3: Fix any missed guard with minimal changes**

If the route test fails, make `D:\NanTuPy\app\routers\recommendations.py` match:

```python
    page = min(page, 50)
    per_page = min(per_page, 20)
```

If the selection helper test fails, make `_select_cursor_feed_page` use exactly:

```python
        rows = query.limit(per_page * 10).all()
```

If serialization order fails, ensure `get_personalized_feed()` does:

```python
        batch_data = RecommendationService._batch_load_post_data(db, diversified_posts, user_id)
        serialized_posts = RecommendationService._serialize_posts(diversified_posts, batch_data)
        RecommendationService._record_feed_seen(db, user_id, diversified_posts, now=now)
```

- [ ] **Step 4: Run focused tests again**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_home_feed_cursor_seen tests.test_small_circle_feed_ranking
```

Expected: PASS.

---

## Task 6: Full backend verification and migration sanity checks

**Files:**
- No source changes expected.

- [ ] **Step 1: Compile changed backend files**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m py_compile app/models/models.py app/services/recommendation_service.py app/routers/recommendations.py alembic/versions/2026_06_20_1200-7f3c2a9b8d10_add_post_feed_seen.py
```

Expected: exit code 0.

- [ ] **Step 2: Run focused recommendation tests**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_home_feed_cursor_seen tests.test_small_circle_feed_ranking
```

Expected: all tests pass.

- [ ] **Step 3: Run backend regression suite used for this optimization batch**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_performance_observability tests.test_chat_performance_contracts tests.test_posts_liked_performance_contract tests.test_explore_batching_contracts tests.test_search_guardrails tests.test_ws_validation_unit tests.test_ws_manager_scheduling tests.test_notification_service_unit tests.test_cors_config tests.test_community_service_sql tests.test_small_circle_feed_ranking tests.test_home_feed_cursor_seen
```

Expected: all tests pass.

- [ ] **Step 4: Generate offline migration SQL for review only**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m alembic upgrade head --sql
```

Expected: command prints SQL including `CREATE TABLE post_feed_seen` and both indexes. Do not apply this migration to the remote/current database unless the user explicitly confirms.

- [ ] **Step 5: Report results and remaining deployment step**

Report:

```text
Implemented locally, tests passing/failing with exact command output.
DB migration file is ready, but not applied to the database.
Before production use, run Alembic upgrade or manually create post_feed_seen after confirmation.
No git commit was made.
```

---

## Self-review checklist

- Spec coverage:
  - `page <= 50`: Task 2 and Task 5.
  - `per_page <= 20`: Task 2.
  - 7-day seen de-dupe: Task 3.
  - 30/90-day windows: Task 4.
  - cursor pagination without deep offset: Task 4 and Task 5.
  - seen indexes: Task 1.
  - `next_cursor/feed_status`: Task 2 and Task 4.
  - non-blocking seen write: Task 3.
- Placeholder scan: no TBD/TODO placeholders are required for implementation.
- Type consistency:
  - Cursor helpers use `dict | None` and Python 3.10+ union syntax, matching current test environment.
  - Service signature is `cursor: str | None = None` and route passes `cursor=cursor`.
  - New model name is consistently `PostFeedSeen` and table name is consistently `post_feed_seen`.
- Deployment caution:
  - Migration is created but not applied automatically.
  - No commit steps are included because the user has not asked for commits.
