"""add app releases

Revision ID: 2026_09_04_0100
Revises: 2026_07_25_0300
Create Date: 2026-09-04 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "2026_09_04_0100"
down_revision = "2026_07_25_0300"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_releases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("version_name", sa.String(length=64), nullable=False),
        sa.Column("build_number", sa.Integer(), nullable=False),
        sa.Column("minimum_supported_build_number", sa.Integer(), nullable=False),
        sa.Column("force_update", sa.Boolean(), nullable=False),
        sa.Column("update_action", sa.String(length=16), nullable=False),
        sa.Column("download_url", sa.String(length=2048), nullable=False),
        sa.Column("release_notes", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.Column("sha256", sa.String(length=128), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "platform IN ('android','ios','windows','web')",
            name="ck_app_releases_platform",
        ),
        sa.CheckConstraint(
            "build_number >= 1",
            name="ck_app_releases_build_number",
        ),
        sa.CheckConstraint(
            "minimum_supported_build_number >= 0 "
            "AND minimum_supported_build_number <= build_number",
            name="ck_app_releases_minimum_build",
        ),
        sa.CheckConstraint(
            "update_action IN ('download','store','refresh')",
            name="ck_app_releases_update_action",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "platform",
            "channel",
            "build_number",
            name="uq_app_releases_platform_channel_build",
        ),
    )
    op.create_index("ix_app_releases_platform", "app_releases", ["platform"])
    op.create_index("ix_app_releases_channel", "app_releases", ["channel"])
    op.create_index("ix_app_releases_published_at", "app_releases", ["published_at"])
    op.create_index("ix_app_releases_enabled", "app_releases", ["enabled"])
    op.create_index(
        "ix_app_releases_lookup",
        "app_releases",
        ["platform", "channel", "enabled", "build_number"],
    )


def downgrade():
    op.drop_index("ix_app_releases_lookup", table_name="app_releases")
    op.drop_index("ix_app_releases_enabled", table_name="app_releases")
    op.drop_index("ix_app_releases_published_at", table_name="app_releases")
    op.drop_index("ix_app_releases_channel", table_name="app_releases")
    op.drop_index("ix_app_releases_platform", table_name="app_releases")
    op.drop_table("app_releases")
