"""Medium: the reserve account is the owner's main login — touched last, and
only while it still has life. Companion to test_preferred_home.py."""

from __future__ import annotations

import pytest

from claude_swap.autoswitch import SwitchEvent, TickOutcome
from tests.test_autoswitch import _usage, _usage7
from tests.test_home_account import _harness


def reserved(temp_home, *, live=1, **overrides):
    """Slot 3 is the reserve; plain `best` ranking, no home pin in the way."""
    return _harness(
        temp_home,
        live=live,
        **{
            "strategy": "best",
            "threshold": 99.9,
            "home_account": None,
            "reserve_account": "3",
            "reserve_min_life_pct": 80.0,
            "switch_under_load": True,
            **overrides,
        },
    )


def switches(harness):
    return [event for event in harness.events if isinstance(event, SwitchEvent)]


class TestReserveAccount:
    def test_reserve_is_skipped_although_it_has_the_most_headroom(self, temp_home):
        """The whole point: `best` would take slot 3 first, the reserve must not."""
        h = reserved(temp_home)
        assert h.tick_with_usage({
            "1": _usage(100), "2": _usage(70), "3": _usage(0),
        }) is TickOutcome.SWITCHED
        assert h.active_number() == 2

    def test_reserve_lands_when_nothing_else_qualifies(self, temp_home):
        h = reserved(temp_home)
        assert h.tick_with_usage({
            "1": _usage(100), "2": _usage(100), "3": _usage(0),
        }) is TickOutcome.SWITCHED
        assert h.active_number() == 3

    @pytest.mark.parametrize("used", [20.1, 25.0, 60.0])
    def test_reserve_is_refused_below_its_life_threshold(self, temp_home, used):
        """Last resort never means "burn the account that has to stay usable"."""
        h = reserved(temp_home)
        # Every candidate refused — the engine reports BLOCKED, not NO_ACTION.
        assert h.tick_with_usage({
            "1": _usage(100), "2": _usage(100), "3": _usage(used),
        }) is TickOutcome.BLOCKED
        assert h.active_number() == 1
        assert switches(h) == []

    def test_reserve_lands_at_exactly_its_life_threshold(self, temp_home):
        """80% life left is still 'at least 80%' — the boundary is inclusive."""
        h = reserved(temp_home)
        assert h.tick_with_usage({
            "1": _usage(100), "2": _usage(100), "3": _usage(20.0),
        }) is TickOutcome.SWITCHED
        assert h.active_number() == 3

    def test_the_weekly_window_binds_the_life_check_too(self, temp_home):
        """5h wide open, weekly half gone: headroom is the WORSE window."""
        h = reserved(temp_home)
        assert h.tick_with_usage({
            "1": _usage(100), "2": _usage(100), "3": _usage7(0.0, 50.0),
        }) is TickOutcome.BLOCKED
        assert h.active_number() == 1
        assert switches(h) == []

    def test_an_unset_reserve_leaves_ranking_untouched(self, temp_home):
        """Regression guard: without the setting, `best` still takes slot 3."""
        h = reserved(temp_home, reserve_account=None)
        assert h.tick_with_usage({
            "1": _usage(100), "2": _usage(70), "3": _usage(0),
        }) is TickOutcome.SWITCHED
        assert h.active_number() == 3

    def test_a_reserve_naming_no_managed_account_is_inert(self, temp_home):
        h = reserved(temp_home, reserve_account="nobody@example.com")
        assert h.tick_with_usage({
            "1": _usage(100), "2": _usage(70), "3": _usage(0),
        }) is TickOutcome.SWITCHED
        assert h.active_number() == 3
