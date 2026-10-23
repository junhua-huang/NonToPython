import json
import os
import asyncio
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import models as m
from app.models import community as c
from app.models import deployment as d
from app.models.privacy import AccountErasure
from app.services.account_erasure_service import erase_account, assert_user_reference_coverage
from app.services.privacy_cleanup_service import process_erasure, purge_account_prefix, prune_transient_records, MediaCleanupError

NOW = datetime(2026, 10, 24, 12)


class ErasureTests(unittest.TestCase):
    def setUp(self):
        test_url = os.environ.get("NONTO_ERASURE_TEST_DATABASE_URL", "sqlite:///:memory:")
        if not test_url.startswith("sqlite:") and "/nonto_erasure_tests" not in test_url:
            raise RuntimeError("Use only the dedicated ephemeral erasure test database")
        self.engine = create_engine(test_url, **({"poolclass": StaticPool, "connect_args":{"check_same_thread":False}}
                                                if test_url.startswith("sqlite:") else {}))
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        else:
            with self.engine.begin() as connection:
                connection.execute(text("DROP TABLE IF EXISTS external_reference"))
            Base.metadata.drop_all(self.engine)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.db.add_all([m.User(id=i, username=f"person{i}", email=f"person{i}@example.test", password_hash="secret") for i in (1, 2, 3)])
        self.db.add(m.Role(id=1, name="coser", label="Coser"))
        self.db.add(m.ComicCity(id=1, name="测试城市"))
        self.db.flush()
        self.db.add_all([c.Community(id=10, name="共享社群", owner_id=1),
                         c.Community(id=11, name="他人的社群", owner_id=2),
                         m.ComicEvent(id=20, name="共享活动", city_id=1, creator_id=1)])
        self.db.flush()
        self.db.add_all([m.Post(id=100, user_id=1, content="own post", community_id=10),
                         m.Post(id=101, user_id=2, content="peer post", community_id=11)])
        self.db.flush()
        self.db.add_all([m.Comment(id=200, user_id=1, post_id=101, content="own comment"),
                         m.Comment(id=202, user_id=3, post_id=100, content="comment on removed post"),
                         m.ComicComment(id=250, user_id=1, event_id=20, content="own comic comment"),
                         m.Conversation(id=30, user1_id=1, user2_id=2),
                         m.Conversation(id=31, type="community", community_id=10)])
        self.db.flush()
        self.db.add_all([m.Comment(id=201, user_id=2, post_id=101, parent_id=200, reply_to_user_id=1, content="peer reply"),
            m.ComicComment(id=251, user_id=2, event_id=20, parent_id=250, reply_to_user_id=1, content="peer comic reply"),
            m.Message(id=300, conversation_id=30, sender_id=1, content="own private message"),
            m.Message(id=302, conversation_id=31, sender_id=2, content="shared post text", message_type="post", related_id=100),
            m.Notification(id=500, user_id=2, sender_id=1, notification_type="mention", content="old copied content"),
            m.Notification(id=501, user_id=1, sender_id=2, notification_type="mention"),
            m.Notification(id=502, user_id=3, sender_id=2, notification_type="mention", content="peer notification")])
        self.db.flush()
        self.db.add_all([m.Message(id=301, conversation_id=30, sender_id=2, content="peer message",
                                 quote_message_id=300, quote_preview="own private message"),
            m.Like(user_id=3, post_id=100), m.Like(user_id=2, post_id=101, comment_id=200),
            m.ComicLike(user_id=1, event_id=20), m.ComicCommentLike(user_id=2, comment_id=250),
            c.CommunityMember(community_id=10, user_id=1, role="owner"),
            c.CommunityMember(community_id=10, user_id=2, role="admin"),
            c.CommunityMember(community_id=11, user_id=1),
            c.CommunityAnnouncement(community_id=10, author_id=1, title="own announcement"),
            c.CommunityBan(community_id=10, user_id=3, banned_by=1, reason="valid ban"),
            c.CommunityJoinRequest(community_id=11, user_id=3, reviewed_by=1),
            m.ConversationParticipant(conversation_id=30, user_id=1),
            m.ConversationParticipant(conversation_id=30, user_id=2),
            m.PushDevice(user_id=1, device_id="device1", apns_token="private-token"),
            m.PushDevice(user_id=2, device_id="device2"),
            m.PushLog(user_id=2, device_id="device2", notification_id=500, title="copied identity"),
            m.PushLog(user_id=3, device_id="device3", notification_id=502),
            m.NotificationDelivery(user_id=2, notification_id=500, channel="email", event_type="mention", recipient_email="private"),
            m.NotificationDelivery(user_id=3, notification_id=502, channel="push", event_type="mention"),
            m.WSMessageLog(user_id=2, seq=1, payload=json.dumps({"event":"new_message", "data":{"message":{"id":300,"sender_id":1,"content":"own private message"}}})),
            m.WSMessageLog(user_id=2, seq=2, payload=json.dumps({"event":"new_message", "data":{"message":{"id":301,"sender_id":2,"quote_message_id":300,"quote_preview":"own private message"}}})),
            m.WSMessageLog(user_id=2, seq=3, payload=json.dumps({"event":"new_message", "data":{"message":{"id":999,"sender_id":3,"content":"independent"}}})),
            m.WSAckDedup(user_id=1, client_msg_id="own", message_id=300),
            m.WSUserSeq(user_id=1, current_seq=5),
            m.EmailOtp(email="PERSON1@example.test", code="123456", purpose="login", expires_at=NOW),
            m.EmailOtp(email="person2@example.test", code="654321", purpose="login", expires_at=NOW),
            m.LoginAttempt(identifier="PERSON1", ip_address="127.0.0.1"),
            m.LoginAttempt(identifier="person2", ip_address="127.0.0.2"),
            m.SearchHistory(user_id=1, query="private search"),
            m.Report(reporter_id=1, target_type="post", target_id=101, reason="own report"),
            m.CoserProfile(user_id=1), m.UserRole(user_id=1, role_id=1),
            m.RoleApplication(user_id=2, role_id=1, reviewer_id=1, review_comment="reviewer's private note"),
            m.AdminAuditLog(admin_user_id=1, action="test", target_type="post", target_id="101", reason="private", metadata_json='{}'),
            d.DeploymentArtifact(id="artifact", manifest_json="{}", bundle_sha256="0"*64, uploaded_by=1),
            d.DeploymentSettings(id=1, operator_ids_json="[1,2]", updated_by=1)])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_complete_transaction_preserves_other_users_and_shared_content(self):
        assert_user_reference_coverage()
        erasure_id = erase_account(self.db, 1, now=NOW)
        self.db.commit()
        self.db.expire_all()
        self.assertIsNone(self.db.get(m.User, 1))
        self.assertIsNotNone(self.db.get(m.User, 2))
        for model in (m.PushDevice, m.CoserProfile, m.UserRole, m.SearchHistory, m.WSUserSeq, m.WSAckDedup):
            self.assertEqual(self.db.query(model).filter(model.user_id == 1).count(), 0, model.__name__)
        self.assertEqual(self.db.query(m.Post).count(), 1)
        self.assertEqual(self.db.query(m.Comment).count(), 1)
        self.assertIsNone(self.db.get(m.Comment, 201).parent_id)
        self.assertIsNone(self.db.get(m.Comment, 201).reply_to_user_id)
        self.assertEqual(self.db.get(m.Message, 301).content, "peer message")
        self.assertIsNone(self.db.get(m.Message, 301).quote_preview)
        self.assertIsNone(self.db.get(m.Message, 301).quote_message_id)
        self.assertEqual(self.db.get(m.Message, 302).content, "原内容已删除")
        self.assertIsNone(self.db.get(m.Conversation, 30).user1_id)
        self.assertEqual(self.db.query(m.ConversationParticipant).count(), 1)
        self.assertEqual(self.db.query(m.Notification).one().id, 502)
        self.assertEqual(self.db.query(m.NotificationDelivery).one().user_id, 3)
        self.assertEqual(self.db.query(m.PushLog).one().user_id, 3)
        self.assertEqual(self.db.query(m.WSMessageLog).one().seq, 3)
        self.assertEqual(self.db.query(m.EmailOtp).one().email, "person2@example.test")
        self.assertEqual(self.db.query(m.LoginAttempt).one().identifier, "person2")
        community = self.db.get(c.Community, 10)
        self.assertIsNone(community.owner_id)
        self.assertEqual(community.status, "archived")
        self.assertEqual(community.member_count, 1)
        self.assertEqual(self.db.get(c.Community, 11).owner_id, 2)
        self.assertIsNone(self.db.query(c.CommunityBan).one().banned_by)
        self.assertEqual(self.db.query(c.CommunityBan).one().user_id, 3)
        self.assertIsNone(self.db.get(m.ComicEvent, 20).creator_id)
        self.assertEqual(self.db.get(m.ComicEvent, 20).comment_count, 1)
        self.assertIsNone(self.db.get(m.ComicComment, 251).parent_id)
        self.assertIsNone(self.db.query(m.RoleApplication).one().reviewer_id)
        self.assertIsNone(self.db.query(m.RoleApplication).one().review_comment)
        self.assertIsNone(self.db.query(m.AdminAuditLog).one().admin_user_id)
        self.assertIsNone(self.db.query(m.AdminAuditLog).one().reason)
        self.assertIsNone(self.db.get(d.DeploymentArtifact, "artifact").uploaded_by)
        self.assertEqual(json.loads(self.db.get(d.DeploymentSettings, 1).operator_ids_json), [2])
        ledger = self.db.get(AccountErasure, erasure_id)
        self.assertEqual(ledger.media_status, "pending")
        self.assertEqual(ledger.backup_status, "unverified")
        self.assertEqual(erase_account(self.db, 1, now=NOW), erasure_id)

    def test_unexpected_database_reference_rolls_back_everything(self):
        self.db.execute(text("CREATE TABLE external_reference (id INTEGER PRIMARY KEY, user_id INTEGER, FOREIGN KEY (user_id) REFERENCES users(id))"))
        self.db.execute(text("INSERT INTO external_reference VALUES (1,1)"))
        self.db.commit()
        with self.assertRaises(IntegrityError):
            erase_account(self.db, 1, now=NOW)
            self.db.commit()
        self.db.rollback()
        self.assertIsNotNone(self.db.get(m.User, 1))
        self.assertIsNotNone(self.db.get(m.Post, 100))
        self.assertIsNotNone(self.db.get(m.Message, 300))
        self.assertEqual(self.db.query(AccountErasure).count(), 0)

    def test_legacy_media_is_reviewed_and_foreign_namespaces_are_not_deleted(self):
        with patch("app.utils.FileUploader.cos_key_from_url", side_effect=lambda url: url.split(".test/",1)[1]):
            self.db.get(m.User, 1).avatar_url = "https://bucket.test/avatars/legacy.jpg"
            self.db.get(m.User, 1).cover_photo_url = "https://bucket.test/covers/2/keep.jpg"
            self.db.commit()
            key = erase_account(self.db, 1, now=NOW)
        self.db.commit()
        ledger = self.db.get(AccountErasure, key)
        self.assertEqual(json.loads(ledger.legacy_keys_json), ["avatars/legacy.jpg"])
        process_erasure(self.db, ledger, client=FakeCOS(), bucket="test", now=NOW+timedelta(hours=2))
        self.assertEqual(ledger.media_status, "legacy_review_required")

    def test_cloud_outage_keeps_committed_account_deletion_and_safe_retry(self):
        key = erase_account(self.db, 1, now=NOW)
        self.db.commit()
        ledger = self.db.get(AccountErasure, key)
        client = FakeCOS()
        client.failure = RuntimeError("credential=private-key and private object path")
        process_erasure(self.db, ledger, client=client, bucket="test", now=NOW)
        self.db.commit()
        self.assertIsNone(self.db.get(m.User, 1))
        self.assertEqual(ledger.media_status, "retry")
        self.assertEqual(ledger.last_error_code, "storage_cleanup_failed")
        self.assertGreater(ledger.next_attempt_at, NOW)

    def test_final_cloud_sweep_waits_for_presigned_url_expiry(self):
        key = erase_account(self.db, 1, now=NOW)
        self.db.commit()
        ledger = self.db.get(AccountErasure, key)
        client = FakeCOS()
        process_erasure(self.db, ledger, client=client, bucket="test", now=NOW)
        self.assertEqual(ledger.media_status, "waiting_for_upload_expiry")
        self.assertIsNone(ledger.media_completed_at)
        client.versions = [{"Key":"posts/1/reuploaded.jpg", "VersionId":"later"}]
        process_erasure(self.db, ledger, client=client, bucket="test", now=NOW+timedelta(minutes=66))
        self.assertEqual(ledger.media_status, "complete")
        self.assertEqual(ledger.backup_status, "unverified")
        self.assertEqual(client.versions, [])

    def test_transient_expiry_does_not_break_hourly_rate_limits(self):
        self.db.add_all([
            m.EmailOtp(email="old@example.test", code="100000", purpose="login", expires_at=NOW-timedelta(days=2)),
            m.EmailOtp(email="recent@example.test", code="100001", purpose="login", expires_at=NOW-timedelta(minutes=20)),
            m.LoginAttempt(identifier="expired", ip_address="127.0.0.1", created_at=NOW-timedelta(days=31)),
            m.LoginAttempt(identifier="recent", ip_address="127.0.0.1", created_at=NOW-timedelta(hours=2))])
        self.db.commit()
        counts = prune_transient_records(self.db, now=NOW)
        self.db.commit()
        self.assertEqual(counts["email_otps"], 1)
        self.assertEqual(counts["login_attempts"], 1)
        self.assertIsNotNone(self.db.query(m.EmailOtp).filter_by(email="recent@example.test").first())
        self.assertIsNotNone(self.db.query(m.LoginAttempt).filter_by(identifier="recent").first())

    def test_http_deletion_revokes_old_token_and_postcommit_cache_failure_is_not_a_500(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from jose import jwt
        from app.core import auth_core
        from app.core.config import Config
        from app.database import get_db
        from app.routers import auth
        from app.ws_manager import ws_manager
        factory = sessionmaker(bind=self.engine, autoflush=False)
        app = FastAPI()
        app.include_router(auth.router, prefix="/api/auth")

        def test_db():
            with factory() as session:
                yield session

        app.dependency_overrides[get_db] = test_db
        token = jwt.encode({"sub":"1"}, Config.JWT_SECRET_KEY, algorithm="HS256")
        peer_token = jwt.encode({"sub":"2"}, Config.JWT_SECRET_KEY, algorithm="HS256")
        with patch.object(auth_core, "SessionLocal", factory), \
             patch.object(ws_manager, "schedule_push", side_effect=lambda coroutine: coroutine.close()), \
             patch.object(ws_manager, "clear_social_caches", side_effect=RuntimeError("cache unavailable")), \
             TestClient(app) as client:
            response = client.delete("/api/auth/account", headers={"Authorization":f"Bearer {token}"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["account_deleted"])
            self.assertEqual(response.json()["media_cleanup"], "pending")
            self.assertEqual(client.get("/api/auth/me", headers={"Authorization":f"Bearer {token}"}).status_code, 401)
            self.assertEqual(client.get("/api/auth/me", headers={"Authorization":f"Bearer {peer_token}"}).status_code, 200)

    def test_preserved_ledger_blocks_restored_account_token_and_passive_socket_delivery(self):
        from jose import jwt
        from app.core import auth_core
        from app.core.config import Config
        import app.ws_manager as ws_module
        from app.ws_manager import WSManager
        erase_account(self.db, 1, now=NOW)
        self.db.commit()
        self.db.add(m.User(id=1, username="restored", email="restored@example.test", password_hash="old"))
        self.db.commit()
        factory = sessionmaker(bind=self.engine)
        token = jwt.encode({"sub":"1"}, Config.JWT_SECRET_KEY, algorithm="HS256")
        with patch.object(auth_core, "SessionLocal", factory):
            with self.assertRaises(auth_core.AuthError):
                auth_core.verify_token(token)
        manager = WSManager()
        socket = AsyncMock()
        original_connections = manager._connections
        manager._connections = {1:{"connection":socket}}
        try:
            with patch.object(ws_module, "SessionLocal", factory), patch.object(manager, "disconnect", new_callable=AsyncMock) as disconnect:
                asyncio.run(manager._raw_send_to_connections(1, {"private":"must not be delivered"}))
                socket.send_json.assert_not_awaited()
                disconnect.assert_awaited_once_with(1)
        finally:
            manager._connections = original_connections


class FakeCOS:
    def __init__(self):
        self.versions = [{"Key":"posts/1/a.jpg", "VersionId":"old"}, {"Key":"posts/1/a.jpg", "VersionId":"new"},
                         {"Key":"posts/10/keep.jpg", "VersionId":"peer"}]
        self.markers = [{"Key":"posts/1/a.jpg", "VersionId":"delete-marker"}]
        self.uploads = [{"Key":"posts/1/upload.jpg", "UploadId":"upload"}]
        self.deleted = []
        self.failure = None

    def list_objects_versions(self, **kwargs):
        if self.failure:
            raise self.failure
        prefix = kwargs["Prefix"]
        return {"Version":[x for x in self.versions if x["Key"].startswith(prefix)],
                "DeleteMarker":[x for x in self.markers if x["Key"].startswith(prefix)], "IsTruncated":"false"}

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs)
        self.versions = [x for x in self.versions if (x["Key"], x["VersionId"]) != (kwargs["Key"], kwargs["VersionId"])]
        self.markers = [x for x in self.markers if (x["Key"], x["VersionId"]) != (kwargs["Key"], kwargs["VersionId"])]

    def list_multipart_uploads(self, **kwargs):
        return {"Upload":[x for x in self.uploads if x["Key"].startswith(kwargs["Prefix"])], "IsTruncated":"false"}

    def abort_multipart_upload(self, **kwargs):
        self.uploads = [x for x in self.uploads if x["UploadId"] != kwargs["UploadId"]]


class COSScopeTests(unittest.TestCase):
    def test_versions_delete_markers_and_multipart_are_removed_without_prefix_collision(self):
        client = FakeCOS()
        purge_account_prefix(client, "test", "posts/1/")
        self.assertEqual([x["VersionId"] for x in client.deleted], ["old", "new", "delete-marker"])
        self.assertEqual(client.versions, [{"Key":"posts/10/keep.jpg", "VersionId":"peer"}])
        self.assertEqual(client.markers, [])
        self.assertEqual(client.uploads, [])

    def test_provider_out_of_scope_response_is_not_deleted(self):
        client = FakeCOS()
        client.list_objects_versions = lambda **_: {"Version":[{"Key":"posts/2/keep.jpg","VersionId":"v"}],"IsTruncated":"false"}
        with self.assertRaises(MediaCleanupError):
            purge_account_prefix(client, "test", "posts/1/")
        self.assertEqual(client.deleted, [])

    def test_invalid_provider_pagination_does_not_report_completion(self):
        client = FakeCOS()
        client.list_objects_versions = lambda **_: {"IsTruncated":"true", "NextKeyMarker":"posts/2/key", "NextVersionIdMarker":"v"}
        with self.assertRaises(MediaCleanupError):
            purge_account_prefix(client, "test", "posts/1/")


if __name__ == "__main__":
    unittest.main()
