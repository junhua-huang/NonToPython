"""Native APNs installation bindings (tokens are never used as installation IDs)."""
from alembic import op
import sqlalchemy as sa

revision = "2026_10_18_0100"
down_revision = "2026_09_07_0100"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("push_devices", sa.Column("apns_token", sa.String(512), nullable=True))
    op.add_column("push_devices", sa.Column("apns_environment", sa.String(20), nullable=True))
    op.add_column("push_devices", sa.Column("apns_topic", sa.String(255), nullable=True))
    op.add_column("push_devices", sa.Column("binding_secret_hash", sa.String(64), nullable=True))


def downgrade():
    for name in ("binding_secret_hash", "apns_topic", "apns_environment", "apns_token"):
        op.drop_column("push_devices", name)
