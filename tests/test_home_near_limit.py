"""Medium: the 2026-09-23 incident — a prefer-mode home rode past 100%.

Three faults in one evening, each read from the daemon log:
  1. the home read 94%, then "keychain unavailable" — and the pin held on
     "unknown" for minutes (home-unknown-hold) while the window kept burning;
  2. the owner left the home by hand at 97%; one minute later the daemon
     returned the login to it, because 6% headroom met the hysteresis margin;
  3. the at-limit switch then spent 47-65 s scanning transcripts only to label
     itself — at the limit nothing is written, so the early exit never fires.
"""

from __future__ import annotations

import claude_swap.autoswitch as mod
from claude_swap.autoswitch import HOME_RETURN_MIN_HEADROOM_PCT, TickOutcome
from claude_swap.json_output import USAGE_KEYCHAIN_UNAVAILABLE
from claude_swap.usage_store import UsageEntry
from tests.test_autoswitch import _entry_for, _usage
from tests.test_home_account import _reasons
from tests.test_preferred_home import preferred, switches


def owner_settings(temp_home, *, live):
    return preferred(temp_home, live=live, threshold=97.0, hysteresis_pct=3.0)


class TestReturnNeedsRealRoom:
    def test_a_home_at_94_percent_does_not_pull_the_login_back(self, temp_home):
        h = owner_settings(temp_home, live=2)
        assert h.tick_with_usage({
            "1": _usage(94), "2": _usage(15), "3": _usage(0),
        }) is TickOutcome.NO_ACTION
        assert h.active_number() == 2
        assert switches(h) == []

    def test_a_home_with_the_minimum_room_returns(self, temp_home):
        h = owner_settings(temp_home, live=2)
        assert h.tick_with_usage({
            "1": _usage(100 - HOME_RETURN_MIN_HEADROOM_PCT),
            "2": _usage(15), "3": _usage(0),
        }) is TickOutcome.SWITCHED
        assert h.active_number() == 1

    def test_an_escape_does_not_land_on_a_nearly_spent_home(self, temp_home):
        h = owner_settings(temp_home, live=2)
        assert h.tick_with_usage({
            "1": _usage(90), "2": _usage(100), "3": _usage(30),
        }) is TickOutcome.SWITCHED
        assert h.active_number() == 3


class TestUnreadableHomeNearTheThreshold:
    def _tick(self, h, last_used):
        return h.tick_with_entries({
            "1": UsageEntry(
                sentinel=USAGE_KEYCHAIN_UNAVAILABLE, last_good=_usage(last_used),
            ),
            "2": _entry_for(_usage(20), h.clock.now),
            "3": _entry_for(_usage(50), h.clock.now),
        })

    def test_last_reading_in_the_band_escapes_instead_of_holding(self, temp_home):
        h = owner_settings(temp_home, live=1)
        assert self._tick(h, 94) is TickOutcome.SWITCHED
        assert h.active_number() == 2
        assert "home-unknown-hold" not in _reasons(h)

    def test_last_reading_with_room_still_holds_the_pin(self, temp_home):
        h = owner_settings(temp_home, live=1)
        assert self._tick(h, 60) is TickOutcome.NO_ACTION
        assert h.active_number() == 1
        assert "home-unknown-hold" in _reasons(h)


class TestUngatedSwitchScansAfterTheSwap:
    def test_at_limit_switch_lands_before_the_transcript_walk(self, temp_home):
        h = owner_settings(temp_home, live=2)
        order = []
        original_scan = mod.latest_session_activity_ts
        original_switch = h.switcher.switch_to

        def scan(*_a, **_k):
            order.append("scan")
            return None

        def switch(*a, **k):
            order.append("switch")
            return original_switch(*a, **k)

        mod.latest_session_activity_ts = scan
        h.switcher.switch_to = switch
        try:
            assert h.tick_with_usage({
                "1": _usage(100), "2": _usage(100), "3": _usage(10),
            }) is TickOutcome.SWITCHED
        finally:
            mod.latest_session_activity_ts = original_scan
        assert "switch" in order
        assert "scan" not in order[: order.index("switch")]
        (event,) = switches(h)
        assert event.gate == "quiet"  # still measured, just after the swap
        assert event.timing["quietScanMs"] == 0
