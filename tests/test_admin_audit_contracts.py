import json
from types import SimpleNamespace

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.models import AdminAuditLog, User
from app.services.admin_audit_service import record_admin_audit, sanitize_audit_metadata


def _session():
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_record_admin_audit_persists_sanitized_metadata_and_request_metadata():
    db = _session()
    admin = User(username="admin", email="admin@example.com", password_hash="hash")
    db.add(admin)
    db.flush()

    request = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers={"user-agent": "pytest-agent"},
    )

    record_admin_audit(
        db,
        admin_user_id=admin.id,
        action="hide_post",
        target_type="post",
        target_id=42,
        reason="违反社区规则",
        metadata={
            "status_before": "visible",
            "status_after": "hidden",
            "content": "must not persist",
            "signed_url": "must not persist",
            "cos_key": "must not persist",
            "counts": {"reports": 2, "secret": "must not persist"},
            "target_label": "post",
        },
        request=request,
    )

    row = db.query(AdminAuditLog).one()
    assert row.admin_user_id == admin.id
    assert row.action == "hide_post"
    assert row.target_type == "post"
    assert row.target_id == "42"
    assert row.reason == "违反社区规则"
    assert row.ip_address == "127.0.0.1"
    assert row.user_agent == "pytest-agent"
    metadata = json.loads(row.metadata_json)
    assert metadata["status_before"] == "visible"
    assert metadata["status_after"] == "hidden"
    assert metadata["target_label"] == "post"
    assert "content" not in metadata
    assert "signed_url" not in metadata
    assert "cos_key" not in metadata
    assert "must not persist" not in row.metadata_json


def test_record_admin_audit_truncates_long_fields():
    db = _session()
    admin = User(username="admin", email="admin@example.com", password_hash="hash")
    db.add(admin)
    db.flush()

    record_admin_audit(
        db,
        admin_user_id=admin.id,
        action="a" * 120,
        target_type="t" * 90,
        target_id="i" * 120,
        result="r" * 40,
        reason="x" * 700,
    )

    row = db.query(AdminAuditLog).one()
    assert len(row.action) == 80
    assert len(row.target_type) == 50
    assert len(row.target_id) == 80
    assert len(row.result) == 20
    assert len(row.reason) == 500


def test_sanitize_audit_metadata_allows_only_safe_whitelisted_keys():
    sanitized = sanitize_audit_metadata(
        {
            "status_before": "pending",
            "error_code": "CONTENT_REJECTED",
            "route_key": "not whitelisted",
            "body": "secret body",
            "raw_response": "provider xml",
            "token": "jwt",
            "password": "pw",
        }
    )

    assert sanitized == {
        "status_before": "pending",
        "error_code": "CONTENT_REJECTED",
    }


def test_record_admin_audit_ignores_write_failure(caplog):
    class BrokenSession:
        def add(self, item):
            raise RuntimeError("database failure with secret body")

        def flush(self):
            raise AssertionError("flush must not run after add failure")

    record_admin_audit(
        BrokenSession(),
        admin_user_id=1,
        action="hide_post",
        target_type="post",
        target_id=42,
        metadata={"content": "private body"},
    )

    assert "secret body" not in caplog.text
    assert "private body" not in caplog.text
