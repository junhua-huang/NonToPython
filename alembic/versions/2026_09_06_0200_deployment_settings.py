"""Persist non-sensitive deployment console settings."""
from alembic import op
import sqlalchemy as sa

revision = "2026_09_06_0200"
down_revision = "2026_09_06_0100"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "deployment_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        # MySQL rejects defaults on TEXT columns; the application supplies [] when creating the row.
        sa.Column("operator_ids_json", sa.Text(), nullable=False),
        sa.Column("max_bytes", sa.Integer(), nullable=False, server_default="524288000"),
        sa.Column("max_expanded_bytes", sa.Integer(), nullable=False, server_default="1073741824"),
        sa.Column("max_files", sa.Integer(), nullable=False, server_default="10000"),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("worker_heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("worker_status", sa.String(32), nullable=False, server_default="offline"),
        sa.Column("last_error_code", sa.String(64), nullable=True),
    )


def downgrade():
    op.drop_table("deployment_settings")
