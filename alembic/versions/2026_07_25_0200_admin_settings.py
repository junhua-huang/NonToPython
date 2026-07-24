"""add admin settings

Revision ID: 2026_07_25_0200
Revises: 2026_07_25_0100
Create Date: 2026-07-25 02:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "2026_07_25_0200"
down_revision = "2026_07_25_0100"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "admin_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("value", sa.String(length=500), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name="fk_admin_settings_updated_by"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_admin_settings_key"),
    )
    op.create_index("ix_admin_settings_key", "admin_settings", ["key"])
    op.create_index("ix_admin_settings_updated_by", "admin_settings", ["updated_by"])
    op.create_index("ix_admin_settings_updated_at", "admin_settings", ["updated_at"])


def downgrade():
    op.drop_index("ix_admin_settings_updated_at", table_name="admin_settings")
    op.drop_index("ix_admin_settings_updated_by", table_name="admin_settings")
    op.drop_index("ix_admin_settings_key", table_name="admin_settings")
    op.drop_table("admin_settings")
