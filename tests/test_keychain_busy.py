"""Medium: the 2026-09-23 night — an overloaded Keychain posed as an empty fleet.

22:06-22:26Z five at-limit switches failed on "Current account credential is
empty (Keychain unreadable?)"; at 23:06Z slots with 63% and 26% life were
dropped from the candidates because their backups could not be read, and the
engine declared every account exhausted and slept 600 s.
"""

from __future__ import annotations

from unittest.mock import patch

from claude_swap.autoswitch import AllExhaustedEvent, TickOutcome
from tests.test_autoswitch import EngineHarness, _usage


def _fleet(temp_home):
    h = EngineHarness(temp_home, strategy="best", threshold=97.0, cooldown_seconds=0.0)
    h.seed(1, "a@example.com")
    h.seed(2, "b@example.com")
    h.seed(3, "c@example.com")
    h.make_live("a@example.com", 1)
    return h


class TestUnreadableSlotIsNotExhausted:
    def test_a_slot_whose_backup_is_unreadable_blocks_the_exhausted_verdict(self, temp_home):
        h = _fleet(temp_home)
        with patch.object(h.switcher, "switchable_account_numbers", return_value=["1", "2"]):
            outcome = h.tick_with_usage({"1": _usage(100), "2": _usage(100), "3": _usage(40)})
        assert outcome is TickOutcome.BLOCKED
        assert not any(isinstance(e, AllExhaustedEvent) for e in h.events)

    def test_a_truly_spent_fleet_is_still_reported_exhausted(self, temp_home):
        h = _fleet(temp_home)
        outcome = h.tick_with_usage({"1": _usage(100), "2": _usage(100), "3": _usage(100)})
        assert outcome is TickOutcome.BLOCKED
        assert any(isinstance(e, AllExhaustedEvent) for e in h.events)


class TestEmptyLiveReadIsRetried:
    def test_an_empty_read_that_settles_lets_the_switch_through(self, temp_home):
        h = _fleet(temp_home)
        real = h.switcher._read_credentials
        calls = {"n": 0}

        def busy_then_ok():
            calls["n"] += 1
            return "" if calls["n"] <= 2 else real()

        with patch.object(h.switcher, "_read_credentials", side_effect=busy_then_ok), \
             patch("claude_swap.switcher.time.sleep") as slept:
            result = h.switcher.switch_to("2", json_output=True)
        assert result and result.get("switched")
        # switch_to also reads the live credential before the backup step, so
        # the exact split of the two empty reads is not the contract — that
        # the switch lands after at least one retry is.
        assert slept.call_count >= 1

    def test_a_read_that_never_settles_still_refuses(self, temp_home):
        h = _fleet(temp_home)
        with patch.object(h.switcher, "_read_credentials", return_value=""), \
             patch("claude_swap.switcher.time.sleep"):
            try:
                h.switcher.switch_to("2", json_output=True)
            except Exception as e:  # noqa: BLE001 — the contract is "refuses"
                assert "refusing to overwrite its backup" in str(e)
            else:
                raise AssertionError("switch went through on an empty live read")

