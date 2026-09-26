import ast
from pathlib import Path

from app.services.moderation_inventory import MODERATED_TEXT_FIELDS


ROUTERS = Path(__file__).parents[1] / "app" / "routers"
BUSINESS_ROUTER_FILES = [
    "auth.py",
    "posts.py",
    "interactions.py",
    "chat.py",
    "ws.py",
    "communities.py",
    "comic.py",
    "roles.py",
    "topics.py",
    "reports.py",
]


def test_business_routers_do_not_import_legacy_filters():
    source = "\n".join((ROUTERS / name).read_text(encoding="utf-8") for name in BUSINESS_ROUTER_FILES)
    assert "content_moderation import ContentModeration" not in source
    assert "content_filter import" not in source


def test_inventory_has_no_duplicate_or_empty_field_sets():
    source = (Path(__file__).parents[1] / "app" / "services" / "moderation_inventory.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    route_keys = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign) or getattr(node.target, "id", None) != "MODERATED_TEXT_FIELDS":
            continue
        route_keys = [key.value for key in node.value.keys if isinstance(key, ast.Constant)]
        break

    assert len(route_keys) == len(set(route_keys))
    assert all(fields and len(fields) == len(set(fields)) for fields in MODERATED_TEXT_FIELDS.values())


ROUTE_REJECTION_TESTS = {
    "POST /api/auth/register": ("test_moderation_profile_role_writes.py", "test_registration_uses_user_registration_target_before_otp_and_user_creation"),
    "PUT /api/auth/profile": ("test_moderation_profile_role_writes.py", "test_profile_update_uses_user_profile_target_before_db_query"),
    "POST /api/posts": ("test_moderation_route_integration.py", "test_create_post_moderates_before_file_upload_and_db_mutation"),
    "PUT /api/posts/{post_id}": ("test_moderation_route_integration.py", "test_update_post_moderates_before_db_mutation"),
    "POST /api/posts/{post_id}/comments": ("test_moderation_route_integration.py", "test_create_comment_moderates_before_comment_insert"),
    "PUT /api/comments/{comment_id}": ("test_moderation_route_integration.py", "test_update_comment_moderates_before_comment_mutation"),
    "POST /api/chat/conversations/{conversation_id}/messages": ("test_moderation_route_integration.py", "test_private_chat_message_moderates_before_persist_and_fanout"),
    "WS send_message": ("test_moderation_messages.py", "test_ws_rejection_is_failed_ack_and_has_no_dedup_side_effect"),
    "POST /api/communities": ("test_moderation_community_writes.py", "test_community_text_writes_use_targeted_moderation_before_mutation"),
    "PATCH /api/communities/{community_id}": ("test_moderation_community_writes.py", "test_community_text_writes_use_targeted_moderation_before_mutation"),
    "POST /api/communities/{community_id}/join": ("test_moderation_community_writes.py", "test_community_text_writes_use_targeted_moderation_before_mutation"),
    "POST /api/communities/{community_id}/announcements": ("test_moderation_community_writes.py", "test_community_text_writes_use_targeted_moderation_before_mutation"),
    "PATCH /api/communities/{community_id}/announcements/{announcement_id}": ("test_moderation_community_writes.py", "test_community_text_writes_use_targeted_moderation_before_mutation"),
    "POST /api/communities/{community_id}/bans": ("test_moderation_community_writes.py", "test_community_text_writes_use_targeted_moderation_before_mutation"),
    "POST /api/communities/{community_id}/chat/messages": ("test_moderation_messages.py", "test_http_community_chat_rejection_has_no_persistence_or_fanout"),
    "POST /api/comic/events": ("test_moderation_comic_writes.py", "test_comic_text_writes_use_targeted_moderation_before_mutation"),
    "PUT /api/comic/events/{event_id}": ("test_moderation_comic_writes.py", "test_comic_text_writes_use_targeted_moderation_before_mutation"),
    "POST /api/comic/events/{event_id}/comments": ("test_moderation_comic_writes.py", "test_comic_text_writes_use_targeted_moderation_before_mutation"),
    "POST /api/roles/apply": ("test_moderation_profile_role_writes.py", "test_role_application_and_profiles_moderate_before_write"),
    "PUT /api/roles/profiles/coser": ("test_moderation_profile_role_writes.py", "test_role_application_and_profiles_moderate_before_write"),
    "PUT /api/roles/profiles/photographer": ("test_moderation_profile_role_writes.py", "test_role_application_and_profiles_moderate_before_write"),
    "PUT /api/roles/profiles/service": ("test_moderation_profile_role_writes.py", "test_role_application_and_profiles_moderate_before_write"),
    "POST /api/roles/applications/{application_id}/approve": ("test_moderation_profile_role_writes.py", "test_role_reviews_moderate_review_comment_before_status_mutation"),
    "POST /api/roles/applications/{application_id}/reject": ("test_moderation_profile_role_writes.py", "test_role_reviews_moderate_review_comment_before_status_mutation"),
    "POST /api/roles/applications/{application_id}/suspend": ("test_moderation_profile_role_writes.py", "test_role_reviews_moderate_review_comment_before_status_mutation"),
    "POST /role-applications/{application_id}/approve": ("test_moderation_profile_role_writes.py", "test_legacy_admin_role_reviews_moderate_before_status_mutation"),
    "POST /role-applications/{application_id}/reject": ("test_moderation_profile_role_writes.py", "test_legacy_admin_role_reviews_moderate_before_status_mutation"),
    "POST /role-applications/{application_id}/suspend": ("test_moderation_profile_role_writes.py", "test_legacy_admin_role_reviews_moderate_before_status_mutation"),
    "POST /api/topics": ("test_moderation_governance_writes.py", "test_topic_text_rejects_before_mutation"),
    "PUT /api/topics/{topic_id}": ("test_moderation_governance_writes.py", "test_topic_text_rejects_before_mutation"),
    "POST /api/reports": ("test_moderation_governance_writes.py", "test_report_reason_rejects_before_lookup_or_write"),
    "POST /api/reports/post": ("test_moderation_governance_writes.py", "test_report_reason_rejects_before_lookup_or_write"),
    "POST /api/reports/comment": ("test_moderation_governance_writes.py", "test_report_reason_rejects_before_lookup_or_write"),
    "POST /api/reports/user": ("test_moderation_governance_writes.py", "test_report_reason_rejects_before_lookup_or_write"),
}


def _function_source(path: Path, function_name: str) -> str:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue
        start = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list])
        return "\n".join(lines[start - 1:node.end_lineno])
    raise AssertionError(f"missing rejection test function: {path.name}::{function_name}")


def test_each_inventory_route_has_a_rejection_test_marker():
    assert sorted(set(MODERATED_TEXT_FIELDS) - set(ROUTE_REJECTION_TESTS)) == []
    assert sorted(set(ROUTE_REJECTION_TESTS) - set(MODERATED_TEXT_FIELDS)) == []

    for route_key, (file_name, function_name) in ROUTE_REJECTION_TESTS.items():
        source = _function_source(Path(__file__).parent / file_name, function_name)
        assert route_key in source
        assert (
            "pytest.raises(HTTPException)" in source
            or "CONTENT_REJECTED" in source
            or "ContentRejected" in source
        )
