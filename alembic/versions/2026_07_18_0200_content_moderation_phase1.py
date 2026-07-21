"""Persist versioned dynamic moderation rules.

Revision ID: 2026_07_18_0200
Revises: 2026_07_18_0100
Create Date: 2026-07-18 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2026_07_18_0200"
down_revision: Union[str, Sequence[str], None] = "2026_07_18_0100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SQLITE_NAMING_CONVENTION = {
    "uq": "uq_%(table_name)s_%(column_0_name)s",
}
_RULE_COLUMNS = {
    "match_type": sa.String(length=16),
    "category": sa.String(length=32),
    "severity": sa.String(length=16),
    "is_active": sa.Boolean(),
    "row_version": sa.Integer(),
    "created_by": sa.Integer(),
    "created_at": sa.DateTime(),
    "updated_at": sa.DateTime(),
}


def _sensitive_words_table() -> sa.TableClause:
    return sa.table(
        "sensitive_words",
        sa.column("word", sa.String(length=500)),
        sa.column("match_type", sa.String(length=16)),
        sa.column("category", sa.String(length=32)),
        sa.column("severity", sa.String(length=16)),
        sa.column("is_active", sa.Boolean()),
        sa.column("row_version", sa.Integer()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )


def _backfill_sensitive_words_statement():
    sensitive_words = _sensitive_words_table()
    now = sa.func.current_timestamp()
    created_at = sa.func.coalesce(sensitive_words.c.created_at, now)
    return sensitive_words.update().values(
        match_type="literal",
        category="other",
        severity="medium",
        is_active=True,
        row_version=1,
        created_at=created_at,
        updated_at=created_at,
    )


def _insert_initial_version_statement():
    versions = sa.table(
        "sensitive_word_versions",
        sa.column("id", sa.Integer()),
        sa.column("version", sa.Integer()),
        sa.column("updated_at", sa.DateTime()),
    )
    return versions.insert().values(
        id=1,
        version=1,
        updated_at=sa.func.current_timestamp(),
    )


def _find_legacy_word_unique_name(connection) -> str:
    constraints = sa.inspect(connection).get_unique_constraints("sensitive_words")
    matches = [
        constraint
        for constraint in constraints
        if list(constraint.get("column_names") or ()) == ["word"]
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "expected exactly one legacy unique constraint on sensitive_words.word"
        )

    name = matches[0].get("name")
    if name:
        return name
    if getattr(getattr(connection, "dialect", None), "name", None) == "sqlite":
        # batch_alter_table applies this same convention while reflecting SQLite's
        # otherwise-anonymous UNIQUE(word), making the inspected constraint droppable.
        return "uq_sensitive_words_word"
    raise RuntimeError("legacy sensitive_words.word unique constraint is unnamed")


def _max_word_length_statement(dialect_name: str):
    sensitive_words = _sensitive_words_table()
    length_function = (
        sa.func.length
        if dialect_name == "sqlite"
        else sa.func.char_length
    )
    return sa.select(sa.func.max(length_function(sensitive_words.c.word)))


def _assert_word_length_safe_for_downgrade(connection) -> None:
    maximum = connection.execute(
        _max_word_length_statement(connection.dialect.name)
    ).scalar_one_or_none()
    if maximum is not None and maximum > 100:
        raise RuntimeError(
            "cannot downgrade sensitive_words.word: existing value exceeds 100 characters"
        )


def _add_nullable_rule_columns(existing_columns: set[str]) -> None:
    for name, column_type in _RULE_COLUMNS.items():
        if name == "created_at" and name in existing_columns:
            continue
        op.add_column(
            "sensitive_words",
            sa.Column(name, column_type, nullable=True),
        )


def _alter_word_length(connection, length: int) -> None:
    old_length, new_length = ((100, 500) if length == 500 else (500, 100))
    if connection.dialect.name == "sqlite":
        with op.batch_alter_table(
            "sensitive_words",
            naming_convention=_SQLITE_NAMING_CONVENTION,
        ) as batch_op:
            batch_op.alter_column(
                "word",
                existing_type=sa.String(length=old_length),
                type_=sa.String(length=new_length),
                existing_nullable=False,
            )
        return

    op.alter_column(
        "sensitive_words",
        "word",
        existing_type=sa.String(length=old_length),
        type_=sa.String(length=new_length),
        existing_nullable=False,
    )


def _upgrade_constraints_and_nullability(
    connection,
    legacy_word_unique_name: str,
    created_at_was_missing: bool,
) -> None:
    if connection.dialect.name == "sqlite":
        with op.batch_alter_table(
            "sensitive_words",
            naming_convention=_SQLITE_NAMING_CONVENTION,
        ) as batch_op:
            for name in (
                "match_type",
                "category",
                "severity",
                "is_active",
                "row_version",
                "created_at",
                "updated_at",
            ):
                batch_op.alter_column(
                    name,
                    existing_type=_RULE_COLUMNS[name],
                    nullable=False,
                )
            batch_op.drop_constraint(legacy_word_unique_name, type_="unique")
            batch_op.create_unique_constraint(
                "uq_sensitive_word_expression_type",
                ["word", "match_type"],
            )
            batch_op.create_foreign_key(
                "fk_sensitive_words_creator",
                "users",
                ["created_by"],
                ["id"],
            )
            batch_op.create_check_constraint(
                "ck_sensitive_word_match_type",
                "match_type IN ('literal','regex')",
            )
            batch_op.create_check_constraint(
                "ck_sensitive_word_category",
                "category IN ('sexual','violence','illegal','abuse','hate','spam','privacy','other')",
            )
            batch_op.create_check_constraint(
                "ck_sensitive_word_severity",
                "severity IN ('low','medium','high')",
            )
            if created_at_was_missing:
                batch_op.create_check_constraint(
                    "ck_sensitive_word_migration_added_created_at",
                    "created_at IS NOT NULL",
                )
            batch_op.create_index(
                "ix_sensitive_word_active_version",
                ["is_active", "row_version"],
            )
        return

    for name in (
        "match_type",
        "category",
        "severity",
        "is_active",
        "row_version",
        "created_at",
        "updated_at",
    ):
        op.alter_column(
            "sensitive_words",
            name,
            existing_type=_RULE_COLUMNS[name],
            nullable=False,
        )
    op.drop_constraint(
        legacy_word_unique_name,
        "sensitive_words",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_sensitive_word_expression_type",
        "sensitive_words",
        ["word", "match_type"],
    )
    op.create_foreign_key(
        "fk_sensitive_words_creator",
        "sensitive_words",
        "users",
        ["created_by"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_sensitive_word_match_type",
        "sensitive_words",
        "match_type IN ('literal','regex')",
    )
    op.create_check_constraint(
        "ck_sensitive_word_category",
        "sensitive_words",
        "category IN ('sexual','violence','illegal','abuse','hate','spam','privacy','other')",
    )
    op.create_check_constraint(
        "ck_sensitive_word_severity",
        "sensitive_words",
        "severity IN ('low','medium','high')",
    )
    if created_at_was_missing:
        op.create_check_constraint(
            "ck_sensitive_word_migration_added_created_at",
            "sensitive_words",
            "created_at IS NOT NULL",
        )
    op.create_index(
        "ix_sensitive_word_active_version",
        "sensitive_words",
        ["is_active", "row_version"],
    )


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_columns = {
        column["name"] for column in inspector.get_columns("sensitive_words")
    }
    created_at_was_missing = "created_at" not in existing_columns
    legacy_word_unique_name = _find_legacy_word_unique_name(connection)

    _add_nullable_rule_columns(existing_columns)
    _alter_word_length(connection, 500)
    connection.execute(_backfill_sensitive_words_statement())
    _upgrade_constraints_and_nullability(
        connection,
        legacy_word_unique_name,
        created_at_was_missing,
    )

    op.create_table(
        "sensitive_word_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "id = 1",
            name="ck_sensitive_word_version_singleton",
        ),
    )
    connection.execute(_insert_initial_version_statement())


def _created_at_was_added_by_upgrade(connection) -> bool:
    return any(
        constraint.get("name")
        == "ck_sensitive_word_migration_added_created_at"
        for constraint in sa.inspect(connection).get_check_constraints(
            "sensitive_words"
        )
    )


def _downgrade_sensitive_words(
    connection, created_at_was_added: bool
) -> None:
    if connection.dialect.name == "sqlite":
        with op.batch_alter_table(
            "sensitive_words",
            naming_convention=_SQLITE_NAMING_CONVENTION,
        ) as batch_op:
            batch_op.drop_index("ix_sensitive_word_active_version")
            batch_op.drop_constraint(
                "ck_sensitive_word_severity", type_="check"
            )
            batch_op.drop_constraint(
                "ck_sensitive_word_category", type_="check"
            )
            batch_op.drop_constraint(
                "ck_sensitive_word_match_type", type_="check"
            )
            if created_at_was_added:
                batch_op.drop_constraint(
                    "ck_sensitive_word_migration_added_created_at",
                    type_="check",
                )
            batch_op.drop_constraint(
                "fk_sensitive_words_creator", type_="foreignkey"
            )
            batch_op.drop_constraint(
                "uq_sensitive_word_expression_type", type_="unique"
            )
            batch_op.create_unique_constraint(
                "uq_sensitive_words_word", ["word"]
            )
            if created_at_was_added:
                batch_op.drop_column("created_at")
            else:
                batch_op.alter_column(
                    "created_at",
                    existing_type=sa.DateTime(),
                    nullable=True,
                )
            batch_op.drop_column("updated_at")
            batch_op.drop_column("created_by")
            batch_op.drop_column("row_version")
            batch_op.drop_column("is_active")
            batch_op.drop_column("severity")
            batch_op.drop_column("category")
            batch_op.drop_column("match_type")
        return

    op.drop_index("ix_sensitive_word_active_version", table_name="sensitive_words")
    op.drop_constraint(
        "ck_sensitive_word_severity", "sensitive_words", type_="check"
    )
    op.drop_constraint(
        "ck_sensitive_word_category", "sensitive_words", type_="check"
    )
    op.drop_constraint(
        "ck_sensitive_word_match_type", "sensitive_words", type_="check"
    )
    if created_at_was_added:
        op.drop_constraint(
            "ck_sensitive_word_migration_added_created_at",
            "sensitive_words",
            type_="check",
        )
    op.drop_constraint(
        "fk_sensitive_words_creator", "sensitive_words", type_="foreignkey"
    )
    op.drop_constraint(
        "uq_sensitive_word_expression_type",
        "sensitive_words",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_sensitive_words_word", "sensitive_words", ["word"]
    )
    if created_at_was_added:
        op.drop_column("sensitive_words", "created_at")
    else:
        op.alter_column(
            "sensitive_words",
            "created_at",
            existing_type=sa.DateTime(),
            nullable=True,
        )
    for name in (
        "updated_at",
        "created_by",
        "row_version",
        "is_active",
        "severity",
        "category",
        "match_type",
    ):
        op.drop_column("sensitive_words", name)


def downgrade() -> None:
    connection = op.get_bind()
    _assert_word_length_safe_for_downgrade(connection)
    created_at_was_added = _created_at_was_added_by_upgrade(connection)
    op.drop_table("sensitive_word_versions")
    _downgrade_sensitive_words(connection, created_at_was_added)
    _alter_word_length(connection, 100)
