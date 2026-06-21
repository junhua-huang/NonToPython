"""align_schema_final

对齐模型与数据库的剩余差异（改库对齐模型，方向：放松约束）：
- nullable: likes.post_id / messages.content / notifications.title / notifications.content → 允许 NULL
- 类型: posts.images JSON → Text（代码用 json.loads 操作字符串）；reports.reason 50 → 200
- 关联表 FK 显式命名：post_topics / post_visibility / topic_followers（消除 _ibfk_* 噪音）

Revision ID: 81eae2bf4a6c
Revises: 57f2c21ebb73
Create Date: 2026-06-19 18:40:38.314926

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '81eae2bf4a6c'
down_revision: Union[str, Sequence[str], None] = '57f2c21ebb73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ── nullable 对齐（放松为可空，零风险）──
    # likes.post_id: NOT NULL → nullable=True（支持评论点赞场景）
    op.alter_column('likes', 'post_id',
               existing_type=mysql.INTEGER(),
               nullable=True)

    # messages.content: NOT NULL → nullable=True（图片/系统消息无文本）
    op.alter_column('messages', 'content',
               existing_type=mysql.TEXT(),
               nullable=True)

    # notifications.title/content: NOT NULL → nullable=True（某些通知类型无标题正文）
    op.alter_column('notifications', 'title',
               existing_type=mysql.VARCHAR(length=200),
               nullable=True)
    op.alter_column('notifications', 'content',
               existing_type=mysql.TEXT(),
               nullable=True)

    # ── 类型对齐 ──
    # posts.images: JSON → Text（应用层用 json.loads 操作字符串）
    op.alter_column('posts', 'images',
               existing_type=mysql.JSON(),
               type_=sa.Text(),
               existing_nullable=True)
    # reports.reason: VARCHAR(50) → VARCHAR(200)（模型声明 200）
    op.alter_column('reports', 'reason',
               existing_type=mysql.VARCHAR(length=50),
               type_=sa.String(length=200),
               existing_nullable=False)

    # ── 关联表 FK 显式命名（消除 MySQL _ibfk_* 噪音）──
    # post_topics
    op.drop_constraint('post_topics_ibfk_1', 'post_topics', type_='foreignkey')
    op.drop_constraint('post_topics_ibfk_2', 'post_topics', type_='foreignkey')
    op.create_foreign_key('fk_post_topics_post', 'post_topics', 'posts',
                          ['post_id'], ['id'], ondelete='CASCADE')
    op.create_foreign_key('fk_post_topics_topic', 'post_topics', 'topics',
                          ['topic_id'], ['id'], ondelete='CASCADE')

    # post_visibility
    op.drop_constraint('post_visibility_ibfk_1', 'post_visibility', type_='foreignkey')
    op.drop_constraint('post_visibility_ibfk_2', 'post_visibility', type_='foreignkey')
    op.create_foreign_key('fk_post_visibility_post', 'post_visibility', 'posts',
                          ['post_id'], ['id'], ondelete='CASCADE')
    op.create_foreign_key('fk_post_visibility_user', 'post_visibility', 'users',
                          ['user_id'], ['id'], ondelete='CASCADE')

    # topic_followers
    op.drop_constraint('topic_followers_ibfk_1', 'topic_followers', type_='foreignkey')
    op.drop_constraint('topic_followers_ibfk_2', 'topic_followers', type_='foreignkey')
    op.create_foreign_key('fk_topic_followers_user', 'topic_followers', 'users',
                          ['user_id'], ['id'], ondelete='CASCADE')
    op.create_foreign_key('fk_topic_followers_topic', 'topic_followers', 'topics',
                          ['topic_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    """Downgrade schema."""
    # nullable 收紧
    op.alter_column('notifications', 'content',
               existing_type=mysql.TEXT(),
               nullable=False)
    op.alter_column('notifications', 'title',
               existing_type=mysql.VARCHAR(length=200),
               nullable=False)
    op.alter_column('messages', 'content',
               existing_type=mysql.TEXT(),
               nullable=False)
    op.alter_column('likes', 'post_id',
               existing_type=mysql.INTEGER(),
               nullable=False)

    # 类型回退
    op.alter_column('reports', 'reason',
               existing_type=sa.String(length=200),
               type_=mysql.VARCHAR(length=50),
               existing_nullable=False)
    op.alter_column('posts', 'images',
               existing_type=sa.Text(),
               type_=mysql.JSON(),
               existing_nullable=True)

    # FK 回退为自动命名
    op.drop_constraint('fk_topic_followers_topic', 'topic_followers', type_='foreignkey')
    op.drop_constraint('fk_topic_followers_user', 'topic_followers', type_='foreignkey')
    op.create_foreign_key('topic_followers_ibfk_1', 'topic_followers', 'users',
                          ['user_id'], ['id'])
    op.create_foreign_key('topic_followers_ibfk_2', 'topic_followers', 'topics',
                          ['topic_id'], ['id'])

    op.drop_constraint('fk_post_visibility_user', 'post_visibility', type_='foreignkey')
    op.drop_constraint('fk_post_visibility_post', 'post_visibility', type_='foreignkey')
    op.create_foreign_key('post_visibility_ibfk_1', 'post_visibility', 'posts',
                          ['post_id'], ['id'])
    op.create_foreign_key('post_visibility_ibfk_2', 'post_visibility', 'users',
                          ['user_id'], ['id'])

    op.drop_constraint('fk_post_topics_topic', 'post_topics', type_='foreignkey')
    op.drop_constraint('fk_post_topics_post', 'post_topics', type_='foreignkey')
    op.create_foreign_key('post_topics_ibfk_1', 'post_topics', 'posts',
                          ['post_id'], ['id'])
    op.create_foreign_key('post_topics_ibfk_2', 'post_topics', 'topics',
                          ['topic_id'], ['id'])
