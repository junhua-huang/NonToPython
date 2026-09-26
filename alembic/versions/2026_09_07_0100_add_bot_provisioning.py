"""Add bot-provisioned test accounts.

Revision ID: 2026_09_07_0100
Revises: 2026_09_06_0200
Create Date: 2026-09-07
"""
from alembic import op
import sqlalchemy as sa

revision = "2026_09_07_0100"
down_revision = "2026_09_06_0200"
branch_labels = None
depends_on = None

BOT_ROLE = ("bot", "机器人", "受控机器人/测试账号（管理员批量供应，非真实用户）", 1030)


def _column_names(bind, table_name):
    return {c["name"] for c in sa.inspect(bind).get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()

    # 1) users.is_bot 布尔列，幂等
    if "users" in {t for t in sa.inspect(bind).get_table_names()}:
        if "is_bot" not in _column_names(bind, "users"):
            op.add_column("users", sa.Column("is_bot", sa.Boolean(), nullable=False, server_default=sa.false()))

    # 2) 预置 bot 角色（幂等 upsert）
    tables = {t for t in sa.inspect(bind).get_table_names()}
    if "roles" in tables:
        bind.execute(
            sa.text(
                "INSERT INTO roles (name, label, description, sort_order, created_at) "
                "VALUES (:name, :label, :description, :sort_order, NOW()) "
                "ON DUPLICATE KEY UPDATE label = VALUES(label), description = VALUES(description), sort_order = VALUES(sort_order)"
            ),
            {"name": BOT_ROLE[0], "label": BOT_ROLE[1], "description": BOT_ROLE[2], "sort_order": BOT_ROLE[3]},
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = {t for t in sa.inspect(bind).get_table_names()}
    if "users" in tables and "is_bot" in _column_names(bind, "users"):
        op.drop_column("users", "is_bot")
