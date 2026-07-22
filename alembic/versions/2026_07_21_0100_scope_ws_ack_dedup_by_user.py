"""Scope WebSocket ACK deduplication by user.

Revision ID: 2026_07_21_0100
Revises: 2026_07_18_0200
Create Date: 2026-07-21 01:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2026_07_21_0100"
down_revision: Union[str, Sequence[str], None] = "2026_07_18_0200"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.create_table(
            "_ws_ack_dedup_user_scoped",
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("client_msg_id", sa.String(length=36), nullable=False),
            sa.Column("message_id", sa.Integer(), nullable=True),
            sa.Column("processed_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint(
                "user_id",
                "client_msg_id",
                name="pk_ws_ack_dedup_user_client_msg",
            ),
        )
        op.execute(
            "INSERT INTO _ws_ack_dedup_user_scoped "
            "(user_id, client_msg_id, message_id, processed_at) "
            "SELECT user_id, client_msg_id, message_id, processed_at FROM ws_ack_dedup"
        )
        op.drop_table("ws_ack_dedup")
        op.rename_table("_ws_ack_dedup_user_scoped", "ws_ack_dedup")
        return

    op.drop_constraint("PRIMARY", "ws_ack_dedup", type_="primary")
    op.create_primary_key(
        "pk_ws_ack_dedup_user_client_msg",
        "ws_ack_dedup",
        ["user_id", "client_msg_id"],
    )


def _assert_downgrade_has_unique_client_msg_ids() -> None:
    duplicate = op.get_bind().execute(sa.text(
        "SELECT client_msg_id FROM ws_ack_dedup "
        "GROUP BY client_msg_id HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot downgrade ws_ack_dedup while cross-user client_msg_id duplicates exist"
        )


def downgrade() -> None:
    bind = op.get_bind()
    _assert_downgrade_has_unique_client_msg_ids()
    if bind.dialect.name == "sqlite":
        op.create_table(
            "_ws_ack_dedup_client_scoped",
            sa.Column("client_msg_id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("message_id", sa.Integer(), nullable=True),
            sa.Column("processed_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("client_msg_id", name="pk_ws_ack_dedup_client_msg"),
        )
        op.execute(
            "INSERT INTO _ws_ack_dedup_client_scoped "
            "(client_msg_id, user_id, message_id, processed_at) "
            "SELECT client_msg_id, user_id, message_id, processed_at FROM ws_ack_dedup"
        )
        op.drop_table("ws_ack_dedup")
        op.rename_table("_ws_ack_dedup_client_scoped", "ws_ack_dedup")
        return

    op.drop_constraint("PRIMARY", "ws_ack_dedup", type_="primary")
    op.create_primary_key(
        "pk_ws_ack_dedup_client_msg",
        "ws_ack_dedup",
        ["client_msg_id"],
    )
