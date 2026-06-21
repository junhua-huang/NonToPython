"""add_likes_comment_fk

补充 likes.comment_id → comments.id 的外键约束。
模型已声明 ForeignKey('comments.id')，库未建该 FK（无脏数据，可安全添加）。

Revision ID: c2e96a3103aa
Revises: 81eae2bf4a6c
Create Date: 2026-06-19 18:56:46.762298

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2e96a3103aa'
down_revision: Union[str, Sequence[str], None] = '81eae2bf4a6c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # likes.comment_id → comments.id FK（模型已声明，补齐库约束）
    op.create_foreign_key('fk_likes_comment', 'likes', 'comments',
                          ['comment_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_likes_comment', 'likes', type_='foreignkey')