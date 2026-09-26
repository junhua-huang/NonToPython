"""add quote posts

Revision ID: 2026_07_25_0300
Revises: 2026_07_25_0200
Create Date: 2026-07-25 03:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "2026_07_25_0300"
down_revision = "2026_07_25_0200"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("posts", sa.Column("quoted_post_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_posts_quoted_post", "posts", "posts", ["quoted_post_id"], ["id"])
    op.create_index("ix_posts_quoted_post_id", "posts", ["quoted_post_id"])


def downgrade():
    op.drop_index("ix_posts_quoted_post_id", table_name="posts")
    op.drop_constraint("fk_posts_quoted_post", "posts", type_="foreignkey")
    op.drop_column("posts", "quoted_post_id")
