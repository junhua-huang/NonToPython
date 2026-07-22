import ast
import logging
from pathlib import Path

from app.services.content_filter import ContentFilter, content_filter
from app.services.content_moderation import ContentModeration


ROOT = Path(__file__).resolve().parents[1]
SENTINELS = (
    "PRIVATE_BODY_8392",
    "MATCH_4481",
    "PRIVATE_REGEX_(a+)+$",
    "mysql://private-user:private-password@private-host/db",
)


def assert_sentinels_absent(text: str):
    for sentinel in SENTINELS:
        assert sentinel not in text


def test_legacy_rejection_logs_only_metadata(caplog):
    with caplog.at_level(logging.WARNING):
        content_filter.log_block(9, SENTINELS[0], [SENTINELS[1], SENTINELS[2]])
        ContentModeration.log_moderation(
            10,
            "comment",
            SENTINELS[0],
            [SENTINELS[1], SENTINELS[3]],
        )

    text = caplog.text
    assert_sentinels_absent(text)
    assert "user_id=9" in text
    assert "user_id=10" in text
    assert "content_type=legacy_filter" in text
    assert "content_type=comment" in text
    assert text.count("rule_count=2") == 2
    assert all(record.exc_info is None for record in caplog.records)


def test_content_check_does_not_log_matched_terms_or_regex(caplog):
    expression = "PRIVATE_DYNAMIC_MATCH_7751"
    filter_instance = ContentFilter()
    filter_instance.add_word(expression)

    with caplog.at_level(logging.DEBUG):
        result = ContentModeration.check_content(expression)

    assert result["approved"] is False
    assert expression not in caplog.text
    assert "sensitive words found" not in caplog.text


def test_dynamic_legacy_rule_crud_logs_action_and_count_without_expression(caplog):
    expression = SENTINELS[2]
    filter_instance = ContentFilter()

    with caplog.at_level(logging.INFO):
        assert filter_instance.add_word(expression)
        assert filter_instance.remove_word(expression)

    assert_sentinels_absent(caplog.text)
    assert "action=add" in caplog.text
    assert "action=remove" in caplog.text
    assert "total=" in caplog.text


def logger_calls(path: Path):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if (
            isinstance(owner, ast.Name)
            and owner.id == "logger"
            and node.func.attr in {"debug", "info", "warning", "error", "exception"}
        ):
            yield ast.get_source_segment(source, node) or ""


def test_moderation_logger_calls_are_statically_metadata_only():
    paths = [
        ROOT / "app" / "services" / "content_filter.py",
        ROOT / "app" / "services" / "content_moderation.py",
    ]
    forbidden = {
        "original_text",
        "filtered_text",
        "text_preview",
        "hit_words",
        "reasons",
        "word,",
        "expression",
        "pattern",
        "regex",
        "exc_info",
        "logger.exception",
    }

    calls = [call for path in paths for call in logger_calls(path)]
    assert calls
    for call in calls:
        lowered = call.lower()
        assert not any(token in lowered for token in forbidden), call


def test_admin_rule_crud_never_logs_rule_expression_or_exception_text():
    path = ROOT / "app" / "routers" / "admin.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    moderation_functions = {
        "get_sensitive_words",
        "add_sensitive_word",
        "patch_sensitive_word",
        "delete_sensitive_word",
    }

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in moderation_functions:
            continue
        function_source = ast.get_source_segment(source, node) or ""
        assert "str(" not in function_source
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                assert not (
                    isinstance(child.func.value, ast.Name)
                    and child.func.value.id == "logger"
                    and (
                        any(
                            token in (ast.get_source_segment(source, child) or "").lower()
                            for token in ("word", "expression", "regex", "exception")
                        )
                        or child.func.attr == "exception"
                    )
                )


def test_websocket_logging_is_metadata_only():
    paths = [
        ROOT / "app" / "routers" / "ws.py",
        ROOT / "app" / "ws_manager.py",
    ]
    forbidden = {
        "headers=",
        "authorization",
        "cookie",
        "access_token",
        "exc_info",
        "logger.exception",
        "{e}",
        "str(e)",
    }

    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "print(" not in source
        for call in logger_calls(path):
            lowered = call.lower()
            assert not any(token in lowered for token in forbidden), call
