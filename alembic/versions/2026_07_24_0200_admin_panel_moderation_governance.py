"""add admin panel moderation governance fields

Revision ID: 2026_07_24_0200
Revises: 2026_07_24_0100
Create Date: 2026-07-24 02:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "2026_07_24_0200"
down_revision = "2026_07_24_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("comments", sa.Column("hidden_by_admin", sa.Boolean(), nullable=True))
    op.add_column("comments", sa.Column("hidden_by", sa.Integer(), nullable=True))
    op.add_column("comments", sa.Column("hidden_reason", sa.String(length=500), nullable=True))
    op.add_column("comments", sa.Column("hidden_at", sa.DateTime(), nullable=True))
    op.create_foreign_key("fk_comments_hidden_by", "comments", "users", ["hidden_by"], ["id"])

    op.add_column("reports", sa.Column("resolution", sa.String(length=50), nullable=True))
    op.add_column("reports", sa.Column("resolution_note", sa.String(length=500), nullable=True))
    op.add_column("reports", sa.Column("action_taken", sa.String(length=50), nullable=True))
    op.add_column("reports", sa.Column("resolved_by", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_reports_resolved_by", "reports", "users", ["resolved_by"], ["id"])

    op.create_table(
        "moderation_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("content_type", sa.String(length=20), nullable=False),
        sa.Column("route_key", sa.String(length=120), nullable=True),
        sa.Column("target_type", sa.String(length=80), nullable=True),
        sa.Column("target_id", sa.String(length=80), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("label", sa.String(length=80), nullable=True),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_moderation_events_actor"),
    )
    for column in [
        "provider",
        "content_type",
        "route_key",
        "target_type",
        "target_id",
        "actor_user_id",
        "decision",
        "error_code",
        "created_at",
    ]:
        op.create_index(f"ix_moderation_events_{column}", "moderation_events", [column])


def downgrade() -> None:
    for column in [
        "created_at",
        "error_code",
        "decision",
        "actor_user_id",
        "target_id",
        "target_type",
        "route_key",
        "content_type",
        "provider",
    ]:
        op.drop_index(f"ix_moderation_events_{column}", table_name="moderation_events")
    op.drop_table("moderation_events")

    op.drop_constraint("fk_reports_resolved_by", "reports", type_="foreignkey")
    op.drop_column("reports", "resolved_by")
    op.drop_column("reports", "action_taken")
    op.drop_column("reports", "resolution_note")
    op.drop_column("reports", "resolution")

    op.drop_constraint("fk_comments_hidden_by", "comments", type_="foreignkey")
    op.drop_column("comments", "hidden_at")
    op.drop_column("comments", "hidden_reason")
    op.drop_column("comments", "hidden_by")
    op.drop_column("comments", "hidden_by_admin")
