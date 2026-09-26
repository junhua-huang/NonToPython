"""add push device app state

Revision ID: 2026_07_03_0100
Revises: 2026_07_02_0200
Create Date: 2026-07-03 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '2026_07_03_0100'
down_revision: Union[str, Sequence[str], None] = '2026_07_02_0200'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('push_devices', sa.Column('app_state', sa.String(length=20), nullable=True, server_default='unknown'))
    op.add_column('push_devices', sa.Column('app_state_updated_at', sa.DateTime(), nullable=True))
    op.add_column('push_devices', sa.Column('last_foreground_at', sa.DateTime(), nullable=True))
    op.add_column('push_devices', sa.Column('last_background_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('push_devices', 'last_background_at')
    op.drop_column('push_devices', 'last_foreground_at')
    op.drop_column('push_devices', 'app_state_updated_at')
    op.drop_column('push_devices', 'app_state')
