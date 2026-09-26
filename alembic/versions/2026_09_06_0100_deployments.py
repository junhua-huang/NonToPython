"""Durable deployment artifacts and approval queue."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import MEDIUMTEXT

revision = "2026_09_06_0100"
down_revision = "2026_09_04_0100"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "deployment_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("manifest_json", sa.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False),
        sa.Column("bundle_sha256", sa.String(64), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "deployment_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("deployment_artifacts.id"), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("phase", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("events_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("migration_confirmed", sa.Boolean(), nullable=False),
        sa.Column("schema_compatible", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_deployment_jobs_status", "deployment_jobs", ["status"])


def downgrade():
    op.drop_index("ix_deployment_jobs_status", table_name="deployment_jobs")
    op.drop_table("deployment_jobs")
    op.drop_table("deployment_artifacts")
