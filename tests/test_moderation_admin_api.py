from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.dependencies import require_admin
from app.models.models import SensitiveWord, SensitiveWordVersion, User
from app.routers import admin as admin_router


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = "/api/admin/moderation/sensitive-words"
LEGACY = "/sensitive-words"
CONFLICT_DETAIL = {
    "code": "MODERATION_RULE_CONFLICT",
    "message": "规则已存在或发生冲突",
}
INVALID_CODE = "INVALID_MODERATION_RULE"


@pytest.fixture()
def moderation_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    session.add_all(
        [
            User(
                id=1,
                username="moderation-admin",
                email="moderation-admin@example.test",
                password_hash="x",
                is_admin=True,
            ),
            SensitiveWordVersion(id=1, version=10),
        ]
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def admin_app(moderation_db):
    test_app = FastAPI()
    test_app.include_router(admin_router.router)
    test_app.include_router(admin_router.moderation_router)

    def override_db():
        yield moderation_db

    test_app.dependency_overrides[get_db] = override_db
    test_app.dependency_overrides[require_admin] = lambda: moderation_db.get(User, 1)
    return test_app


@pytest.fixture()
def admin_client(admin_app):
    with TestClient(admin_app) as client:
        yield client


@pytest.fixture()
def created_rule(moderation_db):
    row = SensitiveWord(
        word="existing literal",
        match_type="literal",
        category="other",
        severity="medium",
        is_active=True,
        row_version=10,
        created_by=1,
    )
    moderation_db.add(row)
    moderation_db.commit()
    return row


def current_version(db):
    db.expire_all()
    return db.get(SensitiveWordVersion, 1).version


def test_canonical_crud_advances_version_once_per_success_and_aligns_row_version(
    admin_client, moderation_db
):
    before = current_version(moderation_db)

    created = admin_client.post(
        CANONICAL,
        json={
            "word": r"  wx[0-9]{5,12}  ",
            "match_type": "regex",
            "category": "spam",
            "severity": "medium",
            "is_active": True,
        },
    )
    assert created.status_code == 201
    created_body = created.json()["word"]
    assert created_body["word"] == r"wx[0-9]{5,12}"
    assert created_body["category"] == "spam"
    assert created_body["severity"] == "medium"
    assert created_body["row_version"] == before + 1
    assert current_version(moderation_db) == before + 1

    listed = admin_client.get(CANONICAL)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["words"]] == [created_body["id"]]

    patched = admin_client.patch(
        f"{CANONICAL}/{created_body['id']}",
        json={"category": "privacy", "severity": "high", "is_active": False},
    )
    assert patched.status_code == 200
    patched_body = patched.json()["word"]
    assert patched_body["category"] == "privacy"
    assert patched_body["severity"] == "high"
    assert patched_body["is_active"] is False
    assert patched_body["row_version"] == before + 2
    assert current_version(moderation_db) == before + 2

    deleted = admin_client.delete(f"{CANONICAL}/{created_body['id']}")
    assert deleted.status_code == 200
    assert current_version(moderation_db) == before + 3
    assert moderation_db.get(SensitiveWord, created_body["id"]) is None


def test_legacy_get_post_and_delete_remain_compatible(admin_client, moderation_db):
    before = current_version(moderation_db)
    created = admin_client.post(
        LEGACY,
        json={
            "word": " legacy literal ",
            "match_type": "literal",
            "category": "abuse",
            "severity": "low",
            "is_active": True,
        },
    )

    assert created.status_code == 201
    body = created.json()["word"]
    assert body["word"] == "legacy literal"
    assert body["row_version"] == before + 1
    assert admin_client.get(LEGACY).status_code == 200
    assert admin_client.delete(f"{LEGACY}/{body['id']}").status_code == 200
    assert current_version(moderation_db) == before + 2


@pytest.mark.parametrize(
    "payload",
    [
        {
            "word": r"(a+)+$",
            "match_type": "regex",
            "category": "spam",
            "severity": "high",
            "is_active": True,
        },
        {
            "word": "   ",
            "match_type": "literal",
            "category": "other",
            "severity": "medium",
            "is_active": True,
        },
        {
            "word": "valid",
            "match_type": "glob",
            "category": "other",
            "severity": "medium",
            "is_active": True,
        },
        {
            "word": "valid",
            "match_type": "literal",
            "category": "unknown",
            "severity": "medium",
            "is_active": True,
        },
        {
            "word": "valid",
            "match_type": "literal",
            "category": "other",
            "severity": "critical",
            "is_active": True,
        },
    ],
)
def test_rule_validation_precedes_version_mutation(
    admin_client, moderation_db, payload
):
    before = current_version(moderation_db)

    response = admin_client.post(CANONICAL, json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == INVALID_CODE
    assert current_version(moderation_db) == before
    assert moderation_db.query(SensitiveWord).count() == 0


def test_pydantic_rejects_missing_or_oversized_input_before_version_mutation(
    admin_client, moderation_db
):
    before = current_version(moderation_db)

    missing = admin_client.post(CANONICAL, json={"category": "spam"})
    oversized = admin_client.post(
        CANONICAL,
        json={
            "word": "x" * 501,
            "match_type": "literal",
            "category": "other",
            "severity": "medium",
        },
    )

    assert missing.status_code == 422
    assert oversized.status_code == 422
    assert current_version(moderation_db) == before


def test_regex_length_validation_does_not_echo_expression(admin_client, moderation_db):
    expression = "PRIVATE_REGEX_LEAK_7721" + ("x" * 501)
    before = current_version(moderation_db)

    response = admin_client.post(
        CANONICAL,
        json={
            "word": expression,
            "match_type": "regex",
            "category": "spam",
            "severity": "medium",
        },
    )

    assert response.status_code == 422
    assert expression not in str(response.json())
    assert current_version(moderation_db) == before


def test_non_string_regex_input_does_not_echo_expression(admin_client, moderation_db):
    expression = "PRIVATE_REGEX_OBJECT_LEAK_8812(a+)+$"
    before = current_version(moderation_db)

    response = admin_client.post(
        CANONICAL,
        json={
            "word": {"pattern": expression},
            "match_type": "regex",
            "category": "spam",
            "severity": "medium",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == INVALID_CODE
    assert expression not in str(response.json())
    assert current_version(moderation_db) == before


def test_patch_validates_resulting_rule_before_version_mutation(
    admin_client, moderation_db, created_rule
):
    before = current_version(moderation_db)

    response = admin_client.patch(
        f"{CANONICAL}/{created_rule.id}",
        json={"word": r"(a+)+$", "match_type": "regex"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == INVALID_CODE
    assert current_version(moderation_db) == before
    moderation_db.refresh(created_rule)
    assert created_rule.word == "existing literal"
    assert created_rule.match_type == "literal"
    assert created_rule.row_version == before


def test_patch_rejects_explicit_null_word_before_version_mutation(
    admin_client, moderation_db, created_rule
):
    before = current_version(moderation_db)

    response = admin_client.patch(
        f"{CANONICAL}/{created_rule.id}",
        json={"word": None},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == INVALID_CODE
    assert current_version(moderation_db) == before
    moderation_db.refresh(created_rule)
    assert created_rule.word == "existing literal"
    assert created_rule.row_version == before


def test_patch_rejects_explicit_null_is_active_before_version_mutation(
    admin_client, moderation_db, created_rule
):
    before = current_version(moderation_db)

    response = admin_client.patch(
        f"{CANONICAL}/{created_rule.id}",
        json={"is_active": None},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == INVALID_CODE
    assert current_version(moderation_db) == before
    moderation_db.refresh(created_rule)
    assert created_rule.is_active is True
    assert created_rule.row_version == before


def test_empty_patch_is_successful_and_advances_exactly_one_version(
    admin_client, moderation_db, created_rule
):
    before = current_version(moderation_db)

    response = admin_client.patch(f"{CANONICAL}/{created_rule.id}", json={})

    assert response.status_code == 200
    assert response.json()["word"]["row_version"] == before + 1
    assert current_version(moderation_db) == before + 1


def test_duplicate_conflict_rolls_back_rule_and_version_with_fixed_safe_contract(
    admin_client, moderation_db, created_rule
):
    before = current_version(moderation_db)

    response = admin_client.post(
        CANONICAL,
        json={
            "word": created_rule.word,
            "match_type": created_rule.match_type,
            "category": "spam",
            "severity": "high",
            "is_active": True,
        },
    )

    assert response.status_code == 409
    assert response.json() == {"detail": CONFLICT_DETAIL}
    assert current_version(moderation_db) == before
    assert moderation_db.query(SensitiveWord).count() == 1
    assert "existing literal" not in str(response.json())


def test_patch_conflict_rolls_back_fields_and_version(
    admin_client, moderation_db, created_rule
):
    other = SensitiveWord(
        word="other literal",
        match_type="literal",
        category="spam",
        severity="high",
        is_active=True,
        row_version=current_version(moderation_db),
        created_by=1,
    )
    moderation_db.add(other)
    moderation_db.commit()
    before = current_version(moderation_db)

    response = admin_client.patch(
        f"{CANONICAL}/{created_rule.id}",
        json={"word": other.word, "category": "privacy"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": CONFLICT_DETAIL}
    assert current_version(moderation_db) == before
    moderation_db.expire_all()
    unchanged = moderation_db.get(SensitiveWord, created_rule.id)
    assert unchanged.word == "existing literal"
    assert unchanged.category == "other"
    assert unchanged.row_version == before


def test_patch_reloads_current_row_after_version_lock_before_validation(
    monkeypatch, moderation_db, created_rule
):
    bind = moderation_db.get_bind()
    SessionFactory = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)
    stale_session = SessionFactory()
    concurrent_session = SessionFactory()
    stale_session.get(SensitiveWord, created_rule.id)
    before = current_version(moderation_db)
    original_lock = admin_router._lock_rule_version

    def concurrent_change(db):
        row = concurrent_session.get(SensitiveWord, created_rule.id)
        row.match_type = "regex"
        concurrent_session.commit()
        return original_lock(db)

    monkeypatch.setattr(admin_router, "_lock_rule_version", concurrent_change)
    try:
        with pytest.raises(HTTPException) as exc_info:
            admin_router.patch_sensitive_word(
                created_rule.id,
                admin_router.SensitiveWordPatch(word=r"(a+)+$"),
                moderation_db.get(User, 1),
                stale_session,
            )
    finally:
        stale_session.close()
        concurrent_session.close()

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == INVALID_CODE
    moderation_db.expire_all()
    unchanged = moderation_db.get(SensitiveWord, created_rule.id)
    assert unchanged.word == "existing literal"
    assert unchanged.match_type == "regex"
    assert current_version(moderation_db) == before


def test_delete_reloads_current_row_after_version_lock_before_mutation(
    monkeypatch, moderation_db, created_rule
):
    bind = moderation_db.get_bind()
    SessionFactory = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)
    stale_session = SessionFactory()
    concurrent_session = SessionFactory()
    rule_id = created_rule.id
    stale_session.get(SensitiveWord, rule_id)
    before = current_version(moderation_db)
    original_lock = admin_router._lock_rule_version

    def concurrent_delete(db):
        row = concurrent_session.get(SensitiveWord, rule_id)
        concurrent_session.delete(row)
        concurrent_session.commit()
        return original_lock(db)

    monkeypatch.setattr(admin_router, "_lock_rule_version", concurrent_delete)
    try:
        with pytest.raises(HTTPException) as exc_info:
            admin_router.delete_sensitive_word(
                rule_id,
                moderation_db.get(User, 1),
                stale_session,
            )
    finally:
        stale_session.close()
        concurrent_session.close()

    assert exc_info.value.status_code == 404
    assert current_version(moderation_db) == before
    assert moderation_db.get(SensitiveWord, rule_id) is None


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_canonical_not_found_contract(admin_client, method):
    kwargs = {"json": {"is_active": False}} if method == "patch" else {}
    response = getattr(admin_client, method)(f"{CANONICAL}/999999", **kwargs)

    assert response.status_code == 404
    assert response.json() == {"detail": "Sensitive word not found"}


def test_canonical_and_legacy_routes_require_admin(moderation_db):
    test_app = FastAPI()
    test_app.include_router(admin_router.router)
    test_app.include_router(admin_router.moderation_router)

    def override_db():
        yield moderation_db

    test_app.dependency_overrides[get_db] = override_db
    with TestClient(test_app) as client:
        assert client.get(CANONICAL).status_code == 401
        assert client.get(LEGACY).status_code == 401
        assert client.post(CANONICAL, json={"word": "x"}).status_code == 401


def test_main_registers_canonical_router_exactly_once_without_lifecycle_replacement():
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

    assert source.count("app.include_router(admin.moderation_router)") == 1
    assert "lifespan=lifespan" in source
    assert "poll_snapshots(snapshot_store, stop)" in source


def test_version_increment_uses_a_locked_singleton_row():
    source = (ROOT / "app" / "routers" / "admin.py").read_text(encoding="utf-8")

    assert "with_for_update()" in source
    assert "SensitiveWordVersion.id == 1" in source
