"""Durable erasure queue and nullable attribution for independently shared records."""
from alembic import op
import sqlalchemy as sa

revision = "2026_10_24_0100"
down_revision = "2026_10_18_0100"
branch_labels = None
depends_on = None

ATTRIBUTIONS = (
    ("communities", "owner_id"), ("community_bans", "banned_by"),
    ("comic_events", "creator_id"), ("admin_audit_logs", "admin_user_id"),
    ("deployment_artifacts", "uploaded_by"), ("deployment_jobs", "created_by"),
)


def upgrade():
    for table, column in ATTRIBUTIONS:
        op.alter_column(table, column, existing_type=sa.Integer(), existing_nullable=False, nullable=True)
    op.create_table(
        "account_erasures",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("media_status", sa.String(32), nullable=False),
        sa.Column("legacy_keys_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("final_sweep_after", sa.DateTime(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("media_completed_at", sa.DateTime(), nullable=True),
        sa.Column("backup_status", sa.String(32), nullable=False),
    )
    op.create_index("ix_account_erasures_media_status", "account_erasures", ["media_status"])
    op.create_index("ix_account_erasures_next_attempt_at", "account_erasures", ["next_attempt_at"])


def downgrade():
    # Never silently discard deletion evidence or fabricate replacement users.
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT COUNT(*) FROM account_erasures")).scalar():
        raise RuntimeError("Erasure ledger must be preserved; downgrade requires a reviewed recovery plan.")
    for table, column in ATTRIBUTIONS:
        if connection.execute(sa.text(f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL")).scalar():
            raise RuntimeError("Deleted attribution cannot be made non-null without fabricating identities.")
    op.drop_table("account_erasures")
    for table, column in ATTRIBUTIONS:
        op.alter_column(table, column, existing_type=sa.Integer(), existing_nullable=True, nullable=False)
