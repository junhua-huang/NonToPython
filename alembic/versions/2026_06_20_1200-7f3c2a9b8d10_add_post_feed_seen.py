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
    op.create_table('post_feed_seen',
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
    op.create_index('idx_post_feed_seen_user_source_seen_post', 'post_feed_seen', ['user_id', 'source', 'seen_at', 'post_id'], unique=False)
    op.create_index('idx_post_feed_seen_post_source', 'post_feed_seen', ['post_id', 'source'], unique=False)
    op.create_index('idx_post_feed_seen_seen_at', 'post_feed_seen', ['seen_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_post_feed_seen_seen_at', table_name='post_feed_seen')
    op.drop_index('idx_post_feed_seen_post_source', table_name='post_feed_seen')
    op.drop_index('idx_post_feed_seen_user_source_seen_post', table_name='post_feed_seen')
    op.drop_table('post_feed_seen')
