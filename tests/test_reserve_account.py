"""Medium: the reserve account is the owner's main login — touched last, and
only while it still has life. Companion to test_preferred_home.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_swap.autoswitch import NoSwitchEvent, SwitchEvent, TickOutcome
from claude_swap.settings import load_settings
from tests.test_home_account import settings_path
from tests.test_autoswitch import _R_LATER, _R_LATEST, _R_SOON, _usage, _usage7
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

    def test_the_block_names_the_reserve_floor(self, temp_home):
        """A blocked tick must say WHY. The generic detail describes the
        threshold/hysteresis gate, which an at-limit escape never runs — so on
        a fleet held back only by the reserve it read as "everyone is
        exhausted" and sent the operator hunting for quota sitting right
        there, deliberately untouched."""
        h = reserved(temp_home)
        assert h.tick_with_usage({
            "1": _usage(100), "2": _usage(100), "3": _usage7(0.0, 60.0),
        }) is TickOutcome.BLOCKED
        reasons = [e.reason for e in h.events if isinstance(e, NoSwitchEvent)]
        assert "reserve-protected" in reasons
        detail = next(e.detail for e in h.events
                      if isinstance(e, NoSwitchEvent) and e.reason == "reserve-protected")
        assert "40% of its week" in detail and "80% reserve floor" in detail

    @pytest.mark.parametrize("used", [20.1, 25.0, 60.0])
    def test_reserve_is_refused_below_its_life_threshold(self, temp_home, used):
        """Last resort never means "burn the account that has to stay usable"."""
        h = reserved(temp_home)
        # Every candidate refused — the engine reports BLOCKED, not NO_ACTION.
        assert h.tick_with_usage({
            "1": _usage(100), "2": _usage(100), "3": _usage7(0.0, used),
        }) is TickOutcome.BLOCKED
        assert h.active_number() == 1
        assert switches(h) == []

    def test_reserve_lands_at_exactly_its_life_threshold(self, temp_home):
        """80% of the week left is still 'at least 80%' — inclusive boundary."""
        h = reserved(temp_home)
        assert h.tick_with_usage({
            "1": _usage(100), "2": _usage(100), "3": _usage7(0.0, 20.0),
        }) is TickOutcome.SWITCHED
        assert h.active_number() == 3

    def test_the_weekly_window_is_what_the_floor_reads(self, temp_home):
        """5h wide open, weekly half gone: the floor holds the reserve."""
        h = reserved(temp_home)
        assert h.tick_with_usage({
            "1": _usage(100), "2": _usage(100), "3": _usage7(0.0, 50.0),
        }) is TickOutcome.BLOCKED
        assert h.active_number() == 1
        assert switches(h) == []

    def test_a_busy_5h_window_does_not_hold_the_reserve(self, temp_home):
        """Owner's rule (2026-09-30): usable until 80% of its WEEK is spent.
        The floor used to read the binding window, so 2026-09-29 22:49Z a
        reserve with 53% of its week left was held while everything else was
        out, and every session stopped for 3 hours. The 5h window is the
        normal threshold's business, not the floor's."""
        h = reserved(temp_home, reserve_min_life_pct=20.0)
        assert h.tick_with_usage({
            "1": _usage(100), "2": _usage(100), "3": _usage7(85.0, 57.0),
        }) is TickOutcome.SWITCHED
        assert h.active_number() == 3

    def test_the_default_floor_lets_the_reserve_run_to_80pct_of_its_week(self, temp_home):
        from claude_swap.settings import AutoSwitchSettings

        h = reserved(
            temp_home, reserve_min_life_pct=AutoSwitchSettings().reserve_min_life_pct
        )
        assert h.tick_with_usage({
            "1": _usage(100), "2": _usage(100), "3": _usage7(0.0, 79.0),
        }) is TickOutcome.SWITCHED

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


class TestReserveUnderConsumeFirst:
    """The filter runs after ranking, so it must hold under every strategy —
    including the one whose whole point is to reach for the soonest reset."""

    def test_reserve_is_skipped_even_when_it_resets_soonest(self, temp_home):
        h = reserved(temp_home, strategy="consume-first")
        assert h.tick_with_usage({
            "1": _usage7(20, 20, _R_LATEST),
            "2": _usage7(10, 10, _R_LATER),
            "3": _usage7(10, 10, _R_SOON),   # reserve: consume-first would take it first
        }) is TickOutcome.SWITCHED
        assert h.active_number() == 2

    def test_reserve_still_lands_as_the_last_resort(self, temp_home):
        h = reserved(temp_home, strategy="consume-first")
        assert h.tick_with_usage({
            "1": _usage7(100, 100, _R_LATEST),
            "2": _usage7(100, 100, _R_LATER),
            "3": _usage7(10, 10, _R_SOON),
        }) is TickOutcome.SWITCHED
        assert h.active_number() == 3


class TestReserveSettingsClamp:
    """Parity with TestSettingsClamp: a hand-written slot number must read.

    `reserveAccount` reached the settings surface without the bare-int
    coercion `homeAccount` has, so `{"reserveAccount": 3}` fell through to
    None — the reserve looked configured while gating nothing, with no
    warning (the string branch does not warn, unlike `choice`).
    """

    def test_bare_json_number_reads_as_the_slot(self, tmp_path: Path):
        path = settings_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"autoswitch": {"reserveAccount": 3}}))
        assert load_settings(tmp_path).reserve_account == "3"

    def test_an_email_reserve_is_untouched(self, tmp_path: Path):
        path = settings_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"autoswitch": {"reserveAccount": "a@example.com"}}))
        assert load_settings(tmp_path).reserve_account == "a@example.com"
