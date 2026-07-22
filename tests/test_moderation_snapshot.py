import asyncio
from dataclasses import FrozenInstanceError
import logging
from types import SimpleNamespace
import threading

import pytest

import app.services.moderation_snapshot as snapshot_module
from app.services.moderation_errors import ModerationUnavailable
from app.services.moderation_snapshot import (
    RegexValidationError,
    SensitiveWordSnapshotStore,
    SqlAlchemyRuleRepository,
    poll_snapshots,
    validate_safe_regex,
)


class ManualClock:
    def __init__(self, value: float = 100.0):
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeRuleRepository:
    def __init__(self):
        self.version = 1
        self.rows = [self.literal_row()]
        self.version_reads = 0
        self.rule_reads = 0

    @staticmethod
    def literal_row(
        word="blocked",
        *,
        rule_id=1,
        category="abuse",
        severity="high",
        row_version=1,
    ):
        return SimpleNamespace(
            id=rule_id,
            word=word,
            match_type="literal",
            category=category,
            severity=severity,
            row_version=row_version,
        )

    @staticmethod
    def regex_row(
        word=r"wx[0-9]{5,12}",
        *,
        rule_id=2,
        category="spam",
        severity="medium",
        row_version=1,
    ):
        return SimpleNamespace(
            id=rule_id,
            word=word,
            match_type="regex",
            category=category,
            severity=severity,
            row_version=row_version,
        )

    def get_version(self):
        self.version_reads += 1
        return self.version

    def get_active_rules(self):
        self.rule_reads += 1
        return list(self.rows)


@pytest.fixture
def fake_rule_repository():
    return FakeRuleRepository()


@pytest.mark.parametrize(
    "pattern",
    [
        r"(a+)+$",
        r"(a|aa)+$",
        r"(a*)*$",
        r"(.)\1+",
        r"(a?)+$",
        r"((a+))+$",
    ],
)
def test_unsafe_regex_is_rejected_with_non_sensitive_reason(pattern):
    with pytest.raises(RegexValidationError) as caught:
        validate_safe_regex(pattern)

    assert pattern not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("pattern", [r"(?<=private)word", r"(?<!private)word"])
def test_lookbehind_is_rejected(pattern):
    with pytest.raises(RegexValidationError, match="^unsafe regex structure$"):
        validate_safe_regex(pattern)


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        (r"wx[0-9]{5,12}", r"wx[0-9]{5,12}"),
        (r"  wx[0-9]{5,12}  ", r"wx[0-9]{5,12}"),
        (r"(?:红包|轉帳)[\p{N}]{2,4}", r"(?:红包|轉帳)[\p{N}]{2,4}"),
    ],
)
def test_valid_ascii_and_unicode_regex_is_accepted_and_stripped(pattern, expected):
    assert validate_safe_regex(pattern) == expected


@pytest.mark.parametrize("pattern", ["", "   ", "x" * 257])
def test_regex_length_is_bounded_without_echoing_input(pattern):
    with pytest.raises(RegexValidationError, match="^regex length out of range$") as caught:
        validate_safe_regex(pattern)

    if pattern:
        assert pattern not in str(caught.value)


def test_compile_error_is_wrapped_without_pattern_or_exception_details():
    pattern = "[private-sentinel"

    with pytest.raises(RegexValidationError, match="^regex validation failed$") as caught:
        validate_safe_regex(pattern)

    assert pattern not in str(caught.value)
    assert "unterminated" not in str(caught.value).lower()
    assert caught.value.__cause__ is None


def test_probe_timeout_is_wrapped_and_each_probe_has_twenty_ms_budget(monkeypatch):
    timeouts = []

    class TimingOutRegex:
        def search(self, text, timeout):
            timeouts.append(timeout)
            raise TimeoutError("private-timeout-sentinel")

    monkeypatch.setattr(snapshot_module.regex, "compile", lambda expression: TimingOutRegex())

    with pytest.raises(RegexValidationError, match="^regex validation failed$") as caught:
        validate_safe_regex("safe-looking")

    assert timeouts == [0.02]
    assert "private-timeout-sentinel" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_validation_runs_multiple_bounded_probes(monkeypatch):
    probes = []

    class RecordingRegex:
        def search(self, text, timeout):
            probes.append((text, timeout))
            return None

    monkeypatch.setattr(snapshot_module.regex, "compile", lambda expression: RecordingRegex())

    assert validate_safe_regex("safe-looking") == "safe-looking"
    assert len(probes) >= 3
    assert all(len(text) <= 4096 and timeout == 0.02 for text, timeout in probes)


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filtered = False
        self.ordered = False

    def filter(self, clause):
        self.filtered = True
        return self

    def order_by(self, column):
        self.ordered = True
        return self

    def all(self):
        assert self.filtered
        assert self.ordered
        return sorted(self.rows, key=lambda row: row.id)


class FakeSession:
    def __init__(self, *, version_row=None, rows=()):
        self.version_row = version_row
        self.query_object = FakeQuery(list(rows))
        self.expunge_calls = []
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.exited = True

    def get(self, model, key):
        assert key == 1
        return self.version_row

    def query(self, model):
        return self.query_object

    def expunge(self, row):
        self.expunge_calls.append(row)


def test_sqlalchemy_repository_uses_independent_sessions_and_detaches_sorted_rows(
    monkeypatch,
):
    rows = [FakeRuleRepository.literal_row(rule_id=9), FakeRuleRepository.literal_row(rule_id=2)]
    sessions = [
        FakeSession(version_row=SimpleNamespace(version=17)),
        FakeSession(rows=rows),
    ]
    created = []

    def session_factory():
        session = sessions[len(created)]
        created.append(session)
        return session

    monkeypatch.setattr(snapshot_module, "SessionLocal", session_factory)
    repository = SqlAlchemyRuleRepository()

    assert repository.get_version() == 17
    detached = repository.get_active_rules()

    assert [row.id for row in detached] == [2, 9]
    assert len(created) == 2
    assert created[0] is not created[1]
    assert all(session.entered and session.exited for session in created)
    assert created[1].expunge_calls == detached


def test_sqlalchemy_repository_missing_version_singleton_fails(monkeypatch):
    session = FakeSession(version_row=None)
    monkeypatch.setattr(snapshot_module, "SessionLocal", lambda: session)

    with pytest.raises(RuntimeError, match="^moderation rule version unavailable$"):
        SqlAlchemyRuleRepository().get_version()

    assert session.exited


def test_refresh_builds_before_atomic_swap(fake_rule_repository):
    clock = ManualClock()
    store = SensitiveWordSnapshotStore(
        fake_rule_repository, max_stale_seconds=120, monotonic=clock
    )
    first = store.refresh(force=True)
    fake_rule_repository.version = 2
    fake_rule_repository.rows = [fake_rule_repository.regex_row(r"(a+)+$")]

    with pytest.raises(RegexValidationError):
        store.refresh()

    assert store.current_snapshot() is first
    assert store.current_snapshot().version == 1


def test_missing_or_stale_snapshot_fails_closed_without_sleep(fake_rule_repository):
    clock = ManualClock()
    store = SensitiveWordSnapshotStore(
        fake_rule_repository, max_stale_seconds=5, monotonic=clock
    )

    with pytest.raises(ModerationUnavailable):
        store.current_snapshot()

    store.refresh(force=True)
    clock.value += 5.001

    with pytest.raises(ModerationUnavailable):
        store.current_snapshot()


def test_same_version_refresh_replaces_snapshot_and_only_advances_verification(
    fake_rule_repository,
):
    clock = ManualClock()
    store = SensitiveWordSnapshotStore(fake_rule_repository, monotonic=clock)
    first = store.refresh(force=True)
    clock.value = 110.0

    verified = store.refresh()

    assert verified is store.current_snapshot()
    assert verified is not first
    assert verified.version == first.version
    assert verified.literal_rules is first.literal_rules
    assert verified.regex_rules is first.regex_rules
    assert verified.loaded_at == first.loaded_at
    assert verified.verified_at == 110.0
    assert fake_rule_repository.rule_reads == 1


def test_force_refresh_reloads_same_version_and_creates_new_snapshot(fake_rule_repository):
    clock = ManualClock()
    store = SensitiveWordSnapshotStore(fake_rule_repository, monotonic=clock)
    first = store.refresh(force=True)
    fake_rule_repository.rows = [fake_rule_repository.literal_row("replacement")]
    clock.value = 120.0

    forced = store.refresh(force=True)

    assert forced is not first
    assert forced.loaded_at == forced.verified_at == 120.0
    assert forced.literal_rules[0].expression == "replacement"
    assert fake_rule_repository.rule_reads == 2


def test_version_race_rejects_candidate_and_preserves_previous_snapshot(
    fake_rule_repository,
):
    store = SensitiveWordSnapshotStore(fake_rule_repository, monotonic=ManualClock())
    first = store.refresh(force=True)
    fake_rule_repository.version = 2
    fake_rule_repository.rows = [fake_rule_repository.literal_row("candidate")]
    original_get_active_rules = fake_rule_repository.get_active_rules

    def racing_rows():
        rows = original_get_active_rules()
        fake_rule_repository.version = 3
        return rows

    fake_rule_repository.get_active_rules = racing_rows

    with pytest.raises(RuntimeError, match="^moderation rule version changed during load$"):
        store.refresh()

    assert store.current_snapshot() is first


def test_refresh_never_rolls_snapshot_back_to_an_older_version(fake_rule_repository):
    store = SensitiveWordSnapshotStore(fake_rule_repository, monotonic=ManualClock())
    store.refresh(force=True)
    fake_rule_repository.version = 2
    current = store.refresh(force=True)
    fake_rule_repository.version = 1
    fake_rule_repository.rows = [fake_rule_repository.literal_row("older")]

    with pytest.raises(RuntimeError, match="^moderation rule version regressed$"):
        store.refresh(force=True)

    assert store.current_snapshot() is current
    assert store.current_snapshot().version == 2


def test_version_change_during_candidate_build_is_rejected(
    fake_rule_repository, monkeypatch
):
    store = SensitiveWordSnapshotStore(fake_rule_repository, monotonic=ManualClock())
    first = store.refresh(force=True)
    fake_rule_repository.version = 2
    fake_rule_repository.rows = [fake_rule_repository.regex_row()]
    original_validate = snapshot_module.validate_safe_regex

    def racing_validation(pattern):
        expression = original_validate(pattern)
        fake_rule_repository.version = 3
        return expression

    monkeypatch.setattr(snapshot_module, "validate_safe_regex", racing_validation)

    with pytest.raises(RuntimeError, match="^moderation rule version changed during load$"):
        store.refresh(force=True)

    assert store.current_snapshot() is first


def test_concurrent_older_candidate_cannot_overwrite_newer_snapshot():
    clock = ManualClock()
    old_rows_loaded = threading.Event()
    allow_old_verify = threading.Event()

    class RacingRepository:
        def get_version(self):
            if threading.current_thread().name == "old-refresh":
                if old_rows_loaded.is_set():
                    allow_old_verify.wait(timeout=2)
                return 2
            return 3

        def get_active_rules(self):
            if threading.current_thread().name == "old-refresh":
                old_rows_loaded.set()
                return [FakeRuleRepository.literal_row("version-two")]
            return [FakeRuleRepository.literal_row("version-three")]

    repository = RacingRepository()
    bootstrap = FakeRuleRepository()
    store = SensitiveWordSnapshotStore(bootstrap, monotonic=clock)
    store.refresh(force=True)
    store._repository = repository
    outcome = []

    def load_old():
        try:
            outcome.append(store.refresh(force=True))
        except RuntimeError:
            outcome.append(None)

    old_thread = threading.Thread(target=load_old, name="old-refresh")
    old_thread.start()
    assert old_rows_loaded.wait(timeout=2)

    newer = store.refresh(force=True)
    allow_old_verify.set()
    old_thread.join(timeout=2)

    assert not old_thread.is_alive()
    assert store.current_snapshot() is newer
    assert newer.version == 3
    assert newer.literal_rules[0].expression == "version-three"


def test_same_version_verification_cannot_overwrite_concurrently_loaded_version(
    fake_rule_repository,
):
    clock_entered = threading.Event()
    allow_clock = threading.Event()

    class BlockingClock:
        def __init__(self):
            self.block = False
            self.value = 100.0

        def __call__(self):
            if self.block and threading.current_thread().name == "verification-refresh":
                clock_entered.set()
                allow_clock.wait(timeout=2)
            return self.value

    clock = BlockingClock()
    store = SensitiveWordSnapshotStore(fake_rule_repository, monotonic=clock)
    first = store.refresh(force=True)
    clock.block = True
    outcome = []

    thread = threading.Thread(
        target=lambda: outcome.append(store.refresh()), name="verification-refresh"
    )
    thread.start()
    assert clock_entered.wait(timeout=2)

    fake_rule_repository.version = 2
    clock.block = False
    newer = store.refresh(force=True)
    allow_clock.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert first.version == 1
    assert store.current_snapshot() is newer
    assert newer.version == 2


def test_snapshot_and_rules_are_immutable_and_regex_uses_third_party_engine(
    fake_rule_repository,
):
    fake_rule_repository.rows = [fake_rule_repository.regex_row()]
    snapshot = SensitiveWordSnapshotStore(
        fake_rule_repository, monotonic=ManualClock()
    ).refresh(force=True)

    with pytest.raises(FrozenInstanceError):
        snapshot.version = 2
    with pytest.raises(FrozenInstanceError):
        snapshot.regex_rules[0].rule.expression = "changed"
    assert snapshot.regex_rules[0].pattern.__class__.__module__ == "_regex"


def test_poll_uses_to_thread_and_logs_metadata_only_on_failure(monkeypatch, caplog):
    async def exercise():
        sentinel = "private-expression mysql://private-dsn"
        stop = asyncio.Event()
        to_thread_calls = []

        class FailingStore:
            def refresh(self):
                stop.set()
                raise RuntimeError(sentinel)

        async def fake_to_thread(function, *args):
            to_thread_calls.append((function, args))
            return function(*args)

        monkeypatch.setattr(snapshot_module.asyncio, "to_thread", fake_to_thread)

        with caplog.at_level(logging.ERROR, logger=snapshot_module.__name__):
            await poll_snapshots(FailingStore(), stop, interval=0)

        assert len(to_thread_calls) == 1
        assert "moderation_snapshot_refresh_failed" in caplog.text
        assert sentinel not in caplog.text
        assert all(record.exc_info is None for record in caplog.records)

    asyncio.run(exercise())


def test_poll_honors_pre_set_stop_without_refreshing():
    async def exercise():
        stop = asyncio.Event()
        stop.set()

        class Store:
            def refresh(self):
                raise AssertionError("refresh must not run")

        await poll_snapshots(Store(), stop, interval=0)

    asyncio.run(exercise())


def test_poll_continues_after_asyncio_timeout_error(monkeypatch):
    async def exercise():
        stop = asyncio.Event()
        refreshes = []
        waits = []

        class Store:
            def refresh(self):
                refreshes.append(True)
                if len(refreshes) == 2:
                    stop.set()

        async def direct_to_thread(function, *args):
            return function(*args)

        async def fake_wait_for(awaitable, timeout):
            waits.append(timeout)
            awaitable.close()
            raise asyncio.TimeoutError

        monkeypatch.setattr(snapshot_module.asyncio, "to_thread", direct_to_thread)
        monkeypatch.setattr(snapshot_module.asyncio, "wait_for", fake_wait_for)

        await poll_snapshots(Store(), stop, interval=7.5)

        assert len(refreshes) == 2
        assert waits == [7.5]

    asyncio.run(exercise())


def test_main_lifespan_merges_init_refresh_poll_and_shutdown_without_secrets(
    monkeypatch, caplog
):
    async def exercise():
        import app.main as main_module

        calls = []
        poll_started = asyncio.Event()
        poll_finished = asyncio.Event()
        sentinel = "private-rule mysql://private-dsn"

        def fake_init_db():
            calls.append("init_db")

        def failed_initial_refresh(force=False):
            calls.append(("refresh", force))
            raise RuntimeError(sentinel)

        async def fake_poll(store, stop, interval=30.0):
            calls.append(("poll", store))
            poll_started.set()
            await stop.wait()
            poll_finished.set()

        monkeypatch.setattr(main_module, "init_db", fake_init_db)
        monkeypatch.setattr(main_module.snapshot_store, "refresh", failed_initial_refresh)
        monkeypatch.setattr(main_module, "poll_snapshots", fake_poll)

        with caplog.at_level(logging.ERROR, logger=main_module.__name__):
            async with main_module.lifespan(main_module.app):
                await asyncio.wait_for(poll_started.wait(), timeout=1)
                assert not poll_finished.is_set()

        assert calls[0] == "init_db"
        assert calls[1] == ("refresh", True)
        assert calls[2] == ("poll", main_module.snapshot_store)
        assert poll_finished.is_set()
        assert "moderation_snapshot_initial_load_failed" in caplog.text
        assert sentinel not in caplog.text
        assert all(record.exc_info is None for record in caplog.records)

    asyncio.run(exercise())


def test_main_shutdown_cancels_poller_that_does_not_honor_stop(monkeypatch):
    async def exercise():
        import app.main as main_module

        poll_started = asyncio.Event()
        poll_cancelled = asyncio.Event()

        monkeypatch.setattr(main_module, "init_db", lambda: None)
        monkeypatch.setattr(main_module.snapshot_store, "refresh", lambda force=False: None)

        async def stuck_poll(store, stop, interval=30.0):
            poll_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                poll_cancelled.set()

        monkeypatch.setattr(main_module, "poll_snapshots", stuck_poll)

        async def enter_and_exit():
            async with main_module.lifespan(main_module.app):
                await poll_started.wait()

        await asyncio.wait_for(enter_and_exit(), timeout=0.25)
        assert poll_cancelled.is_set()

    asyncio.run(exercise())


def test_main_cancelled_lifespan_finishes_its_poller(monkeypatch):
    async def exercise():
        import app.main as main_module

        lifespan_entered = asyncio.Event()
        poll_finished = asyncio.Event()

        monkeypatch.setattr(main_module, "init_db", lambda: None)
        monkeypatch.setattr(main_module.snapshot_store, "refresh", lambda force=False: None)

        async def cooperative_poll(store, stop, interval=30.0):
            await stop.wait()
            poll_finished.set()

        monkeypatch.setattr(main_module, "poll_snapshots", cooperative_poll)

        async def run_lifespan():
            async with main_module.lifespan(main_module.app):
                lifespan_entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(run_lifespan())
        await lifespan_entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert task.done()
        assert poll_finished.is_set()

    asyncio.run(exercise())


def test_main_exceptional_lifespan_finishes_its_poller(monkeypatch):
    async def exercise():
        import app.main as main_module

        poll_finished = asyncio.Event()
        sentinel = RuntimeError("private-request-body")

        monkeypatch.setattr(main_module, "init_db", lambda: None)
        monkeypatch.setattr(main_module.snapshot_store, "refresh", lambda force=False: None)

        async def cooperative_poll(store, stop, interval=30.0):
            await stop.wait()
            poll_finished.set()

        monkeypatch.setattr(main_module, "poll_snapshots", cooperative_poll)

        with pytest.raises(RuntimeError) as caught:
            async with main_module.lifespan(main_module.app):
                raise sentinel

        assert caught.value is sentinel
        assert poll_finished.is_set()

    asyncio.run(exercise())


def test_main_binds_existing_global_service_to_single_snapshot_store():
    import app.main as main_module
    from app.services.local_text_moderator import LocalTextModerator
    from app.services.moderation_service import moderation_service

    assert main_module.moderation_service is moderation_service
    assert isinstance(moderation_service.local_moderator, LocalTextModerator)
    provider = moderation_service.local_moderator._snapshot_provider
    assert provider.__self__ is snapshot_module.snapshot_store
    assert provider.__func__ is snapshot_module.snapshot_store.current_snapshot.__func__
