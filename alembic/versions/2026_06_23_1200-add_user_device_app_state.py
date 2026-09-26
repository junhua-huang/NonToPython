"""add_user_device_app_state

Revision ID: add_user_device_app_state
Revises: 7f3c2a9b8d10
Create Date: 2026-06-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_user_device_app_state'
down_revision: Union[str, Sequence[str], None] = '7f3c2a9b8d10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user_devices', sa.Column('app_state', sa.String(length=20), nullable=True))
    op.add_column('user_devices', sa.Column('app_state_updated_at', sa.DateTime(), nullable=True))
    op.execute("UPDATE user_devices SET app_state = 'unknown' WHERE app_state IS NULL")


def downgrade() -> None:
    op.drop_column('user_devices', 'app_state_updated_at')
    op.drop_column('user_devices', 'app_state')
