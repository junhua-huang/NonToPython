"""add push log delivery claims

Revision ID: 2026_07_18_0100
Revises: 2026_07_03_0100
Create Date: 2026-07-18 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2026_07_18_0100"
down_revision: Union[str, Sequence[str], None] = "2026_07_03_0100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PRE_DEVICE_KEY = "__pre_device__"
_LEGACY_NULL_DEVICE_PREFIX = "__legacy_null_device__"
_LEGACY_NULL_NOTIFICATION_MARKER = "__legacy_null_notification__"
_LEGACY_NULL_BOTH_MARKER = "__legacy_null_both__"
_STATUS_PRIORITY = {
    "success": 4,
    "pending": 3,
    "failed": 2,
    "skipped": 1,
}


def _push_logs_table(*extra_columns):
    return sa.table(
        "push_logs",
        sa.column("id", sa.Integer),
        sa.column("notification_id", sa.Integer),
        sa.column("device_id", sa.String),
        sa.column("status", sa.String),
        sa.column("request_id", sa.String),
        sa.column("message_id", sa.String),
        sa.column("error_code", sa.String),
        sa.column("error_message", sa.Text),
        sa.column("title", sa.String),
        sa.column("created_at", sa.DateTime),
        *extra_columns,
    )


def _normalize_legacy_null_keys(connection) -> dict[int, str]:
    """Give every dirty legacy row a non-null, collision-free delivery key."""
    push_logs = _push_logs_table()
    null_rows = connection.execute(
        sa.select(
            push_logs.c.id,
            push_logs.c.notification_id,
            push_logs.c.device_id,
        )
        .where(
            sa.or_(
                push_logs.c.notification_id.is_(None),
                push_logs.c.device_id.is_(None),
            )
        )
        .order_by(push_logs.c.id)
    ).mappings().all()

    minimum_notification_id = connection.execute(
        sa.select(sa.func.min(push_logs.c.notification_id)).where(
            push_logs.c.notification_id.is_not(None)
        )
    ).scalar_one_or_none()
    synthetic_base = min(minimum_notification_id or 0, 0)
    markers: dict[int, str] = {}

    for row in null_rows:
        values = {}
        if row["notification_id"] is None:
            # Legacy notification IDs are business IDs and therefore positive. Basing the
            # sentinel below the existing minimum and incorporating the primary key makes
            # every normalized orphan deterministic without colliding with a real pair.
            values["notification_id"] = synthetic_base - row["id"] - 1
            markers[row["id"]] = (
                _LEGACY_NULL_BOTH_MARKER
                if row["device_id"] is None
                else _LEGACY_NULL_NOTIFICATION_MARKER
            )
        if row["device_id"] is None:
            values["device_id"] = (
                f"{_LEGACY_NULL_DEVICE_PREFIX}{row['id']}"
                if row["notification_id"] is None
                else _PRE_DEVICE_KEY
            )
        connection.execute(
            push_logs.update().where(push_logs.c.id == row["id"]).values(**values)
        )

    return markers


def _dedupe_push_logs(connection) -> None:
    """Keep a success-preferred, latest row for every normalized delivery key."""
    push_logs = _push_logs_table()
    duplicate_keys = connection.execute(
        sa.select(push_logs.c.notification_id, push_logs.c.device_id)
        .group_by(push_logs.c.notification_id, push_logs.c.device_id)
        .having(sa.func.count(push_logs.c.id) > 1)
    ).all()

    for notification_id, device_id in duplicate_keys:
        rows = connection.execute(
            sa.select(push_logs)
            .where(
                push_logs.c.notification_id == notification_id,
                push_logs.c.device_id == device_id,
            )
            .order_by(push_logs.c.id)
        ).mappings().all()
        keeper = max(
            rows,
            key=lambda row: (
                _STATUS_PRIORITY.get(row["status"], 0),
                row["id"],
            ),
        )
        merged = dict(keeper)
        same_status_rows = [row for row in rows if row["status"] == keeper["status"]]
        for field in ("request_id", "message_id", "error_code", "error_message"):
            if merged.get(field) is None:
                merged[field] = next(
                    (
                        row[field]
                        for row in reversed(same_status_rows)
                        if row[field] is not None
                    ),
                    None,
                )
        connection.execute(
            push_logs.update()
            .where(push_logs.c.id == keeper["id"])
            .values(
                status=merged.get("status") or "failed",
                request_id=merged.get("request_id"),
                message_id=merged.get("message_id"),
                error_code=merged.get("error_code"),
                error_message=merged.get("error_message"),
                title=None,
            )
        )
        connection.execute(
            push_logs.delete().where(
                push_logs.c.notification_id == notification_id,
                push_logs.c.device_id == device_id,
                push_logs.c.id != keeper["id"],
            )
        )


def upgrade() -> None:
    connection = op.get_bind()
    legacy_markers = _normalize_legacy_null_keys(connection)
    _dedupe_push_logs(connection)

    with op.batch_alter_table("push_logs") as batch_op:
        batch_op.add_column(sa.Column("claim_token", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("claimed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "attempt_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
        batch_op.alter_column(
            "notification_id", existing_type=sa.Integer(), nullable=False
        )
        batch_op.alter_column(
            "device_id", existing_type=sa.String(length=128), nullable=False
        )
        batch_op.create_unique_constraint(
            "uq_push_logs_notification_device",
            ["notification_id", "device_id"],
        )

    push_logs = _push_logs_table(
        sa.column("claim_token", sa.String),
        sa.column("attempt_count", sa.Integer),
        sa.column("updated_at", sa.DateTime),
    )
    connection.execute(
        push_logs.update().values(
            attempt_count=sa.case(
                (push_logs.c.status.in_(("pending", "success", "failed")), 1),
                else_=0,
            ),
            updated_at=sa.func.coalesce(
                push_logs.c.created_at, sa.func.current_timestamp()
            ),
            title=None,
        )
    )
    for row_id, marker in legacy_markers.items():
        connection.execute(
            push_logs.update()
            .where(push_logs.c.id == row_id)
            .values(claim_token=marker)
        )


def downgrade() -> None:
    with op.batch_alter_table("push_logs") as batch_op:
        batch_op.drop_constraint(
            "uq_push_logs_notification_device", type_="unique"
        )
        batch_op.alter_column(
            "notification_id", existing_type=sa.Integer(), nullable=True
        )
        batch_op.alter_column(
            "device_id", existing_type=sa.String(length=128), nullable=True
        )

    connection = op.get_bind()
    push_logs = _push_logs_table(sa.column("claim_token", sa.String))
    connection.execute(
        push_logs.update()
        .where(
            push_logs.c.claim_token.in_(
                (_LEGACY_NULL_NOTIFICATION_MARKER, _LEGACY_NULL_BOTH_MARKER)
            )
        )
        .values(notification_id=None)
    )
    connection.execute(
        push_logs.update()
        .where(
            sa.or_(
                push_logs.c.device_id == _PRE_DEVICE_KEY,
                sa.and_(
                    push_logs.c.claim_token == _LEGACY_NULL_BOTH_MARKER,
                    push_logs.c.device_id.like(f"{_LEGACY_NULL_DEVICE_PREFIX}%"),
                ),
            )
        )
        .values(device_id=None)
    )

    with op.batch_alter_table("push_logs") as batch_op:
        batch_op.drop_column("updated_at")
        batch_op.drop_column("attempt_count")
        batch_op.drop_column("claimed_at")
        batch_op.drop_column("claim_token")
