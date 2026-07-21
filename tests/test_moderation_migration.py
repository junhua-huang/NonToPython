import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.exc import IntegrityError

from app.models.models import SensitiveWord, SensitiveWordVersion, User


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "2026_07_18_0200_content_moderation_phase1.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "content_moderation_phase1_migration", MIGRATION_PATH
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


def test_migration_backfill_statements_compile_for_mysql_and_sqlite():
    migration = _load_migration()

    for dialect in (mysql.dialect(), sqlite.dialect()):
        sql = str(
            migration._backfill_sensitive_words_statement().compile(
                dialect=dialect, compile_kwargs={"literal_binds": True}
            )
        ).lower()
        assert "update sensitive_words" in sql
        assert "current_timestamp" in sql
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
            migration._insert_initial_version_statement().compile(
                dialect=dialect, compile_kwargs={"literal_binds": True}
            )
        ).lower()
        assert "insert into sensitive_word_versions" in insert_sql
        assert "current_timestamp" in insert_sql


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
    assert "_find_legacy_word_unique_name" in upgrade_source
    assert "_upgrade_constraints_and_nullability" in upgrade_source
    assert "uq_sensitive_word_expression_type" in source
    assert "fk_sensitive_words_creator" in source
    assert "ix_sensitive_word_active_version" in source
    assert "ck_sensitive_word_match_type" in source
    assert "ck_sensitive_word_category" in source
    assert "ck_sensitive_word_severity" in source
    assert "sensitive_word_versions" in upgrade_source

    assert downgrade_source.index(
        "_assert_word_length_safe_for_downgrade"
    ) < downgrade_source.index("_alter_word_length(connection, 100)")
    downgrade_entry = downgrade_source[downgrade_source.index("def downgrade") :]
    assert downgrade_entry.index(
        'drop_table("sensitive_word_versions")'
    ) < downgrade_entry.index("_downgrade_sensitive_words")
    assert downgrade_source.index(
        'drop_constraint(\n                "fk_sensitive_words_creator"'
    ) < downgrade_source.index('drop_column("created_by")')
    assert downgrade_source.index(
        'drop_index("ix_sensitive_word_active_version")'
    ) < downgrade_source.index('drop_column("row_version")')
    assert "create_unique_constraint" in downgrade_source


def test_migration_round_trips_legacy_rows_on_temporary_sqlite(monkeypatch):
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


def test_migration_restores_legacy_schema_when_created_at_was_missing(monkeypatch):
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
