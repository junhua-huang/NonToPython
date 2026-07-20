"""add aliyun push devices

Revision ID: 2026_07_02_0200
Revises: 2026_06_28_1200
Create Date: 2026-07-02 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '2026_07_02_0200'
down_revision: Union[str, Sequence[str], None] = '2026_06_28_1200'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('push_devices',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('platform', sa.String(length=20), nullable=True),
        sa.Column('provider', sa.String(length=30), nullable=True),
        sa.Column('device_id', sa.String(length=128), nullable=False),
        sa.Column('manufacturer', sa.String(length=80), nullable=True),
        sa.Column('model', sa.String(length=120), nullable=True),
        sa.Column('app_version', sa.String(length=50), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_id', name='uq_push_devices_device_id'),
    )
    op.create_index(op.f('ix_push_devices_user_id'), 'push_devices', ['user_id'], unique=False)
    op.create_index(op.f('ix_push_devices_device_id'), 'push_devices', ['device_id'], unique=False)

    op.create_table('push_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.String(length=128), nullable=True),
        sa.Column('notification_id', sa.Integer(), nullable=True),
        sa.Column('notification_type', sa.String(length=50), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('status', sa.String(length=30), nullable=True),
        sa.Column('request_id', sa.String(length=128), nullable=True),
        sa.Column('message_id', sa.String(length=128), nullable=True),
        sa.Column('error_code', sa.String(length=80), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_push_logs_user_id'), 'push_logs', ['user_id'], unique=False)
    op.create_index(op.f('ix_push_logs_notification_id'), 'push_logs', ['notification_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_push_logs_notification_id'), table_name='push_logs')
    op.drop_index(op.f('ix_push_logs_user_id'), table_name='push_logs')
    op.drop_table('push_logs')
    op.drop_index(op.f('ix_push_devices_device_id'), table_name='push_devices')
    op.drop_index(op.f('ix_push_devices_user_id'), table_name='push_devices')
    op.drop_table('push_devices')
