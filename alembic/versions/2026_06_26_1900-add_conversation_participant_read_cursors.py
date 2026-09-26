"""add conversation participant read cursors

Revision ID: add_conversation_participant_read_cursors
Revises: add_message_client_msg_id
Create Date: 2026-06-26 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_conversation_participant_read_cursors'
down_revision: Union[str, Sequence[str], None] = 'add_message_client_msg_id'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(bind, table_name):
    return {c['name'] for c in sa.inspect(bind).get_columns(table_name)}


def _index_names(bind, table_name):
    return {i['name'] for i in sa.inspect(bind).get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _column_names(bind, 'conversation_participants')
    if 'last_read_at' not in columns:
        op.add_column('conversation_participants', sa.Column('last_read_at', sa.DateTime(), nullable=True))
    if 'last_read_message_id' not in columns:
        op.add_column('conversation_participants', sa.Column('last_read_message_id', sa.Integer(), nullable=True))

    indexes = _index_names(bind, 'conversation_participants')
    if 'ix_cp_conversation_user' not in indexes:
        op.create_index(
            'ix_cp_conversation_user',
            'conversation_participants',
            ['conversation_id', 'user_id'],
            unique=False,
        )
    if 'ix_cp_conversation_last_read_message' not in indexes:
        op.create_index(
            'ix_cp_conversation_last_read_message',
            'conversation_participants',
            ['conversation_id', 'last_read_message_id'],
            unique=False,
        )

    # Existing community members should not receive a historical unread backlog
    # after the cursor-based community unread model is introduced.
    op.execute("""
        UPDATE conversation_participants
        SET
            last_read_message_id = (
                SELECT MAX(m.id)
                FROM messages m
                WHERE m.conversation_id = conversation_participants.conversation_id
            ),
            last_read_at = (
                SELECT MAX(m.created_at)
                FROM messages m
                WHERE m.conversation_id = conversation_participants.conversation_id
            )
        WHERE conversation_id IN (
            SELECT id FROM conversations WHERE type = 'community'
        )
          AND last_read_message_id IS NULL
          AND last_read_at IS NULL
    """)


def downgrade() -> None:
    bind = op.get_bind()
    indexes = _index_names(bind, 'conversation_participants')
    if 'ix_cp_conversation_last_read_message' in indexes:
        op.drop_index('ix_cp_conversation_last_read_message', table_name='conversation_participants')
    if 'ix_cp_conversation_user' in indexes:
        op.drop_index('ix_cp_conversation_user', table_name='conversation_participants')

    columns = _column_names(bind, 'conversation_participants')
    if 'last_read_message_id' in columns:
        op.drop_column('conversation_participants', 'last_read_message_id')
    if 'last_read_at' in columns:
        op.drop_column('conversation_participants', 'last_read_at')
