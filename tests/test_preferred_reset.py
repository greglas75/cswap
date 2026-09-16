"""Medium: reset scheduling and credential locking with isolated local stores."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import ANY, patch

import pytest

from claude_swap import claude_locks, oauth
from claude_swap.autoswitch import ErrorEvent, TickOutcome
from claude_swap.claude_locks import claude_storage_lock
from claude_swap.exceptions import ClaudeCodeLockTimeout
from claude_swap.json_output import USAGE_TOKEN_EXPIRED
from claude_swap.usage_store import FetchRecord, UsageStore
from tests.test_autoswitch import FakeClock, _usage
from tests.test_preferred_home import preferred

IDENTITIES = {"1": ("home@example.com", ""), "2": ("other@example.com", "")}


def exhausted_store(tmp_path):
    clock = FakeClock()
    store = UsageStore(tmp_path / "cache", clock=clock)
    reset = datetime.fromtimestamp(clock.now + 10, timezone.utc).isoformat()
    store.record({"1": FetchRecord(usage=_usage(100, reset))}, IDENTITIES)
    store.set_poll_plan({"1": (clock.now + 70, 600)}, IDENTITIES)
    return store, clock


class TestPreferredReset:
    def test_reset_nomination_handles_missing_plan(self, tmp_path):
        clock = FakeClock()
        store = UsageStore(tmp_path / "cache", clock=clock)
        reset = datetime.fromtimestamp(clock.now + 10, timezone.utc).isoformat()
        store.record({"1": FetchRecord(usage=_usage(100, reset))}, IDENTITIES)
        clock.advance(11)
        store.request_reset_poll({"1": IDENTITIES["1"]}, (), 95)
        assert store.entries(IDENTITIES)["1"].next_poll_at == 1_000_011
        assert set(store.reserve(["1"], IDENTITIES, respect_plans=False)) == {"1"}

    def test_never_fetched_account_is_not_marked_as_having_quota(self, tmp_path):
        store = UsageStore(tmp_path / "cache", clock=FakeClock())
        store.request_reset_poll({"1": IDENTITIES["1"]}, (), 95)
        entry = store.entries(IDENTITIES)["1"]
        assert entry.last_good is None
        assert entry.next_poll_at is None
        assert entry.decision_value() is None

    def test_malformed_reset_does_not_accelerate_poll(self, tmp_path):
        store, clock = exhausted_store(tmp_path)
        store.record({"1": FetchRecord(usage=_usage(100, "not-a-date"))}, IDENTITIES)
        clock.advance(11)
        store.request_reset_poll({"1": IDENTITIES["1"]}, (), 95)
        assert store.entries(IDENTITIES)["1"].next_poll_at == 1_000_070
        assert store.reserve(["1"], IDENTITIES, respect_plans=False) == {}

    def test_real_collector_fetches_primary_and_returns_on_first_reset_tick(
        self, temp_home, monkeypatch
    ):
        h = preferred(temp_home, live=2)
        monkeypatch.setattr("claude_swap.switcher._FETCH_STAGGER_S", 0)
        monkeypatch.setattr(h.switcher, "_live_session_pids", lambda *args: [])
        reset = datetime.fromtimestamp(h.clock.now + 10, timezone.utc).isoformat()
        identities = {
            "1": ("home@example.com", ""), "2": ("b@example.com", ""),
            "3": ("c@example.com", ""),
        }
        h.switcher._usage_store.record({
            "1": FetchRecord(usage=_usage(100, reset)),
            "2": FetchRecord(usage=_usage(20)),
            "3": FetchRecord(usage=_usage(80)),
        }, identities, plans={num: (h.clock.now + 70, 600) for num in identities})
        with patch("claude_swap.oauth.try_fetch_usage_for_account",
                   return_value=oauth.UsageOutcome(_usage(0))) as fetch:
            h.clock.advance(9)
            assert h.engine.tick() is TickOutcome.NO_ACTION
            fetch.assert_not_called()
            h.clock.advance(2)
            assert h.engine.tick() is TickOutcome.SWITCHED
            fetch.assert_called_once_with(
                "1", "home@example.com",
                h.switcher.read_account_credentials("1", "home@example.com"),
                is_active=False, persist_credentials=ANY,
            )
        assert h.active_number() == 1

    @pytest.mark.parametrize("elapsed", [9, 10, 11])
    def test_first_reset_tick_can_fetch_before_normal_ttl(self, tmp_path, elapsed):
        store, clock = exhausted_store(tmp_path)
        clock.advance(elapsed)
        store.request_reset_poll({"1": IDENTITIES["1"]}, (), 99.9)
        claimed = store.reserve(["1"], IDENTITIES, respect_plans=False)
        assert set(claimed) == ({"1"} if elapsed >= 10 else set())
        # Scheduling never fabricates fresh quota.
        assert store.entries(IDENTITIES)["1"].last_good["five_hour"]["pct"] == 100
        assert store.entries(IDENTITIES)["2"].last_good is None

    def test_reset_request_respects_provider_retry_after(self, tmp_path):
        store, clock = exhausted_store(tmp_path)
        clock.advance(1)
        store.record({"1": FetchRecord(error="http-429", retry_after_s=300)}, IDENTITIES)
        clock.advance(10)
        store.request_reset_poll({"1": IDENTITIES["1"]}, (), 99.9)
        assert store.reserve(["1"], IDENTITIES, respect_plans=False) == {}
        assert store.entries(IDENTITIES)["1"].backoff_until == 1_000_301

    def test_failed_first_reset_probe_retries_after_backoff(self, tmp_path):
        store, clock = exhausted_store(tmp_path)
        clock.advance(11)
        store.request_reset_poll({"1": IDENTITIES["1"]}, (), 99.9)
        claim = store.reserve(["1"], IDENTITIES, respect_plans=False)
        assert set(claim) == {"1"}
        store.record({"1": FetchRecord(error="http-503")}, IDENTITIES, claim)
        retry_at = store.entries(IDENTITIES)["1"].backoff_until
        assert retry_at is not None
        clock.now = retry_at
        store.request_reset_poll({"1": IDENTITIES["1"]}, (), 99.9)
        assert set(store.reserve(["1"], IDENTITIES, respect_plans=False)) == {"1"}

    def test_reset_request_does_not_steal_an_active_collector_claim(self, tmp_path):
        store, clock = exhausted_store(tmp_path)
        clock.advance(11)
        first = store.claim(["1"], IDENTITIES)
        store.request_reset_poll({"1": IDENTITIES["1"]}, (), 99.9)
        assert store.reserve(["1"], IDENTITIES, respect_plans=False) == {}
        assert store.record({"1": FetchRecord(usage=_usage(0))}, IDENTITIES, first) == {"1"}

    def test_unchanged_reset_timestamp_cannot_create_a_fast_poll_loop(self, tmp_path):
        store, clock = exhausted_store(tmp_path)
        clock.advance(11)
        store.request_reset_poll({"1": IDENTITIES["1"]}, (), 99.9)
        claim = store.reserve(["1"], IDENTITIES, respect_plans=False)
        assert set(claim) == {"1"}
        old_usage = store.entries(IDENTITIES)["1"].last_good
        store.record({"1": FetchRecord(usage=old_usage)}, IDENTITIES, claim,
                     plans={"1": (clock.now + 600, 600)})
        clock.advance(15)
        store.request_reset_poll({"1": IDENTITIES["1"]}, (), 99.9)
        assert store.reserve(["1"], IDENTITIES, respect_plans=False) == {}

    @pytest.mark.parametrize("usage", [None, {}, _usage(20), _usage(100)])
    def test_unknown_or_unneeded_reset_keeps_existing_plan(self, tmp_path, usage):
        store, clock = exhausted_store(tmp_path)
        store.record({"1": FetchRecord(usage=usage)}, IDENTITIES)
        clock.advance(11)
        store.request_reset_poll({"1": IDENTITIES["1"]}, (), 99.9)
        assert store.entries(IDENTITIES)["1"].next_poll_at == 1_000_070
        assert store.reserve(["1"], IDENTITIES, respect_plans=False) == {}

    def test_later_blocking_weekly_reset_still_controls_readiness(self, tmp_path):
        store, clock = exhausted_store(tmp_path)
        usage = store.entries(IDENTITIES)["1"].last_good
        usage["seven_day"] = {"pct": 100, "resets_at":
                              datetime.fromtimestamp(clock.now + 100, timezone.utc).isoformat()}
        store.record({"1": FetchRecord(usage=usage)}, IDENTITIES)
        clock.advance(11)
        store.request_reset_poll({"1": IDENTITIES["1"]}, (), 99.9)
        assert store.entries(IDENTITIES)["1"].next_poll_at == 1_000_070


@pytest.fixture
def refreshing_account(temp_home, monkeypatch):
    h = preferred(temp_home)
    monkeypatch.setattr("claude_swap.switcher._FETCH_STAGGER_S", 0)
    monkeypatch.setattr(h.switcher, "_live_session_pids", lambda *args: [])
    path = temp_home / ".claude" / ".credentials.json"
    expired = json.dumps({"claudeAiOauth": {
        "accessToken": "expired", "refreshToken": "refresh-old", "expiresAt": 1,
    }})
    successor = json.dumps({"claudeAiOauth": {
        "accessToken": "fresh", "refreshToken": "refresh-new",
        "expiresAt": 32_503_680_000_000,
    }})
    path.write_text(expired)
    h.switcher.write_account_credentials("1", "home@example.com", expired)

    def rotated(credentials, **kwargs):
        latest = json.loads(expired)
        latest["mcpOAuth"] = {"server": {"accessToken": "new-mcp-token"}}
        path.write_text(json.dumps(latest))
        return oauth.RefreshOutcome(successor, None)

    return h, path, expired, successor, rotated


class TestSecureStoreLock:
    def test_refresh_preserves_mcp_update_during_token_rotation(self, refreshing_account):
        h, path, expired, successor, rotated = refreshing_account
        with patch("claude_swap.oauth.try_refresh_oauth_credentials",
                   side_effect=rotated) as refresh, patch(
            "claude_swap.oauth.try_fetch_usage_for_account",
            return_value=oauth.UsageOutcome(_usage(5)),
        ) as fetch:
            entries = h.switcher.usage_entries_by_account(fetch={"1"})
            refresh.assert_called_once_with(expired, timeout_s=6.0)
            fetch.assert_called_once_with(
                "1", "home@example.com", successor, is_active=True
            )
        assert entries["1"].sentinel is None
        saved = json.loads(path.read_text())
        assert saved["mcpOAuth"] == {"server": {"accessToken": "new-mcp-token"}}
        assert saved["claudeAiOauth"]["accessToken"] == "fresh"
        backup = json.loads(h.switcher.read_account_credentials("1", "home@example.com"))
        assert backup["claudeAiOauth"]["refreshToken"] == "refresh-new"

    def test_refresh_read_failure_preserves_active_store_and_successor_backup(
        self, refreshing_account, monkeypatch
    ):
        h, path, expired, _successor, rotated = refreshing_account

        def unreadable_after_rotation(credentials, **kwargs):
            result = rotated(credentials, **kwargs)
            monkeypatch.setattr(h.switcher, "_read_credentials", lambda: None)
            return result

        with patch("claude_swap.oauth.try_refresh_oauth_credentials",
                   side_effect=unreadable_after_rotation) as refresh, patch(
            "claude_swap.oauth.try_fetch_usage_for_account"
        ) as fetch:
            entries = h.switcher.usage_entries_by_account(fetch={"1"})
            refresh.assert_called_once_with(expired, timeout_s=6.0)
            fetch.assert_not_called()
        assert entries["1"].sentinel == USAGE_TOKEN_EXPIRED
        saved = json.loads(path.read_text())
        assert saved["mcpOAuth"] == {"server": {"accessToken": "new-mcp-token"}}
        assert saved["claudeAiOauth"]["accessToken"] == "expired"
        backup = json.loads(h.switcher.read_account_credentials("1", "home@example.com"))
        assert backup["claudeAiOauth"]["refreshToken"] == "refresh-new"

    def test_storage_lock_uses_native_name_and_is_released(self, temp_home):
        expected = temp_home / ".claude" / ".storage-write.lock"
        with claude_storage_lock():
            assert expected.is_dir()
        assert not expected.exists()

    def test_contended_storage_lock_is_not_stolen(self, temp_home):
        expected = temp_home / ".claude" / ".storage-write.lock"
        expected.mkdir()
        with pytest.raises(ClaudeCodeLockTimeout, match="storage-write"):
            with claude_storage_lock(timeout=0):
                pytest.fail("must not enter while another writer owns the lock")
        assert expected.is_dir()

    def test_real_switch_holds_storage_lock_and_preserves_mcp_login(self, temp_home):
        h = preferred(temp_home)
        path = temp_home / ".claude" / ".credentials.json"
        credentials = json.loads(path.read_text())
        credentials["mcpOAuth"] = {"test-server": {"accessToken": "mcp-kept"}}
        path.write_text(json.dumps(credentials))
        original = h.switcher._write_credentials
        writes = []

        def checked_write(value):
            assert (temp_home / ".claude" / ".storage-write.lock").is_dir()
            writes.append(value)
            return original(value)

        with patch.object(h.switcher, "_write_credentials", side_effect=checked_write):
            assert h.tick_with_usage({
                "1": _usage(100), "2": _usage(10), "3": _usage(80),
            }) is TickOutcome.SWITCHED
        assert len(writes) == 1
        saved = json.loads(path.read_text())
        assert saved["mcpOAuth"] == {"test-server": {"accessToken": "mcp-kept"}}
        assert saved["claudeAiOauth"]["accessToken"] == "sk-2"

    def test_contended_writer_aborts_switch_without_changing_login(self, temp_home, monkeypatch):
        h = preferred(temp_home)
        path = temp_home / ".claude" / ".credentials.json"
        before = path.read_text()
        (temp_home / ".claude" / ".storage-write.lock").mkdir()
        monkeypatch.setattr(claude_locks, "DEFAULT_TIMEOUT_S", 0)
        assert h.tick_with_usage({
            "1": _usage(100), "2": _usage(10), "3": _usage(80),
        }) is TickOutcome.ERROR
        assert path.read_text() == before
        assert h.active_number() == 1
        assert any(isinstance(event, ErrorEvent) for event in h.events)
