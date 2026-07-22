import importlib.util
import re
from datetime import datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.exc import IntegrityError

from app.models.models import SensitiveWord, SensitiveWordVersion, User, WSAckDedup


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "2026_07_18_0200_content_moderation_phase1.py"
)
WS_ACK_DEDUP_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "2026_07_21_0100_scope_ws_ack_dedup_by_user.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "content_moderation_phase1_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_ws_ack_dedup_migration():
    spec = importlib.util.spec_from_file_location(
        "scope_ws_ack_dedup_by_user_migration", WS_ACK_DEDUP_MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _constraint_names(table, constraint_type):
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, constraint_type)
    }


def _normalize_reflected_check(sqltext):
    return re.sub(r"[\s`\"\[\]()]", "", sqltext).lower()


def _create_legacy_schema(engine, *, include_created_at=True):
    legacy = sa.MetaData()
    sa.Table(
        "users",
        legacy,
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    columns = [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("word", sa.String(length=100), nullable=False, unique=True),
    ]
    if include_created_at:
        columns.append(sa.Column("created_at", sa.DateTime(), nullable=True))
    sensitive_words = sa.Table("sensitive_words", legacy, *columns)
    legacy.create_all(engine)
    return sensitive_words


def _assert_upgraded_sqlite_schema(connection):
    inspector = sa.inspect(connection)
    sensitive_word_columns = {
        column["name"]: column
        for column in inspector.get_columns("sensitive_words")
    }
    assert set(sensitive_word_columns) == {
        "id",
        "word",
        "match_type",
        "category",
        "severity",
        "is_active",
        "row_version",
        "created_by",
        "created_at",
        "updated_at",
    }
    assert sensitive_word_columns["word"]["type"].length == 500
    assert sensitive_word_columns["created_by"]["nullable"] is True
    assert all(
        not sensitive_word_columns[name]["nullable"]
        for name in (
            "id",
            "word",
            "match_type",
            "category",
            "severity",
            "is_active",
            "row_version",
            "created_at",
            "updated_at",
        )
    )

    unique_constraints = inspector.get_unique_constraints("sensitive_words")
    assert [
        (constraint["name"], constraint["column_names"])
        for constraint in unique_constraints
    ] == [("uq_sensitive_word_expression_type", ["word", "match_type"])]
    reflected_checks = {
        check["name"]: _normalize_reflected_check(check["sqltext"])
        for check in inspector.get_check_constraints("sensitive_words")
    }
    assert reflected_checks == {
        "ck_sensitive_word_match_type": "match_typein'literal','regex'",
        "ck_sensitive_word_category": (
            "categoryin'sexual','violence','illegal','abuse','hate','spam','privacy','other'"
        ),
        "ck_sensitive_word_severity": "severityin'low','medium','high'",
    }
    foreign_keys = inspector.get_foreign_keys("sensitive_words")
    assert len(foreign_keys) == 1
    assert {
        key: foreign_keys[0][key]
        for key in (
            "name",
            "constrained_columns",
            "referred_table",
            "referred_columns",
        )
    } == {
        "name": "fk_sensitive_words_creator",
        "constrained_columns": ["created_by"],
        "referred_table": "users",
        "referred_columns": ["id"],
    }
    assert {
        index["name"]: index["column_names"]
        for index in inspector.get_indexes("sensitive_words")
    } == {"ix_sensitive_word_active_version": ["is_active", "row_version"]}

    version_columns = {
        column["name"]: column
        for column in inspector.get_columns("sensitive_word_versions")
    }
    assert set(version_columns) == {"id", "version", "updated_at"}
    assert all(not version_columns[name]["nullable"] for name in version_columns)
    assert {
        check["name"]: _normalize_reflected_check(check["sqltext"])
        for check in inspector.get_check_constraints("sensitive_word_versions")
    } == {"ck_sensitive_word_version_singleton": "id=1"}


def test_ws_ack_dedup_model_is_scoped_by_user_and_client_msg_id():
    primary_key_columns = tuple(column.name for column in WSAckDedup.__table__.primary_key.columns)

    assert primary_key_columns == ("user_id", "client_msg_id")


def test_ws_ack_dedup_sqlite_migration_scopes_primary_key_by_user():
    migration = _load_ws_ack_dedup_migration()
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    sa.Table(
        "ws_ack_dedup",
        metadata,
        sa.Column("client_msg_id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO users (id) VALUES (1), (2)"))
        connection.execute(sa.text(
            "INSERT INTO ws_ack_dedup (client_msg_id, user_id, message_id, processed_at) "
            "VALUES ('same-client', 1, 10, '2026-07-21 00:00:00')"
        ))
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        original_op = migration.op
        migration.op = operations
        try:
            migration.upgrade()
        finally:
            migration.op = original_op

        assert sa.inspect(connection).get_pk_constraint("ws_ack_dedup") == {
            "constrained_columns": ["user_id", "client_msg_id"],
            "name": "pk_ws_ack_dedup_user_client_msg",
        }
        preserved = connection.execute(sa.text(
            "SELECT user_id, client_msg_id, message_id FROM ws_ack_dedup"
        )).all()
        assert preserved == [(1, "same-client", 10)]
        connection.execute(sa.text(
            "INSERT INTO ws_ack_dedup (user_id, client_msg_id, message_id, processed_at) "
            "VALUES (2, 'same-client', 20, '2026-07-21 00:00:01')"
        ))


def test_ws_ack_dedup_sqlite_downgrade_rejects_cross_user_duplicates():
    migration = _load_ws_ack_dedup_migration()
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    sa.Table(
        "ws_ack_dedup",
        metadata,
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("client_msg_id", sa.String(length=36), primary_key=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO users (id) VALUES (1), (2)"))
        connection.execute(sa.text(
            "INSERT INTO ws_ack_dedup (user_id, client_msg_id, message_id, processed_at) VALUES "
            "(1, 'same-client', 10, '2026-07-21 00:00:00'), "
            "(2, 'same-client', 20, '2026-07-21 00:00:01')"
        ))
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        original_op = migration.op
        migration.op = operations
        try:
            with pytest.raises(RuntimeError, match="cross-user client_msg_id duplicates"):
                migration.downgrade()
        finally:
            migration.op = original_op

        assert sa.inspect(connection).get_pk_constraint("ws_ack_dedup")["constrained_columns"] == [
            "user_id",
            "client_msg_id",
        ]


def test_sensitive_word_model_has_exact_versioned_rule_shape():
    columns = {column.name: column for column in sa.inspect(SensitiveWord).columns}

    assert set(columns) == {
        "id",
        "word",
        "match_type",
        "category",
        "severity",
        "is_active",
        "row_version",
        "created_by",
        "created_at",
        "updated_at",
    }
    assert columns["word"].type.length == 500
    assert columns["match_type"].type.length == 16
    assert columns["category"].type.length == 32
    assert columns["severity"].type.length == 16
    assert columns["created_by"].nullable is True
    assert all(
        columns[name].nullable is False
        for name in (
            "word",
            "match_type",
            "category",
            "severity",
            "is_active",
            "row_version",
            "created_at",
            "updated_at",
        )
    )


def test_sensitive_word_model_has_named_constraints_fk_and_index():
    table = SensitiveWord.__table__

    assert _constraint_names(table, sa.UniqueConstraint) == {
        "uq_sensitive_word_expression_type"
    }
    assert _constraint_names(table, sa.CheckConstraint) == {
        "ck_sensitive_word_match_type",
        "ck_sensitive_word_category",
        "ck_sensitive_word_severity",
    }
    assert {index.name: tuple(column.name for column in index.columns) for index in table.indexes} == {
        "ix_sensitive_word_active_version": ("is_active", "row_version")
    }
    created_by_fk = next(iter(table.c.created_by.foreign_keys))
    assert created_by_fk.constraint.name == "fk_sensitive_words_creator"
    assert created_by_fk.target_fullname == "users.id"


def test_sensitive_word_serialization_is_compatible_and_exact():
    word = SensitiveWord(
        id=7,
        word="blocked",
        match_type="literal",
        category="abuse",
        severity="high",
        is_active=True,
        row_version=3,
        created_by=11,
    )
    # SQL expressions deliberately exercise the existing None-safe serializer contract.
    word.created_at = None
    word.updated_at = None

    assert word.to_dict() == {
        "id": 7,
        "word": "blocked",
        "match_type": "literal",
        "category": "abuse",
        "severity": "high",
        "is_active": True,
        "row_version": 3,
        "created_by": 11,
        "created_at": None,
        "updated_at": None,
    }
    assert "creator" not in word.to_dict()


def test_sensitive_word_version_is_singleton_shaped():
    columns = {
        column.name: column for column in sa.inspect(SensitiveWordVersion).columns
    }

    assert SensitiveWordVersion.__tablename__ == "sensitive_word_versions"
    assert set(columns) == {"id", "version", "updated_at"}
    assert columns["version"].nullable is False
    assert columns["updated_at"].nullable is False
    assert _constraint_names(
        SensitiveWordVersion.__table__, sa.CheckConstraint
    ) == {"ck_sensitive_word_version_singleton"}


def test_models_create_and_enforce_core_contracts_on_temporary_sqlite():
    engine = sa.create_engine("sqlite://")
    User.__table__.create(engine)
    SensitiveWord.__table__.create(engine)
    SensitiveWordVersion.__table__.create(engine)

    with engine.begin() as connection:
        connection.execute(
            SensitiveWord.__table__.insert(),
            [
                {"word": "same", "match_type": "literal"},
                {"word": "same", "match_type": "regex"},
            ],
        )
        connection.execute(
            SensitiveWordVersion.__table__.insert().values(id=1, version=1)
        )

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                SensitiveWord.__table__.insert().values(
                    word="same", match_type="literal"
                )
            )

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                SensitiveWordVersion.__table__.insert().values(id=2, version=2)
            )


def test_migration_is_locked_to_the_observed_single_head():
    migration = _load_migration()

    assert MIGRATION_PATH.name == "2026_07_18_0200_content_moderation_phase1.py"
    assert migration.revision == "2026_07_18_0200"
    assert migration.down_revision == "2026_07_18_0100"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_migration_backfill_sql_compilation_uses_dialect_utc_expressions():
    migration = _load_migration()

    # This checks dialect SQL compilation only; it is not a real MySQL round trip.
    for dialect_name, dialect, expected_now, forbidden_now in (
        ("mysql", mysql.dialect(), "utc_timestamp()", "current_timestamp"),
        ("sqlite", sqlite.dialect(), "current_timestamp", "utc_timestamp"),
    ):
        sql = str(
            migration._backfill_sensitive_words_statement(dialect_name).compile(
                dialect=dialect, compile_kwargs={"literal_binds": True}
            )
        ).lower()
        assert "update sensitive_words" in sql
        assert expected_now in sql
        assert forbidden_now not in sql
        assert "coalesce(sensitive_words.created_at" in sql
        for assignment in (
            "match_type",
            "category",
            "severity",
            "is_active",
            "row_version",
            "created_at",
            "updated_at",
        ):
            assert assignment in sql

        insert_sql = str(
            migration._insert_initial_version_statement(dialect_name).compile(
                dialect=dialect, compile_kwargs={"literal_binds": True}
            )
        ).lower()
        assert "insert into sensitive_word_versions" in insert_sql
        assert expected_now in insert_sql
        assert forbidden_now not in insert_sql


def test_upgrade_discovers_only_the_exact_legacy_word_unique_constraint(monkeypatch):
    migration = _load_migration()

    class Inspector:
        def get_unique_constraints(self, table_name):
            assert table_name == "sensitive_words"
            return [
                {"name": "keep_pair", "column_names": ["word", "match_type"]},
                {"name": "keep_other", "column_names": ["category"]},
                {"name": "legacy_generated_name", "column_names": ["word"]},
            ]

    monkeypatch.setattr(migration.sa, "inspect", lambda connection: Inspector())
    assert (
        migration._find_legacy_word_unique_name(object())
        == "legacy_generated_name"
    )


def test_downgrade_word_length_guard_is_dialect_aware_and_never_truncates():
    migration = _load_migration()

    assert str(migration._duplicate_word_statement()) == (
        "SELECT word FROM sensitive_words GROUP BY word "
        "HAVING COUNT(*) > 1 LIMIT 1"
    )
    mysql_sql = str(
        migration._max_word_length_statement("mysql").compile(
            dialect=mysql.dialect()
        )
    ).lower()
    sqlite_sql = str(
        migration._max_word_length_statement("sqlite").compile(
            dialect=sqlite.dialect()
        )
    ).lower()
    assert "max(char_length(sensitive_words.word))" in mysql_sql
    assert "max(length(sensitive_words.word))" in sqlite_sql

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class Connection:
        class Dialect:
            name = "mysql"

        dialect = Dialect()

        def __init__(self, value):
            self.value = value

        def execute(self, statement):
            assert "char_length" in str(statement).lower()
            return Result(self.value)

    migration._assert_word_length_safe_for_downgrade(Connection(100))
    with pytest.raises(RuntimeError, match="exceeds 100 characters"):
        migration._assert_word_length_safe_for_downgrade(Connection(101))

    source = MIGRATION_PATH.read_text(encoding="utf-8").lower()
    assert "substr(" not in source
    assert "substring(" not in source
    assert "left(" not in source


def test_migration_source_preserves_rows_and_reverses_dependencies_safely():
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    upgrade_source = source[source.index("def upgrade") : source.index("def _downgrade_sensitive_words")]
    downgrade_source = source[source.index("def _downgrade_sensitive_words") :]

    assert "drop_table(\"sensitive_words\")" not in source
    assert upgrade_source.index("_add_nullable_rule_columns") < upgrade_source.index(
        "_backfill_sensitive_words_statement"
    )
    assert upgrade_source.index("_backfill_sensitive_words_statement") < upgrade_source.index(
        "_upgrade_constraints_and_nullability"
    )
    assert "_preflight_upgrade" in upgrade_source
    assert "_upgrade_constraints_and_nullability" in upgrade_source
    assert "uq_sensitive_word_expression_type" in source
    assert "fk_sensitive_words_creator" in source
    assert "ix_sensitive_word_active_version" in source
    assert "ck_sensitive_word_match_type" in source
    assert "ck_sensitive_word_category" in source
    assert "ck_sensitive_word_severity" in source
    assert "sensitive_word_versions" in upgrade_source

    downgrade_preflight_source = source[
        source.index("def _preflight_downgrade") :
        source.index("def _add_nullable_rule_columns")
    ]
    assert "_assert_word_length_safe_for_downgrade" in downgrade_preflight_source
    assert "_assert_legacy_word_unique_is_expressible" in downgrade_preflight_source
    downgrade_entry = downgrade_source[downgrade_source.index("def downgrade") :]
    assert downgrade_entry.index(
        "_preflight_downgrade"
    ) < downgrade_entry.index('drop_table("sensitive_word_versions")')
    assert downgrade_entry.index(
        "_preflight_downgrade"
    ) < downgrade_entry.index("_downgrade_sensitive_words")
    assert downgrade_source.index(
        'drop_constraint(\n                "fk_sensitive_words_creator"'
    ) < downgrade_source.index('drop_column("created_by")')
    assert downgrade_source.index(
        'drop_index("ix_sensitive_word_active_version")'
    ) < downgrade_source.index('drop_column("row_version")')
    assert "create_unique_constraint" in downgrade_source


def test_real_sqlite_upgrade_reflects_and_enforces_migration_contract(monkeypatch):
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")

    @sa.event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    sensitive_words = _create_legacy_schema(engine)
    with engine.begin() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        connection.execute(
            sensitive_words.insert().values(id=7, word="legacy", created_at=None)
        )
        context = MigrationContext.configure(connection)
        monkeypatch.setattr(migration, "op", Operations(context))
        migration.upgrade()

        _assert_upgraded_sqlite_schema(connection)
        reflected = sa.MetaData()
        upgraded_words = sa.Table(
            "sensitive_words", reflected, autoload_with=connection
        )
        versions = sa.Table(
            "sensitive_word_versions", reflected, autoload_with=connection
        )
        now = datetime(2026, 7, 21, 12, 0, 0)
        valid_word = {
            "word": "duplicate",
            "match_type": "literal",
            "category": "other",
            "severity": "medium",
            "is_active": True,
            "row_version": 1,
            "created_by": None,
            "created_at": now,
            "updated_at": now,
        }
        connection.execute(upgraded_words.insert().values(**valid_word))

        invalid_words = [
            {**valid_word, "word": "bad-match", "match_type": "glob"},
            {**valid_word, "word": "bad-category", "category": "unknown"},
            {**valid_word, "word": "bad-severity", "severity": "critical"},
            valid_word,
            {**valid_word, "word": "missing-creator", "created_by": 999},
        ]
        for invalid_word in invalid_words:
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(upgraded_words.insert().values(**invalid_word))

        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    versions.insert().values(id=2, version=2, updated_at=now)
                )

        assert connection.execute(
            sa.select(sa.func.count()).select_from(upgraded_words)
        ).scalar_one() == 2
        assert connection.execute(
            sa.select(sa.func.count()).select_from(versions)
        ).scalar_one() == 1


def test_real_sqlite_failed_downgrade_is_atomic_before_any_schema_change(monkeypatch):
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")

    @sa.event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    sensitive_words = _create_legacy_schema(engine)
    with engine.begin() as connection:
        connection.execute(
            sensitive_words.insert().values(id=7, word="legacy", created_at=None)
        )
        context = MigrationContext.configure(connection)
        monkeypatch.setattr(migration, "op", Operations(context))
        migration.upgrade()
        connection.execute(
            sa.text(
                "INSERT INTO sensitive_words "
                "(word, match_type, category, severity, is_active, row_version, "
                "created_by, created_at, updated_at) "
                "VALUES (:word, 'literal', 'other', 'medium', 1, 1, NULL, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"word": "x" * 101},
        )

        with pytest.raises(RuntimeError, match="exceeds 100 characters"):
            migration.downgrade()

        _assert_upgraded_sqlite_schema(connection)
        assert connection.execute(
            sa.text("SELECT word FROM sensitive_words WHERE length(word) = 101")
        ).scalar_one() == "x" * 101


def test_migration_round_trips_legacy_rows_on_real_temporary_sqlite(monkeypatch):
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")
    legacy = sa.MetaData()
    sa.Table(
        "users",
        legacy,
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    sensitive_words = sa.Table(
        "sensitive_words",
        legacy,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("word", sa.String(length=100), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    legacy.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            sensitive_words.insert().values(id=7, word="legacy", created_at=None)
        )
        context = MigrationContext.configure(connection)
        monkeypatch.setattr(migration, "op", Operations(context))
        migration.upgrade()

        inspector = sa.inspect(connection)
        upgraded_columns = {
            column["name"]: column
            for column in inspector.get_columns("sensitive_words")
        }
        assert set(upgraded_columns) == {
            "id",
            "word",
            "match_type",
            "category",
            "severity",
            "is_active",
            "row_version",
            "created_by",
            "created_at",
            "updated_at",
        }
        assert upgraded_columns["word"]["type"].length == 500
        assert all(
            not upgraded_columns[name]["nullable"]
            for name in (
                "word",
                "match_type",
                "category",
                "severity",
                "is_active",
                "row_version",
                "created_at",
                "updated_at",
            )
        )
        row = connection.execute(
            sa.text("SELECT * FROM sensitive_words WHERE id = 7")
        ).mappings().one()
        assert row["word"] == "legacy"
        assert row["match_type"] == "literal"
        assert row["category"] == "other"
        assert row["severity"] == "medium"
        assert row["is_active"] == 1
        assert row["row_version"] == 1
        assert row["created_at"] is not None
        assert row["updated_at"] == row["created_at"]
        assert connection.execute(
            sa.text("SELECT version FROM sensitive_word_versions WHERE id = 1")
        ).scalar_one() == 1

        migration.downgrade()

        inspector = sa.inspect(connection)
        downgraded_columns = {
            column["name"]: column
            for column in inspector.get_columns("sensitive_words")
        }
        assert set(downgraded_columns) == {"id", "word", "created_at"}
        assert downgraded_columns["word"]["type"].length == 100
        assert downgraded_columns["created_at"]["nullable"] is True
        assert [
            constraint["column_names"]
            for constraint in inspector.get_unique_constraints("sensitive_words")
        ] == [["word"]]
        assert "sensitive_word_versions" not in inspector.get_table_names()
        assert connection.execute(
            sa.text("SELECT id, word FROM sensitive_words")
        ).one() == (7, "legacy")


def test_real_sqlite_round_trip_restores_schema_when_created_at_was_missing(monkeypatch):
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")
    legacy = sa.MetaData()
    sa.Table(
        "users",
        legacy,
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    sensitive_words = sa.Table(
        "sensitive_words",
        legacy,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("word", sa.String(length=100), nullable=False, unique=True),
    )
    legacy.create_all(engine)

    with engine.begin() as connection:
        connection.execute(sensitive_words.insert().values(id=9, word="legacy"))
        context = MigrationContext.configure(connection)
        monkeypatch.setattr(migration, "op", Operations(context))

        migration.upgrade()
        assert "created_at" in {
            column["name"]
            for column in sa.inspect(connection).get_columns("sensitive_words")
        }

        migration.downgrade()
        assert {
            column["name"]
            for column in sa.inspect(connection).get_columns("sensitive_words")
        } == {"id", "word"}
        assert connection.execute(
            sa.text("SELECT id, word FROM sensitive_words")
        ).one() == (9, "legacy")


def test_real_sqlite_duplicate_word_downgrade_fails_before_any_ddl(monkeypatch):
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")
    sensitive_words = _create_legacy_schema(engine)

    with engine.begin() as connection:
        connection.execute(
            sensitive_words.insert().values(id=7, word="same", created_at=None)
        )
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()
        connection.execute(
            sa.text(
                "INSERT INTO sensitive_words "
                "(word, match_type, category, severity, is_active, row_version, "
                "created_by, created_at, updated_at) "
                "VALUES ('same', 'regex', 'other', 'medium', 1, 1, NULL, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

        with pytest.raises(RuntimeError, match="duplicate word"):
            migration.downgrade()

        _assert_upgraded_sqlite_schema(connection)
        assert connection.execute(
            sa.text(
                "SELECT word, match_type FROM sensitive_words "
                "WHERE word = 'same' ORDER BY match_type"
            )
        ).all() == [("same", "literal"), ("same", "regex")]
        assert connection.execute(
            sa.text(
                "SELECT id, version FROM sensitive_word_versions WHERE id = 1"
            )
        ).one() == (1, 1)


class _PreflightDialect:
    def __init__(self, name, server_version_info=None, *, is_mariadb=False):
        self.name = name
        self.server_version_info = server_version_info
        self.is_mariadb = is_mariadb


class _PreflightScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _PreflightMappingResult:
    def __init__(self, mapping):
        self.mapping = mapping

    def mappings(self):
        return self

    def one_or_none(self):
        return self.mapping


class _PreflightConnection:
    def __init__(
        self,
        dialect_name="sqlite",
        server_version_info=None,
        *,
        is_mariadb=False,
        innodb_page_size=16384,
        effective_table_options=None,
    ):
        self.dialect = _PreflightDialect(
            dialect_name, server_version_info, is_mariadb=is_mariadb
        )
        self.innodb_page_size = innodb_page_size
        self.effective_table_options = effective_table_options or {}
        self.read_queries = []

    def execute(self, statement, parameters=None):
        sql = str(statement).lower()
        parameters = parameters or {}
        self.read_queries.append((sql, parameters))
        if "@@innodb_page_size" in sql:
            return _PreflightScalarResult(self.innodb_page_size)
        if "information_schema.tables" in sql:
            assert ":table_name" in sql
            assert set(parameters) == {"table_name"}
            return _PreflightMappingResult(
                self.effective_table_options.get(parameters["table_name"])
            )
        raise AssertionError("upgrade preflight executed unexpected SQL")


class _PreflightOperations:
    def __init__(self, connection):
        self.connection = connection
        self.ddl_calls = []

    def get_bind(self):
        return self.connection

    def __getattr__(self, name):
        def unexpected_ddl(*_args, **_kwargs):
            self.ddl_calls.append(name)
            raise AssertionError(f"DDL called during failed preflight: {name}")

        return unexpected_ddl


class _UpgradeInspector:
    def __init__(
        self,
        mode="valid",
        *,
        users_id_type=None,
        users_id_nullable=False,
        users_pk=None,
        users_uniques=None,
        users_indexes=None,
        table_options=None,
    ):
        self.mode = mode
        self.users_id_type = users_id_type or mysql.INTEGER()
        self.users_id_nullable = users_id_nullable
        self.users_pk = users_pk if users_pk is not None else {"constrained_columns": ["id"]}
        self.users_uniques = users_uniques or []
        self.users_indexes = users_indexes or []
        self.table_options = table_options or {
            "sensitive_words": {
                "mysql_engine": "InnoDB",
                "mysql_row_format": "DYNAMIC",
                "mysql_charset": "utf8mb4",
                "mysql_collate": "utf8mb4_unicode_ci",
            },
            "users": {"mysql_engine": "InnoDB"},
        }

    def get_table_names(self):
        tables = ["users", "sensitive_words"]
        if self.mode == "missing_source":
            tables.remove("sensitive_words")
        if self.mode == "version_table":
            tables.append("sensitive_word_versions")
        if self.mode in {"constraint_name", "index_name", "fk_name"}:
            tables.append("other_table")
        return tables

    def get_columns(self, table_name):
        if table_name == "users":
            user_id_type = (
                sa.String(20)
                if self.mode == "bad_user_id_type"
                else self.users_id_type
            )
            return [
                {
                    "name": "id",
                    "type": user_id_type,
                    "nullable": self.users_id_nullable,
                }
            ]
        if table_name == "other_table":
            return [{"name": "id", "type": sa.Integer(), "nullable": False}]
        columns = [
            {"name": "id", "type": sa.Integer(), "nullable": False},
            {"name": "word", "type": sa.String(100), "nullable": False},
            {"name": "created_at", "type": sa.DateTime(), "nullable": True},
        ]
        if self.mode == "bad_id_type":
            columns[0]["type"] = sa.String(20)
        if self.mode == "bad_word_type":
            columns[1]["type"] = sa.Integer()
        if self.mode == "target_column":
            columns.append(
                {"name": "match_type", "type": sa.String(16), "nullable": True}
            )
        return columns

    def get_pk_constraint(self, table_name):
        if table_name == "users":
            return self.users_pk
        return {"constrained_columns": ["id"]}

    def get_unique_constraints(self, table_name):
        if table_name == "sensitive_words":
            legacy = [{"name": "uq_legacy", "column_names": ["word"]}]
            if self.mode == "missing_legacy_unique":
                return []
            if self.mode == "two_legacy_uniques":
                legacy.append({"name": "uq_legacy_2", "column_names": ["word"]})
            return legacy
        if table_name == "users":
            return self.users_uniques
        if table_name == "other_table" and self.mode == "constraint_name":
            return [
                {
                    "name": "uq_sensitive_word_expression_type",
                    "column_names": ["id"],
                }
            ]
        return []

    def get_check_constraints(self, table_name):
        return []

    def get_foreign_keys(self, table_name):
        if table_name == "other_table" and self.mode == "fk_name":
            return [{"name": "fk_sensitive_words_creator"}]
        return []

    def get_indexes(self, table_name):
        if table_name == "users":
            return self.users_indexes
        if table_name == "other_table" and self.mode == "index_name":
            return [{"name": "ix_sensitive_word_active_version"}]
        return []

    def get_table_options(self, table_name):
        options = dict(self.table_options.get(table_name, {}))
        if table_name == "sensitive_words" and self.mode == "bad_charset":
            options.update(
                {"mysql_charset": "utf8", "mysql_collate": "utf8_general_ci"}
            )
        return options


@pytest.mark.parametrize(
    "dialect_name,version,match",
    [
        ("postgresql", None, "unsupported database dialect"),
        ("mysql", None, "version is unknown"),
        ("mysql", (8, 0, 15), "8.0.16 or newer"),
    ],
)
def test_upgrade_preflight_rejects_dialect_or_mysql_version_before_ddl(
    monkeypatch, dialect_name, version, match
):
    migration = _load_migration()
    connection = _PreflightConnection(dialect_name, version)
    operations = _PreflightOperations(connection)
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration.sa, "inspect", lambda _connection: _UpgradeInspector())

    with pytest.raises(RuntimeError, match=match):
        migration.upgrade()

    assert operations.ddl_calls == []


def test_upgrade_preflight_rejects_mariadb_compatibility_dialect_before_ddl(
    monkeypatch,
):
    migration = _load_migration()
    connection = _PreflightConnection(
        "mysql", (10, 11, 0), is_mariadb=True
    )
    operations = _PreflightOperations(connection)
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration.sa, "inspect", lambda _connection: _UpgradeInspector())

    with pytest.raises(RuntimeError, match="MariaDB"):
        migration.upgrade()

    assert operations.ddl_calls == []


@pytest.mark.parametrize(
    "mode,match",
    [
        ("missing_source", "source table"),
        ("bad_id_type", "id column"),
        ("bad_word_type", "word column"),
        ("bad_user_id_type", "users.id"),
        ("target_column", "target column"),
        ("version_table", "target table"),
        ("missing_legacy_unique", "exactly one legacy"),
        ("two_legacy_uniques", "exactly one legacy"),
        ("constraint_name", "target database object name"),
        ("index_name", "target database object name"),
        ("fk_name", "target database object name"),
    ],
)
def test_upgrade_preflight_rejects_schema_conflicts_before_ddl(
    monkeypatch, mode, match
):
    migration = _load_migration()
    connection = _PreflightConnection()
    operations = _PreflightOperations(connection)
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(
        migration.sa, "inspect", lambda _connection: _UpgradeInspector(mode)
    )

    with pytest.raises(RuntimeError, match=match):
        migration.upgrade()

    assert operations.ddl_calls == []


def test_downgrade_preflight_rejects_missing_target_table_before_ddl(monkeypatch):
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")
    _create_legacy_schema(engine)

    with engine.begin() as connection:
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()
        connection.exec_driver_sql("DROP TABLE sensitive_word_versions")

        guarded_operations = _PreflightOperations(connection)
        monkeypatch.setattr(migration, "op", guarded_operations)
        with pytest.raises(RuntimeError, match="downgrade source table"):
            migration.downgrade()

        assert guarded_operations.ddl_calls == []
        assert {
            column["name"]
            for column in sa.inspect(connection).get_columns("sensitive_words")
        } == {
            "id",
            "word",
            "match_type",
            "category",
            "severity",
            "is_active",
            "row_version",
            "created_by",
            "created_at",
            "updated_at",
        }


def test_mysql_index_budget_and_utf8mb4_table_strategy_are_preflighted(monkeypatch):
    migration = _load_migration()

    assert migration._MYSQL_INDEX_BYTE_BUDGET == 3072
    assert migration._MYSQL_UTF8MB4_BYTES_PER_CHARACTER == 4
    assert migration._MYSQL_COMPOSITE_UNIQUE_BYTES == (500 + 16) * 4 == 2064
    assert migration._MYSQL_COMPOSITE_UNIQUE_BYTES <= migration._MYSQL_INDEX_BYTE_BUDGET

    connection = _PreflightConnection("mysql", (8, 0, 16))
    operations = _PreflightOperations(connection)
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(
        migration.sa,
        "inspect",
        lambda _connection: _UpgradeInspector("bad_charset"),
    )

    with pytest.raises(RuntimeError, match="utf8mb4"):
        migration.upgrade()

    assert operations.ddl_calls == []


def _run_failed_mysql_upgrade(monkeypatch, inspector, connection, match):
    migration = _load_migration()
    operations = _PreflightOperations(connection)
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration.sa, "inspect", lambda _connection: inspector)

    with pytest.raises(RuntimeError, match=match):
        migration.upgrade()

    assert operations.ddl_calls == []
    return migration


def test_mysql_dynamic_row_format_and_16kb_page_size_pass_preflight(monkeypatch):
    migration = _load_migration()
    connection = _PreflightConnection("mysql", (8, 0, 16), innodb_page_size=16384)
    inspector = _UpgradeInspector()
    monkeypatch.setattr(migration.sa, "inspect", lambda _connection: inspector)

    preflight = migration._preflight_upgrade(connection)

    assert preflight["dialect_name"] == "mysql"
    assert any("@@innodb_page_size" in sql for sql, _ in connection.read_queries)


@pytest.mark.parametrize("row_format", ["COMPACT", "redundant"])
def test_mysql_legacy_row_formats_fail_before_first_ddl(
    monkeypatch, row_format
):
    table_options = {
        "sensitive_words": {
            "mysql_engine": "InnoDB",
            "mysql_row_format": row_format,
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
        },
        "users": {"mysql_engine": "InnoDB"},
    }
    _run_failed_mysql_upgrade(
        monkeypatch,
        _UpgradeInspector(table_options=table_options),
        _PreflightConnection("mysql", (8, 0, 16)),
        "row format",
    )


@pytest.mark.parametrize("page_size", [4096, 8192])
def test_mysql_insufficient_page_size_budget_fails_before_first_ddl(
    monkeypatch, page_size
):
    _run_failed_mysql_upgrade(
        monkeypatch,
        _UpgradeInspector(),
        _PreflightConnection(
            "mysql", (8, 0, 16), innodb_page_size=page_size
        ),
        "index key byte budget",
    )


@pytest.mark.parametrize(
    "table_options,page_size,match",
    [
        (
            {
                "sensitive_words": {
                    "mysql_row_format": "DYNAMIC",
                    "mysql_charset": "utf8mb4",
                },
                "users": {"mysql_engine": "InnoDB"},
            },
            16384,
            "storage engine",
        ),
        (
            {
                "sensitive_words": {
                    "mysql_engine": "InnoDB",
                    "mysql_charset": "utf8mb4",
                },
                "users": {"mysql_engine": "InnoDB"},
            },
            16384,
            "row format",
        ),
        (
            {
                "sensitive_words": {
                    "mysql_engine": "InnoDB",
                    "mysql_row_format": "DYNAMIC",
                    "mysql_charset": "utf8mb4",
                },
                "users": {},
            },
            16384,
            "storage engine",
        ),
        (
            {
                "sensitive_words": {
                    "mysql_engine": "InnoDB",
                    "mysql_row_format": "DYNAMIC",
                    "mysql_charset": "utf8mb4",
                },
                "users": {"mysql_engine": "InnoDB"},
            },
            None,
            "page size",
        ),
        (
            {
                "sensitive_words": {
                    "mysql_engine": "InnoDB",
                    "mysql_row_format": "DYNAMIC",
                    "mysql_charset": "utf8mb4",
                },
                "users": {"mysql_engine": "InnoDB"},
            },
            12288,
            "page size",
        ),
    ],
)
def test_mysql_unknown_capabilities_fail_closed_before_first_ddl(
    monkeypatch, table_options, page_size, match
):
    _run_failed_mysql_upgrade(
        monkeypatch,
        _UpgradeInspector(table_options=table_options),
        _PreflightConnection(
            "mysql", (8, 0, 16), innodb_page_size=page_size
        ),
        match,
    )


def test_mysql_default_row_format_uses_parameterized_effective_metadata(monkeypatch):
    migration = _load_migration()
    table_options = {
        "sensitive_words": {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DEFAULT",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
        },
        "users": {"mysql_engine": "InnoDB"},
    }
    connection = _PreflightConnection(
        "mysql",
        (8, 0, 16),
        effective_table_options={
            "sensitive_words": {"ENGINE": "InnoDB", "ROW_FORMAT": "Dynamic"}
        },
    )
    monkeypatch.setattr(
        migration.sa,
        "inspect",
        lambda _connection: _UpgradeInspector(table_options=table_options),
    )

    migration._preflight_upgrade(connection)

    metadata_queries = [
        (sql, parameters)
        for sql, parameters in connection.read_queries
        if "information_schema.tables" in sql
    ]
    assert metadata_queries == [
        (
            metadata_queries[0][0],
            {"table_name": "sensitive_words"},
        )
    ]
    assert "sensitive_words" not in metadata_queries[0][0]


@pytest.mark.parametrize(
    "users_id_type",
    [mysql.BIGINT(), mysql.SMALLINT(), mysql.INTEGER(unsigned=True)],
    ids=["bigint", "smallint", "unsigned"],
)
def test_mysql_fk_rejects_incompatible_users_id_type_before_first_ddl(
    monkeypatch, users_id_type
):
    _run_failed_mysql_upgrade(
        monkeypatch,
        _UpgradeInspector(users_id_type=users_id_type),
        _PreflightConnection("mysql", (8, 0, 16)),
        "signed INTEGER",
    )


def test_mysql_fk_accepts_signed_integer_with_id_leading_index(monkeypatch):
    migration = _load_migration()
    inspector = _UpgradeInspector(
        users_id_type=mysql.INTEGER(unsigned=False),
        users_pk={"constrained_columns": []},
        users_indexes=[
            {"name": "ix_users_id_tenant", "column_names": ["id", "tenant_id"]}
        ],
    )
    connection = _PreflightConnection("mysql", (8, 0, 16))
    monkeypatch.setattr(migration.sa, "inspect", lambda _connection: inspector)

    assert migration._preflight_upgrade(connection)["dialect_name"] == "mysql"


@pytest.mark.parametrize(
    "inspector,match",
    [
        (_UpgradeInspector(users_id_nullable=True), "NOT NULL"),
        (
            _UpgradeInspector(
                users_pk={"constrained_columns": []},
                users_uniques=[{"column_names": ["tenant_id", "id"]}],
                users_indexes=[{"column_names": ["email", "id"]}],
            ),
            "referencable index",
        ),
        (
            _UpgradeInspector(
                table_options={
                    "sensitive_words": {
                        "mysql_engine": "MyISAM",
                        "mysql_row_format": "DYNAMIC",
                        "mysql_charset": "utf8mb4",
                    },
                    "users": {"mysql_engine": "MyISAM"},
                }
            ),
            "InnoDB",
        ),
        (
            _UpgradeInspector(
                table_options={
                    "sensitive_words": {
                        "mysql_engine": "InnoDB",
                        "mysql_row_format": "DYNAMIC",
                        "mysql_charset": "utf8mb4",
                    },
                    "users": {"mysql_engine": "MyISAM"},
                }
            ),
            "InnoDB",
        ),
    ],
    ids=["nullable", "no-index", "myisam", "engine-mismatch"],
)
def test_mysql_fk_schema_incompatibility_fails_before_first_ddl(
    monkeypatch, inspector, match
):
    _run_failed_mysql_upgrade(
        monkeypatch,
        inspector,
        _PreflightConnection("mysql", (8, 0, 16)),
        match,
    )
