"""cleanup legacy schema

清理历史 schema 债：
1. 把 `comic_tag`（10 行旧标签）数据迁移到模型新表 `comic_tags`，再删旧表
2. 删除其他纯废弃表：`shares`, `moderation_logs`, `comic_event`,
   `comic_event_image`, `comic_event_tag`, `comic_user_follow`,
   `comic_city`（模型对应 `comic_cities` 已有完整数据）
3. 删除 `post_visibility.created_at` 多余列（模型已无、库内全 NULL）
4. 修复 unique 索引：模型期望 unique=True 但库里 unique=False 的，
   重建为 unique（users.email/username, topics.name）

不处理的事项（autogenerate 还会报但属误报或无害）：
- FK 自动生成的 `ix_*_id`（unique=True 是 MySQL FK 的隐式索引，必需）
- `__table_args__` 未声明的旧联合 unique（如 `unique_block` 等，
  保留无害，业务依赖唯一约束防重）
- `comic_comment_likes.comment_id` 旧索引 vs 模型 `idx_ccl_user_comment`
  的复合索引（保留旧索引不影响）
- `messages.ix_messages_created_at` 等旧时序索引（保留可能加速查询）

Revision ID: 57f2c21ebb73
Revises: 1fd93272c5db
Create Date: 2026-06-19 17:33:02.008829
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '57f2c21ebb73'
down_revision: Union[str, Sequence[str], None] = '1fd93272c5db'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    # ── 1. 迁移 comic_tag → comic_tags（保留 id 主键以维持引用一致性）──
    # 旧表 comic_tag 字段：id, name(VARCHAR 32), tag_type, sort_order
    # 新表 comic_tags 字段：id, name(VARCHAR 64), tag_type, created_at
    # 仅当 comic_tag 存在且新表为空时执行（幂等）
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'comic_tag' in inspector.get_table_names():
        # 仅在新表为空时迁移，避免重复插入
        new_count = bind.execute(
            sa.text("SELECT COUNT(*) FROM comic_tags")
        ).scalar()
        if new_count == 0:
            bind.execute(sa.text(
                "INSERT INTO comic_tags (id, name, tag_type, created_at) "
                "SELECT id, name, tag_type, NOW() FROM comic_tag"
            ))

    # ── 2. 删除废弃表（按外键依赖顺序）──
    # 依赖链：comic_event_tag, comic_event_image, comic_user_follow → comic_event
    #         comic_event → comic_city
    #         comic_event_tag → comic_tag
    for legacy_table in (
        'comic_event_tag',
        'comic_event_image',
        'comic_user_follow',
        'comic_event',
        'comic_tag',          # 数据已迁移到 comic_tags
        'comic_city',         # 业务数据在 comic_cities，旧表无引用
        'shares',
        'moderation_logs',
    ):
        if legacy_table in inspector.get_table_names():
            op.execute(f"DROP TABLE IF EXISTS `{legacy_table}`")

    # ── 3. 删除 post_visibility.created_at（库内全 NULL，模型已无）──
    op.drop_column('post_visibility', 'created_at')

    # ── 4. 修复 unique 索引：drop + create unique ──
    # users.email
    op.drop_index('ix_users_email', table_name='users')
    op.create_index('ix_users_email', 'users', ['email'], unique=True)
    # users.username
    op.drop_index('ix_users_username', table_name='users')
    op.create_index('ix_users_username', 'users', ['username'], unique=True)
    # topics.name
    op.drop_index('ix_topics_name', table_name='topics')
    op.create_index('ix_topics_name', 'topics', ['name'], unique=True)


def downgrade() -> None:
    """Downgrade schema.

    本迁移涉及【不可逆操作】：
    - 已删除的废弃表数据无法恢复
    - 因此 downgrade 仅回退可逆部分（unique 索引改回 non-unique），
      并提供一个 RuntimeError 提示，避免误用。
    """
    # 索引恢复为 non-unique
    op.drop_index('ix_topics_name', table_name='topics')
    op.create_index('ix_topics_name', 'topics', ['name'], unique=False)
    op.drop_index('ix_users_username', table_name='users')
    op.create_index('ix_users_username', 'users', ['username'], unique=False)
    op.drop_index('ix_users_email', table_name='users')
    op.create_index('ix_users_email', 'users', ['email'], unique=False)
    # 恢复 post_visibility.created_at（仅结构）
    op.add_column(
        'post_visibility',
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    # 废弃表数据无法恢复，主动报错而不是默默吞掉
    raise RuntimeError(
        "downgrade only restores the index/column structure; "
        "dropped legacy tables (comic_tag/comic_event/shares/...) "
        "and their data CANNOT be recovered automatically."
    )
