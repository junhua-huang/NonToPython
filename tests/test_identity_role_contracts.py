import inspect
import unittest
from unittest.mock import patch

from app.models import models
from app.models.models import Post, Role, RoleApplication, User, UserRole
from app.routers import admin as admin_router
from app.routers import posts as posts_router
from app.routers import roles as roles_router
import app.dependencies as dependencies


class _FakeWsManager:
    def is_connected(self, _user_id):
        return False


class IdentityRoleContractTests(unittest.TestCase):
    def test_business_identity_registry_matches_product_scope(self):
        expected = {
            "event_organizer": "活动方",
            "coser": "Coser",
            "photographer": "摄影师",
            "wig_stylist": "毛娘",
            "makeup_artist": "妆娘",
            "ticket_agent": "票代",
            "prop_maker": "道具师",
            "costume_maker": "服装师",
            "retoucher": "后期师",
        }

        self.assertEqual(models.BUSINESS_IDENTITY_ROLES, expected)
        self.assertNotIn("admin", models.BUSINESS_IDENTITY_ROLES)
        self.assertIn("admin", models.SYSTEM_ROLE_NAMES)

    def test_user_public_serialization_hides_system_roles_and_exposes_verified_identities(self):
        user = User(id=1, username="alice", email="alice@example.com")
        user.user_roles = [
            UserRole(role=Role(name="admin", label="管理员")),
            UserRole(role=Role(name="coser", label="Coser")),
            UserRole(role=Role(name="photographer", label="摄影师")),
        ]

        with patch.object(models, "_get_ws_manager", return_value=_FakeWsManager()):
            payload = user.to_dict()

        self.assertEqual(payload["verified_roles"], ["coser", "photographer"])
        self.assertEqual(payload["verified_role_labels"], ["Coser", "摄影师"])
        self.assertEqual(payload["roles"], ["coser", "photographer"])
        self.assertEqual(payload["role_labels"], ["Coser", "摄影师"])
        self.assertNotIn("admin", payload["roles"])
        self.assertNotIn("普通用户", payload.get("role_labels", []))

    def test_role_application_unified_template_fields_are_serialized(self):
        columns = set(RoleApplication.__table__.columns.keys())
        for column in [
            "application_text",
            "proof_images",
            "portfolio_links",
            "contact_info",
            "extra_note",
        ]:
            self.assertIn(column, columns)

        application = RoleApplication(
            id=7,
            user_id=1,
            role_id=2,
            status="pending",
            reason="legacy reason",
            application_text="统一申请说明",
            proof_images='["https://example.com/a.jpg"]',
            portfolio_links='["https://example.com/work"]',
            contact_info="wechat:alice",
            extra_note="备注",
        )

        payload = application.to_dict()
        self.assertEqual(payload["application_text"], "统一申请说明")
        self.assertEqual(payload["proof_images"], ["https://example.com/a.jpg"])
        self.assertEqual(payload["portfolio_links"], ["https://example.com/work"])
        self.assertEqual(payload["contact_info"], "wechat:alice")
        self.assertEqual(payload["extra_note"], "备注")
        self.assertEqual(payload["status"], "pending")

    def test_post_identity_fields_are_nullable_and_serialized(self):
        columns = Post.__table__.columns
        self.assertIn("content_category", columns)
        self.assertIn("display_role_type", columns)
        self.assertTrue(columns["content_category"].nullable)
        self.assertTrue(columns["display_role_type"].nullable)

        post = Post(
            id=9,
            content="作品内容",
            user_id=1,
            content_category="cosplay",
            display_role_type="coser",
        )
        payload = post.to_dict(like_count=0, comment_count=0, topics=[], is_liked=False)
        self.assertEqual(payload["content_category"], "cosplay")
        self.assertEqual(payload["display_role_type"], "coser")
        self.assertEqual(payload["display_role_label"], "Coser")

    def test_post_display_role_validation_never_blocks_publish_for_unverified_role(self):
        source = inspect.getsource(posts_router.resolve_display_role_type)

        self.assertIn('return role_name if row else None', source)
        self.assertIn('role_name not in BUSINESS_IDENTITY_ROLES', source)
        self.assertIn('Role.name == role_name', source)

    def test_role_review_uses_verified_status_not_approved(self):
        roles_source = inspect.getsource(roles_router.approve_application)
        admin_source = inspect.getsource(admin_router.admin_approve_application)

        self.assertIn('app.status = "verified"', roles_source)
        self.assertIn('app.status = "verified"', admin_source)
        self.assertNotIn('app.status = "approved"', roles_source)
        self.assertNotIn('app.status = "approved"', admin_source)

    def test_roles_router_accepts_unified_application_payload(self):
        source = inspect.getsource(roles_router.RoleApplyRequest)
        self.assertIn("application_text", source)
        self.assertIn("proof_images", source)
        self.assertIn("portfolio_links", source)
        self.assertIn("contact_info", source)
        self.assertIn("extra_note", source)

    def test_roles_router_limits_identity_proof_images_to_nine(self):
        source = inspect.getsource(roles_router.RoleApplyRequest)
        self.assertIn("max_length=9", source)
        self.assertIn("proof_images", source)

    def test_role_application_notifies_admin_email_in_background(self):
        source = inspect.getsource(roles_router.apply_role)
        self.assertIn("BackgroundTasks", source)
        self.assertIn("background_tasks.add_task", source)
        self.assertIn("2531830689@qq.com", inspect.getsource(roles_router))
        self.assertIn("send_email", inspect.getsource(roles_router))

    def test_create_post_accepts_identity_form_fields(self):
        source = inspect.getsource(posts_router.create_post)
        self.assertIn('content_category: Optional[str] = Form(None)', source)
        self.assertIn('display_role_type: Optional[str] = Form(None)', source)
        self.assertIn('resolve_display_role_type(user, display_role_type, db)', source)

    def test_display_role_resolution_queries_db_instead_of_detached_user_relationships(self):
        source = inspect.getsource(posts_router.resolve_display_role_type)
        self.assertIn('db: Session', source)
        self.assertIn('db.query(UserRole)', source)
        self.assertNotIn('user.get_verified_identity_roles()', source)

    def test_post_serialization_suppresses_suspended_or_unverified_display_role(self):
        author = User(id=1, username="alice", email="alice@example.com")
        author.user_roles = []
        post = Post(id=9, content="作品内容", user_id=1, author=author, display_role_type="coser")

        payload = post.to_dict(like_count=0, comment_count=0, topics=[], is_liked=False)

        self.assertIsNone(payload["display_role_type"])
        self.assertIsNone(payload["display_role_label"])

    def test_require_role_does_not_trust_stale_jwt_roles(self):
        source = inspect.getsource(dependencies.require_role)
        self.assertNotIn('if token_roles:', source)
        self.assertIn('db.query(UserRole)', source)

    def test_admin_direct_assignment_does_not_bypass_identity_review(self):
        source = inspect.getsource(admin_router.assign_role_to_user)
        self.assertIn('BUSINESS_IDENTITY_ROLES', source)
        self.assertIn('Only system roles can be assigned directly', source)

    def test_service_profile_requires_matching_verified_service_identity(self):
        source = inspect.getsource(roles_router.update_service_profile)
        self.assertIn('Role.name == service_type', source)
        self.assertIn('Required verified service identity', source)


if __name__ == "__main__":
    unittest.main()
