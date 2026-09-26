"""add_role_identity_system

Revision ID: add_role_identity_system
Revises: add_user_device_app_state
Create Date: 2026-06-23 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_role_identity_system'
down_revision: Union[str, Sequence[str], None] = 'add_user_device_app_state'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BUSINESS_ROLES = [
    ("event_organizer", "活动方", "活动/展会组织方", 10),
    ("coser", "Coser", "Coser 身份", 20),
    ("photographer", "摄影师", "摄影师身份", 30),
    ("wig_stylist", "毛娘", "假发造型师身份", 40),
    ("makeup_artist", "妆娘", "化妆师身份", 50),
    ("ticket_agent", "票代", "票务代理身份", 60),
    ("prop_maker", "道具师", "道具制作师身份", 70),
    ("costume_maker", "服装师", "服装制作师身份", 80),
    ("retoucher", "后期师", "后期修图师身份", 90),
]
SYSTEM_ROLES = [
    ("admin", "管理员", "平台管理员权限", 1000),
    ("super_admin", "超级管理员", "平台超级管理员权限", 1010),
    ("moderator", "审核员", "平台审核权限", 1020),
]


def _table_names(bind):
    return set(sa.inspect(bind).get_table_names())


def _column_names(bind, table_name):
    return {c["name"] for c in sa.inspect(bind).get_columns(table_name)}


def _add_column_if_missing(bind, table_name, column):
    if column.name not in _column_names(bind, table_name):
        op.add_column(table_name, column)


def _unique_constraints(bind, table_name):
    return sa.inspect(bind).get_unique_constraints(table_name)


def upgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)

    if "roles" not in tables:
        op.create_table(
            "roles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=32), nullable=False, unique=True),
            sa.Column("label", sa.String(length=32), nullable=False),
            sa.Column("description", sa.String(length=128), nullable=True, default=""),
            sa.Column("sort_order", sa.Integer(), nullable=True, default=0),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )

    if "user_roles" not in tables:
        op.create_table(
            "user_roles",
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )

    if "role_applications" not in tables:
        op.create_table(
            "role_applications",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=True, default="pending"),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("application_text", sa.Text(), nullable=True),
            sa.Column("proof_images", sa.Text(), nullable=True),
            sa.Column("portfolio_links", sa.Text(), nullable=True),
            sa.Column("contact_info", sa.String(length=255), nullable=True),
            sa.Column("extra_note", sa.Text(), nullable=True),
            sa.Column("review_comment", sa.Text(), nullable=True),
            sa.Column("reviewer_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        )
    else:
        _add_column_if_missing(bind, "role_applications", sa.Column("application_text", sa.Text(), nullable=True))
        _add_column_if_missing(bind, "role_applications", sa.Column("proof_images", sa.Text(), nullable=True))
        _add_column_if_missing(bind, "role_applications", sa.Column("portfolio_links", sa.Text(), nullable=True))
        _add_column_if_missing(bind, "role_applications", sa.Column("contact_info", sa.String(length=255), nullable=True))
        _add_column_if_missing(bind, "role_applications", sa.Column("extra_note", sa.Text(), nullable=True))

    tables = _table_names(bind)
    if "coser_profiles" not in tables:
        op.create_table(
            "coser_profiles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("cosname", sa.String(length=64), nullable=True, default=""),
            sa.Column("bio", sa.Text(), nullable=True),
            sa.Column("styles", sa.String(length=512), nullable=True, default=""),
            sa.Column("city", sa.String(length=32), nullable=True, default=""),
            sa.Column("is_available", sa.Boolean(), nullable=True, default=True),
            sa.Column("price_range_min", sa.Integer(), nullable=True, default=0),
            sa.Column("price_range_max", sa.Integer(), nullable=True, default=0),
            sa.Column("portfolio_images", sa.Text(), nullable=True),
            sa.Column("social_links", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )

    if "photographer_profiles" not in tables:
        op.create_table(
            "photographer_profiles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("equipment", sa.String(length=512), nullable=True, default=""),
            sa.Column("styles", sa.String(length=512), nullable=True, default=""),
            sa.Column("city", sa.String(length=32), nullable=True, default=""),
            sa.Column("is_available", sa.Boolean(), nullable=True, default=True),
            sa.Column("price_range_min", sa.Integer(), nullable=True, default=0),
            sa.Column("price_range_max", sa.Integer(), nullable=True, default=0),
            sa.Column("portfolio_images", sa.Text(), nullable=True),
            sa.Column("social_links", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )

    if "service_profiles" not in tables:
        op.create_table(
            "service_profiles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("service_type", sa.String(length=32), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("city", sa.String(length=32), nullable=True, default=""),
            sa.Column("is_available", sa.Boolean(), nullable=True, default=True),
            sa.Column("price_info", sa.String(length=512), nullable=True, default=""),
            sa.Column("portfolio_images", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("user_id", "service_type", name="uq_service_profiles_user_type"),
        )
    else:
        service_uniques = _unique_constraints(bind, "service_profiles")
        for constraint in service_uniques:
            if constraint.get("column_names") == ["user_id"] and constraint.get("name"):
                op.drop_constraint(constraint["name"], "service_profiles", type_="unique")
        if not any(c.get("column_names") == ["user_id", "service_type"] for c in service_uniques):
            op.create_unique_constraint("uq_service_profiles_user_type", "service_profiles", ["user_id", "service_type"])

    _add_column_if_missing(bind, "posts", sa.Column("content_category", sa.String(length=32), nullable=True))
    _add_column_if_missing(bind, "posts", sa.Column("display_role_type", sa.String(length=32), nullable=True))

    for name, label, description, sort_order in BUSINESS_ROLES + SYSTEM_ROLES:
        bind.execute(
            sa.text(
                "INSERT INTO roles (name, label, description, sort_order, created_at) "
                "VALUES (:name, :label, :description, :sort_order, NOW()) "
                "ON DUPLICATE KEY UPDATE label = VALUES(label), description = VALUES(description), sort_order = VALUES(sort_order)"
            ),
            {"name": name, "label": label, "description": description, "sort_order": sort_order},
        )

    bind.execute(sa.text("UPDATE role_applications SET status = 'verified' WHERE status = 'approved'"))


def downgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)
    if "posts" in tables:
        post_columns = _column_names(bind, "posts")
        if "display_role_type" in post_columns:
            op.drop_column("posts", "display_role_type")
        if "content_category" in post_columns:
            op.drop_column("posts", "content_category")

    if "role_applications" in tables:
        columns = _column_names(bind, "role_applications")
        for column_name in ["extra_note", "contact_info", "portfolio_links", "proof_images", "application_text"]:
            if column_name in columns:
                op.drop_column("role_applications", column_name)
        bind.execute(sa.text("UPDATE role_applications SET status = 'approved' WHERE status = 'verified'"))
