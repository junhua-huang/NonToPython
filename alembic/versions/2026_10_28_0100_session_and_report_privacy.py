"""Revocable credentials and separately stored public report feedback."""
from alembic import op
import sqlalchemy as sa

revision = "2026_10_28_0100"
down_revision = "2026_10_24_0100"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("reports", sa.Column("reporter_notice", sa.String(500), nullable=True))
    # Existing resolution_note values are internal and must never be backfilled
    # into the public field. Old reports use a safe status-based fallback.


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM users WHERE auth_version > 0")).scalar():
        raise RuntimeError("Downgrade would reactivate revoked credentials; retain auth_version.")
    op.drop_column("reports", "reporter_notice")
    op.drop_column("users", "auth_version")
