"""Persist versioned dynamic moderation rules.

Revision ID: 2026_07_18_0200
Revises: 2026_07_18_0100
Create Date: 2026-07-18 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "2026_07_18_0200"
down_revision: Union[str, Sequence[str], None] = "2026_07_18_0100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SQLITE_NAMING_CONVENTION = {
    "uq": "uq_%(table_name)s_%(column_0_name)s",
}
_SUPPORTED_DIALECTS = {"mysql", "sqlite"}
_MYSQL_MINIMUM_VERSION = (8, 0, 16)
_MYSQL_INDEX_BYTE_BUDGET = 3072
_MYSQL_UTF8MB4_BYTES_PER_CHARACTER = 4
_MYSQL_COMPOSITE_UNIQUE_BYTES = (
    (500 + 16) * _MYSQL_UTF8MB4_BYTES_PER_CHARACTER
)
_TARGET_COLUMNS = {
    "match_type",
    "category",
    "severity",
    "is_active",
    "row_version",
    "created_by",
    "updated_at",
}
_TARGET_OBJECT_NAMES = {
    "uq_sensitive_word_expression_type",
    "fk_sensitive_words_creator",
    "ck_sensitive_word_match_type",
    "ck_sensitive_word_category",
    "ck_sensitive_word_severity",
    "ck_sensitive_word_migration_added_created_at",
    "ix_sensitive_word_active_version",
    "ck_sensitive_word_version_singleton",
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


def _utc_now(dialect_name: str):
    if dialect_name == "mysql":
        return sa.func.utc_timestamp()
    if dialect_name == "sqlite":
        return sa.func.current_timestamp()
    raise RuntimeError("unsupported database dialect for moderation migration")


def _backfill_sensitive_words_statement(dialect_name: str):
    sensitive_words = _sensitive_words_table()
    now = _utc_now(dialect_name)
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


def _insert_initial_version_statement(dialect_name: str):
    versions = sa.table(
        "sensitive_word_versions",
        sa.column("id", sa.Integer()),
        sa.column("version", sa.Integer()),
        sa.column("updated_at", sa.DateTime()),
    )
    return versions.insert().values(
        id=1,
        version=1,
        updated_at=_utc_now(dialect_name),
    )


def _find_legacy_word_unique_name(connection, inspector=None) -> str:
    inspector = inspector or sa.inspect(connection)
    constraints = inspector.get_unique_constraints("sensitive_words")
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


def _validate_dialect_and_version(connection) -> str:
    dialect_name = getattr(getattr(connection, "dialect", None), "name", None)
    if dialect_name not in _SUPPORTED_DIALECTS:
        raise RuntimeError(
            "unsupported database dialect for moderation migration; "
            "only sqlite and mysql are supported"
        )
    if dialect_name == "mysql":
        if getattr(connection.dialect, "is_mariadb", False):
            raise RuntimeError(
                "MariaDB is not supported by the moderation migration"
            )
        version = getattr(connection.dialect, "server_version_info", None)
        if not version:
            raise RuntimeError(
                "MySQL server version is unknown; moderation migration requires "
                "MySQL 8.0.16 or newer"
            )
        if tuple(version[:3]) < _MYSQL_MINIMUM_VERSION:
            raise RuntimeError(
                "moderation migration requires MySQL 8.0.16 or newer so CHECK "
                "constraints are enforced"
            )
    return dialect_name


def _mysql_option(options, *names):
    normalized = {
        str(key).lower().replace(" ", "_"): value
        for key, value in (options or {}).items()
    }
    for name in names:
        value = normalized.get(name)
        if value is not None:
            return str(value).strip()
    return None


def _read_mysql_effective_table_options(connection, table_name: str):
    statement = sa.text(
        "SELECT ENGINE AS engine, ROW_FORMAT AS row_format "
        "FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = :table_name"
    )
    try:
        row = connection.execute(
            statement, {"table_name": table_name}
        ).mappings().one_or_none()
    except Exception:
        raise RuntimeError(
            "unable to verify MySQL table storage capabilities"
        ) from None
    if row is None:
        return {}
    return {str(key).lower(): value for key, value in row.items()}


def _mysql_storage_options(
    connection, inspector, table_name: str, *, require_row_format: bool
):
    try:
        options = inspector.get_table_options(table_name) or {}
    except Exception:
        raise RuntimeError(
            "unable to verify MySQL table storage capabilities"
        ) from None

    engine = _mysql_option(options, "mysql_engine", "engine")
    row_format = _mysql_option(
        options, "mysql_row_format", "row_format"
    )
    if engine is None or (
        require_row_format
        and (row_format is None or row_format.upper() == "DEFAULT")
    ):
        effective = _read_mysql_effective_table_options(connection, table_name)
        engine = engine or _mysql_option(effective, "engine")
        if require_row_format and (
            row_format is None or row_format.upper() == "DEFAULT"
        ):
            row_format = _mysql_option(effective, "row_format")
    return options, engine, row_format


def _mysql_index_byte_budget(connection) -> int:
    try:
        page_size = connection.execute(
            sa.text("SELECT @@innodb_page_size")
        ).scalar_one()
    except Exception:
        raise RuntimeError(
            "unable to verify MySQL InnoDB page size"
        ) from None
    if type(page_size) is not int or page_size not in {
        4096,
        8192,
        16384,
        32768,
        65536,
    }:
        raise RuntimeError("MySQL InnoDB page size is unknown or unsupported")
    if page_size == 4096:
        return 768
    if page_size == 8192:
        return 1536
    return _MYSQL_INDEX_BYTE_BUDGET


def _assert_mysql_users_id_fk_compatible(inspector, users_id_column) -> None:
    users_id_type = users_id_column.get("type")
    if type(users_id_type) is not mysql.INTEGER or bool(
        getattr(users_id_type, "unsigned", False)
    ):
        raise RuntimeError(
            "users.id must be a signed INTEGER for the MySQL moderation foreign key"
        )
    if users_id_column.get("nullable") is not False:
        raise RuntimeError(
            "users.id must be NOT NULL for the moderation foreign key"
        )

    try:
        key_definitions = [inspector.get_pk_constraint("users")]
        key_definitions.extend(inspector.get_unique_constraints("users"))
        key_definitions.extend(inspector.get_indexes("users"))
    except Exception:
        raise RuntimeError(
            "unable to verify the MySQL users.id referencable index"
        ) from None
    if not any(
        list(key.get("constrained_columns") or key.get("column_names") or ())[:1]
        == ["id"]
        for key in key_definitions
        if isinstance(key, dict)
    ):
        raise RuntimeError(
            "users.id must lead a MySQL referencable index"
        )


def _assert_mysql_index_strategy_safe(inspector, word_column) -> None:
    if _MYSQL_COMPOSITE_UNIQUE_BYTES > _MYSQL_INDEX_BYTE_BUDGET:
        raise RuntimeError(
            "moderation composite unique index exceeds the MySQL 3072-byte budget"
        )

    options = inspector.get_table_options("sensitive_words")
    table_charset = _mysql_option(
        options, "mysql_charset", "mysql_default_charset"
    )
    table_collation = _mysql_option(options, "mysql_collate")
    column_collation = getattr(word_column["type"], "collation", None)
    if not table_charset or table_charset.lower() != "utf8mb4":
        raise RuntimeError(
            "sensitive_words must use utf8mb4 for the moderation index budget"
        )
    if table_collation and not table_collation.lower().startswith("utf8mb4_"):
        raise RuntimeError(
            "sensitive_words collation must use utf8mb4 for the moderation index budget"
        )
    if column_collation and not column_collation.lower().startswith("utf8mb4_"):
        raise RuntimeError(
            "sensitive_words.word collation must use utf8mb4 for the moderation index budget"
        )


def _assert_mysql_upgrade_capabilities(
    connection, inspector, word_column, users_id_column
) -> None:
    sensitive_options, sensitive_engine, row_format = _mysql_storage_options(
        connection,
        inspector,
        "sensitive_words",
        require_row_format=True,
    )
    _, users_engine, _ = _mysql_storage_options(
        connection, inspector, "users", require_row_format=False
    )
    if not sensitive_engine or not users_engine:
        raise RuntimeError("MySQL table storage engine is unknown")
    if sensitive_engine.lower() != "innodb" or users_engine.lower() != "innodb":
        raise RuntimeError(
            "sensitive_words and users must both use InnoDB"
        )
    if not row_format:
        raise RuntimeError("sensitive_words MySQL row format is unknown")
    if row_format.upper() not in {"DYNAMIC", "COMPRESSED"}:
        raise RuntimeError(
            "sensitive_words MySQL row format must support large index prefixes"
        )

    table_charset = _mysql_option(
        sensitive_options, "mysql_charset", "mysql_default_charset"
    )
    table_collation = _mysql_option(sensitive_options, "mysql_collate")
    column_collation = getattr(word_column["type"], "collation", None)
    if not table_charset or table_charset.lower() != "utf8mb4":
        raise RuntimeError(
            "sensitive_words must use utf8mb4 for the moderation index budget"
        )
    if table_collation and not table_collation.lower().startswith("utf8mb4_"):
        raise RuntimeError(
            "sensitive_words collation must use utf8mb4 for the moderation index budget"
        )
    if column_collation and not column_collation.lower().startswith("utf8mb4_"):
        raise RuntimeError(
            "sensitive_words.word collation must use utf8mb4 for the moderation index budget"
        )

    if _MYSQL_COMPOSITE_UNIQUE_BYTES > _mysql_index_byte_budget(connection):
        raise RuntimeError(
            "moderation composite unique index exceeds the MySQL index key byte budget"
        )
    _assert_mysql_users_id_fk_compatible(inspector, users_id_column)


def _assert_target_names_available(inspector, table_names: list[str]) -> None:
    found_names = set()
    for table_name in table_names:
        for getter_name in (
            "get_unique_constraints",
            "get_check_constraints",
            "get_foreign_keys",
            "get_indexes",
        ):
            for item in getattr(inspector, getter_name)(table_name):
                name = item.get("name")
                if name:
                    found_names.add(name)
    conflicts = found_names & _TARGET_OBJECT_NAMES
    if conflicts:
        raise RuntimeError(
            "target database object name already exists for moderation migration"
        )


def _preflight_upgrade(connection):
    # Front-load predictable failures before MySQL's non-transactional DDL.
    # Permissions, connectivity, and other runtime DDL failures remain possible.
    dialect_name = _validate_dialect_and_version(connection)
    inspector = sa.inspect(connection)
    table_names = inspector.get_table_names()
    if "sensitive_words" not in table_names:
        raise RuntimeError("required source table sensitive_words does not exist")
    if "users" not in table_names:
        raise RuntimeError("required source table users does not exist")
    if "sensitive_word_versions" in table_names:
        raise RuntimeError(
            "target table sensitive_word_versions already exists"
        )
    users_columns = {
        column["name"]: column
        for column in inspector.get_columns("users")
    }
    if "id" not in users_columns or not isinstance(
        users_columns["id"]["type"], sa.Integer
    ):
        raise RuntimeError(
            "users.id must be an integer column for the moderation creator foreign key"
        )

    columns = {
        column["name"]: column
        for column in inspector.get_columns("sensitive_words")
    }
    required_columns = {"id", "word"}
    if not required_columns <= columns.keys():
        raise RuntimeError(
            "sensitive_words source table is missing required id or word column"
        )
    if not isinstance(columns["id"]["type"], sa.Integer):
        raise RuntimeError(
            "sensitive_words.id column must be an integer before migration"
        )
    word_type = columns["word"]["type"]
    if not isinstance(word_type, sa.String) or word_type.length != 100:
        raise RuntimeError(
            "sensitive_words.word column must be VARCHAR(100) before migration"
        )
    if columns["word"].get("nullable", True):
        raise RuntimeError(
            "sensitive_words.word column must be non-nullable before migration"
        )

    conflicts = _TARGET_COLUMNS & columns.keys()
    if conflicts:
        raise RuntimeError(
            "target column already exists on sensitive_words"
        )
    if "created_at" in columns and not isinstance(
        columns["created_at"]["type"], sa.DateTime
    ):
        raise RuntimeError(
            "sensitive_words.created_at column must be a datetime when present"
        )

    legacy_word_unique_name = _find_legacy_word_unique_name(
        connection, inspector
    )
    _assert_target_names_available(inspector, table_names)
    if dialect_name == "mysql":
        _assert_mysql_upgrade_capabilities(
            connection,
            inspector,
            columns["word"],
            users_columns["id"],
        )

    return {
        "dialect_name": dialect_name,
        "existing_columns": set(columns),
        "legacy_word_unique_name": legacy_word_unique_name,
    }


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


def _duplicate_word_statement():
    # GROUP BY deliberately delegates equality to the database's active collation;
    # Python normalization would not reproduce either SQLite or MySQL semantics.
    return sa.text(
        "SELECT word FROM sensitive_words "
        "GROUP BY word HAVING COUNT(*) > 1 LIMIT 1"
    )


def _assert_legacy_word_unique_is_expressible(connection) -> None:
    duplicate = connection.execute(_duplicate_word_statement()).first()
    if duplicate is not None:
        raise RuntimeError(
            "cannot downgrade sensitive_words: duplicate word values cannot be "
            "represented by the legacy unique constraint"
        )


def _preflight_downgrade(connection) -> bool:
    dialect_name = _validate_dialect_and_version(connection)
    inspector = sa.inspect(connection)
    table_names = inspector.get_table_names()
    if "sensitive_words" not in table_names:
        raise RuntimeError(
            "required downgrade source table sensitive_words does not exist"
        )
    if "sensitive_word_versions" not in table_names:
        raise RuntimeError(
            "required downgrade source table sensitive_word_versions does not exist"
        )

    columns = {
        column["name"]: column
        for column in inspector.get_columns("sensitive_words")
    }
    expected_columns = {"id", "word", "created_at"} | set(_RULE_COLUMNS)
    if set(columns) != expected_columns:
        raise RuntimeError(
            "sensitive_words does not match the expected moderation downgrade schema"
        )
    word_type = columns["word"]["type"]
    if not isinstance(word_type, sa.String) or word_type.length != 500:
        raise RuntimeError(
            "sensitive_words.word must be VARCHAR(500) before moderation downgrade"
        )

    unique_constraints = inspector.get_unique_constraints("sensitive_words")
    if not any(
        constraint.get("name") == "uq_sensitive_word_expression_type"
        and list(constraint.get("column_names") or ())
        == ["word", "match_type"]
        for constraint in unique_constraints
    ):
        raise RuntimeError(
            "required moderation unique constraint is missing before downgrade"
        )
    check_names = {
        constraint.get("name")
        for constraint in inspector.get_check_constraints("sensitive_words")
    }
    required_checks = {
        "ck_sensitive_word_match_type",
        "ck_sensitive_word_category",
        "ck_sensitive_word_severity",
    }
    if not required_checks <= check_names:
        raise RuntimeError(
            "required moderation check constraint is missing before downgrade"
        )
    if not any(
        foreign_key.get("name") == "fk_sensitive_words_creator"
        and list(foreign_key.get("constrained_columns") or ()) == ["created_by"]
        and foreign_key.get("referred_table") == "users"
        and list(foreign_key.get("referred_columns") or ()) == ["id"]
        for foreign_key in inspector.get_foreign_keys("sensitive_words")
    ):
        raise RuntimeError(
            "required moderation foreign key is missing before downgrade"
        )
    if not any(
        index.get("name") == "ix_sensitive_word_active_version"
        and list(index.get("column_names") or ())
        == ["is_active", "row_version"]
        for index in inspector.get_indexes("sensitive_words")
    ):
        raise RuntimeError(
            "required moderation index is missing before downgrade"
        )

    version_columns = {
        column["name"]
        for column in inspector.get_columns("sensitive_word_versions")
    }
    if version_columns != {"id", "version", "updated_at"}:
        raise RuntimeError(
            "sensitive_word_versions does not match the expected downgrade schema"
        )
    if not any(
        constraint.get("name") == "ck_sensitive_word_version_singleton"
        for constraint in inspector.get_check_constraints(
            "sensitive_word_versions"
        )
    ):
        raise RuntimeError(
            "required moderation version check constraint is missing before downgrade"
        )

    if dialect_name == "mysql":
        _assert_mysql_index_strategy_safe(inspector, columns["word"])
    _assert_word_length_safe_for_downgrade(connection)
    _assert_legacy_word_unique_is_expressible(connection)
    return "ck_sensitive_word_migration_added_created_at" in check_names


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
    preflight = _preflight_upgrade(connection)
    dialect_name = preflight["dialect_name"]
    existing_columns = preflight["existing_columns"]
    created_at_was_missing = "created_at" not in existing_columns

    _add_nullable_rule_columns(existing_columns)
    _alter_word_length(connection, 500)
    connection.execute(_backfill_sensitive_words_statement(dialect_name))
    _upgrade_constraints_and_nullability(
        connection,
        preflight["legacy_word_unique_name"],
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
    connection.execute(_insert_initial_version_statement(dialect_name))


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
    created_at_was_added = _preflight_downgrade(connection)
    op.drop_table("sensitive_word_versions")
    _downgrade_sensitive_words(connection, created_at_was_added)
    _alter_word_length(connection, 100)
