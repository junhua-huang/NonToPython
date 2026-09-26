"""add_message_client_msg_id

Revision ID: add_message_client_msg_id
Revises: add_role_identity_system
Create Date: 2026-06-26 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_message_client_msg_id'
down_revision: Union[str, Sequence[str], None] = 'add_role_identity_system'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(bind, table_name):
    return {c['name'] for c in sa.inspect(bind).get_columns(table_name)}


def _index_names(bind, table_name):
    return {i['name'] for i in sa.inspect(bind).get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _column_names(bind, 'messages')
    if 'client_msg_id' not in columns:
        op.add_column('messages', sa.Column('client_msg_id', sa.String(length=128), nullable=True))
    if 'ix_messages_client_msg_id' not in _index_names(bind, 'messages'):
        op.create_index('ix_messages_client_msg_id', 'messages', ['client_msg_id'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if 'ix_messages_client_msg_id' in _index_names(bind, 'messages'):
        op.drop_index('ix_messages_client_msg_id', table_name='messages')
    if 'client_msg_id' in _column_names(bind, 'messages'):
        op.drop_column('messages', 'client_msg_id')
