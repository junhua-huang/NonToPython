"""drop user devices push table

Revision ID: 2026_06_28_1200
Revises: add_conversation_participant_read_cursors
Create Date: 2026-06-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '2026_06_28_1200'
down_revision: Union[str, Sequence[str], None] = 'add_conversation_participant_read_cursors'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table('user_devices'):
        op.drop_table('user_devices')


def downgrade() -> None:
    # The dropped table belonged to the removed vendor-push integration.
    # Downgrade intentionally leaves it absent.
    pass
