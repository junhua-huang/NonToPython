import asyncio
from dataclasses import replace
import logging
import re
import threading
import time
from collections.abc import Callable
from typing import Protocol

import regex

from app.database import SessionLocal
from app.models.models import SensitiveWord, SensitiveWordVersion
from app.services.moderation_errors import ModerationUnavailable
from app.services.moderation_types import (
    CompiledRegexRule,
    ModerationRule,
    ModerationSnapshot,
    RiskCategory,
    Severity,
)


logger = logging.getLogger(__name__)

_REGEX_PROBE_TIMEOUT = 0.02
_REGEX_MAX_LENGTH = 256
_REGEX_PROBES = (
    "a" * 2048 + "!",
    "ab" * 1024 + "!",
    "0" * 2048 + "!",
    "词" * 1024 + "!",
)
_BACK_REFERENCE = re.compile(r"\\(?:[1-9]|g<|k<)")
_HIGH_RISK_EXTENSION = re.compile(r"\(\?(?:<[-=!]|P=|R|0|&|\(|[1-9])")


class _RegexGroup:
    __slots__ = ("has_alternation", "has_quantified_node")

    def __init__(self):
        self.has_alternation = False
        self.has_quantified_node = False


def _quantifier_end(expression: str, start: int) -> int | None:
    if start >= len(expression):
        return None
    if expression[start] in "*+?":
        end = start + 1
    elif expression[start] == "{":
        end = start + 1
        lower_start = end
        while end < len(expression) and expression[end].isdigit():
            end += 1
        has_lower = end > lower_start
        if end < len(expression) and expression[end] == ",":
            end += 1
            upper_start = end
            while end < len(expression) and expression[end].isdigit():
                end += 1
            if not has_lower and end == upper_start:
                return None
        elif not has_lower:
            return None
        if end >= len(expression) or expression[end] != "}":
            return None
        end += 1
    else:
        return None
    if end < len(expression) and expression[end] in "?+":
        end += 1
    return end


def _has_unsafe_repeat_structure(expression: str) -> bool:
    groups = [_RegexGroup()]
    index = 0
    while index < len(expression):
        character = expression[index]
        if character == "\\":
            index += 2
            continue
        if character == "[":
            index += 1
            while index < len(expression):
                if expression[index] == "\\":
                    index += 2
                elif expression[index] == "]":
                    index += 1
                    break
                else:
                    index += 1
            continue
        if character == "(":
            if expression.startswith("(?:", index):
                index += 3
            elif index + 1 < len(expression) and expression[index + 1] in "?*":
                return True
            else:
                index += 1
            groups.append(_RegexGroup())
            continue
        if character == ")":
            if len(groups) == 1:
                index += 1
                continue
            closed = groups.pop()
            quantifier_end = _quantifier_end(expression, index + 1)
            if quantifier_end is not None:
                if closed.has_quantified_node or closed.has_alternation:
                    return True
                groups[-1].has_quantified_node = True
                index = quantifier_end
            else:
                groups[-1].has_quantified_node |= closed.has_quantified_node
                groups[-1].has_alternation |= closed.has_alternation
                index += 1
            continue
        if character == "|":
            groups[-1].has_alternation = True
            index += 1
            continue
        quantifier_end = _quantifier_end(expression, index)
        if quantifier_end is not None:
            groups[-1].has_quantified_node = True
            index = quantifier_end
            continue
        index += 1
    return False


class RegexValidationError(ValueError):
    """A rule expression failed bounded validation."""


def validate_safe_regex(pattern: str) -> str:
    if not isinstance(pattern, str):
        raise RegexValidationError("regex validation failed")
    expression = pattern.strip()
    if not expression or len(expression) > _REGEX_MAX_LENGTH:
        raise RegexValidationError("regex length out of range")
    if (
        _has_unsafe_repeat_structure(expression)
        or _BACK_REFERENCE.search(expression)
        or "(?<=" in expression
        or "(?<!" in expression
        or _HIGH_RISK_EXTENSION.search(expression)
    ):
        raise RegexValidationError("unsafe regex structure")
    try:
        compiled = regex.compile(expression)
        for probe in _REGEX_PROBES:
            compiled.search(probe, timeout=_REGEX_PROBE_TIMEOUT)
    except (regex.error, TimeoutError):
        raise RegexValidationError("regex validation failed") from None
    return expression


class _RuleRepository(Protocol):
    def get_version(self) -> int: ...

    def get_active_rules(self) -> list[SensitiveWord]: ...


class SqlAlchemyRuleRepository:
    def get_version(self) -> int:
        with SessionLocal() as db:
            row = db.get(SensitiveWordVersion, 1)
            if row is None:
                raise RuntimeError("moderation rule version unavailable")
            return row.version

    def get_active_rules(self) -> list[SensitiveWord]:
        with SessionLocal() as db:
            rows = (
                db.query(SensitiveWord)
                .filter(SensitiveWord.is_active.is_(True))
                .order_by(SensitiveWord.id)
                .all()
            )
            for row in rows:
                db.expunge(row)
            return rows


class SensitiveWordSnapshotStore:
    def __init__(
        self,
        repository: _RuleRepository,
        max_stale_seconds: float = 120.0,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if max_stale_seconds < 0:
            raise ValueError("max_stale_seconds must not be negative")
        self._repository = repository
        self._max_stale_seconds = max_stale_seconds
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._snapshot: ModerationSnapshot | None = None

    def _read_snapshot(self) -> ModerationSnapshot | None:
        with self._lock:
            return self._snapshot

    def refresh(self, force: bool = False) -> ModerationSnapshot:
        version = self._repository.get_version()
        current = self._read_snapshot()

        if not force and current is not None and current.version == version:
            verified = replace(current, verified_at=self._monotonic())
            with self._lock:
                if self._snapshot is current:
                    self._snapshot = verified
                    return verified
                latest = self._snapshot
            if latest is None:
                raise RuntimeError("moderation snapshot unavailable")
            return latest

        rows = self._repository.get_active_rules()
        literal_rules: list[ModerationRule] = []
        regex_rules: list[CompiledRegexRule] = []
        for row in rows:
            if row.match_type == "literal":
                expression = row.word
            elif row.match_type == "regex":
                expression = validate_safe_regex(row.word)
            else:
                raise ValueError("unsupported moderation rule type")
            if not isinstance(expression, str) or not expression:
                raise ValueError("moderation rule expression unavailable")
            rule = ModerationRule(
                id=row.id,
                expression=expression,
                match_type=row.match_type,
                category=RiskCategory(row.category),
                severity=Severity(row.severity),
                row_version=row.row_version,
            )
            if row.match_type == "literal":
                literal_rules.append(rule)
            else:
                regex_rules.append(
                    CompiledRegexRule(rule=rule, pattern=regex.compile(expression))
                )

        if self._repository.get_version() != version:
            raise RuntimeError("moderation rule version changed during load")
        now = self._monotonic()
        candidate = ModerationSnapshot(
            version=version,
            literal_rules=tuple(literal_rules),
            regex_rules=tuple(regex_rules),
            loaded_at=now,
            verified_at=now,
        )
        with self._lock:
            latest = self._snapshot
            if latest is not None and latest.version > candidate.version:
                raise RuntimeError("moderation rule version regressed")
            self._snapshot = candidate
        return candidate

    def current_snapshot(self) -> ModerationSnapshot:
        snapshot = self._read_snapshot()
        now = self._monotonic()
        if (
            snapshot is None
            or now - snapshot.verified_at > self._max_stale_seconds
        ):
            raise ModerationUnavailable(
                RuntimeError("moderation snapshot absent or stale")
            )
        return snapshot


async def poll_snapshots(
    store: SensitiveWordSnapshotStore,
    stop: asyncio.Event,
    interval: float = 30.0,
) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(store.refresh)
        except Exception:
            logger.error("moderation_snapshot_refresh_failed")
        if stop.is_set():
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


repository = SqlAlchemyRuleRepository()
snapshot_store = SensitiveWordSnapshotStore(repository)
