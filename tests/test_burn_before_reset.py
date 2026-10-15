"""Medium: weekly quota about to expire is used before the reset
(autoswitch.burnBeforeResetMinutes). Owner, 2026-10-15: greg@avsh.pl reset
with 13% of its week unused while the prefer-mode home held the login."""

from __future__ import annotations

from datetime import datetime, timezone

from claude_swap.autoswitch import NoSwitchEvent, TickOutcome
from tests.test_autoswitch import _usage
from tests.test_home_account import _harness


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def week(h, pct5: float, pct7: float, resets_in_min: float) -> dict:
    return {
        "five_hour": {"pct": pct5},
        "seven_day": {"pct": pct7, "resets_at": _iso(h.clock.now + resets_in_min * 60)},
    }


def burner(temp_home, *, live=1, **overrides):
    """Slot 1 is a prefer-mode home with room — the state that held the login."""
    return _harness(
        temp_home, live=live,
        **{"strategy": "best", "home_mode": "prefer", "threshold": 95.0,
           "hysteresis_pct": 3.0, "switch_under_load": True,
           "burn_before_reset_minutes": 120.0, **overrides},
    )


def test_an_expiring_week_takes_the_login_from_the_home(temp_home):
    """Bug caught: the home keeps the login and the expiring week is lost."""
    h = burner(temp_home)
    assert h.tick_with_usage({
        "1": _usage(20), "2": _usage(10), "3": week(h, 0, 87, 18),
    }) is TickOutcome.SWITCHED
    assert h.active_number() == 3


def test_a_live_expiring_account_is_not_pulled_back_home(temp_home):
    """Bug caught: the prefer return fires on the next tick and undoes the move."""
    h = burner(temp_home, live=3)
    assert h.tick_with_usage({
        "1": _usage(20), "2": _usage(10), "3": week(h, 40, 87, 18),
    }) is TickOutcome.NO_ACTION
    assert h.active_number() == 3
    assert "burn-before-reset" in [e.reason for e in h.events if isinstance(e, NoSwitchEvent)]


def test_the_soonest_reset_goes_first(temp_home):
    h = burner(temp_home)
    assert h.tick_with_usage({
        "1": _usage(20), "2": week(h, 0, 50, 90), "3": week(h, 0, 50, 30),
    }) is TickOutcome.SWITCHED
    assert h.active_number() == 3


def test_a_full_5h_window_is_not_a_target(temp_home):
    """Bug caught: landing on an account that is at its 5h limit."""
    h = burner(temp_home)
    h.tick_with_usage({"1": _usage(20), "2": _usage(10), "3": week(h, 94, 87, 18)})
    assert h.active_number() == 1


def test_a_reset_beyond_the_window_changes_nothing(temp_home):
    h = burner(temp_home)
    h.tick_with_usage({"1": _usage(20), "2": _usage(10), "3": week(h, 0, 50, 180)})
    assert h.active_number() == 1


def test_an_expiring_account_at_its_threshold_is_left(temp_home):
    """Bug caught: the rule holding the login on an account at its 5h limit."""
    h = burner(temp_home, live=3)
    assert h.tick_with_usage({
        "1": _usage(20), "2": _usage(10), "3": week(h, 96, 87, 18),
    }) is TickOutcome.SWITCHED
    assert h.active_number() != 3


def test_off_by_default(temp_home):
    h = burner(temp_home, burn_before_reset_minutes=0.0)
    h.tick_with_usage({"1": _usage(20), "2": _usage(10), "3": week(h, 0, 87, 18)})
    assert h.active_number() == 1
