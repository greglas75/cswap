"""Medium: past the home, the weekly quota that expires soonest is used first.

Owner, 2026-09-28: "system powinien robić priorytet też na takie konta, które
zaraz się zresetują, żeby wcisnąć jak najwięcej". Weekly quota left at a reset
is lost, and only one login burns at a time.
"""

from __future__ import annotations

from claude_swap.autoswitch import TickOutcome
from tests.test_autoswitch import _R_LATER, _R_LATEST, _R_SOON, _usage7
from tests.test_preferred_home import preferred


def test_soonest_weekly_reset_beats_more_life(temp_home):
    h = preferred(temp_home, live=1, threshold=95.0)
    assert h.tick_with_usage({
        "1": _usage7(100, 10),                 # home, at its 5h limit
        "2": _usage7(0, 10, _R_LATEST),        # 90% life, resets last
        "3": _usage7(0, 60, _R_SOON),          # 40% life, resets first
    }) is TickOutcome.SWITCHED
    assert h.active_number() == 3


def test_a_nearly_spent_account_does_not_win_on_its_reset(temp_home):
    h = preferred(temp_home, live=1, threshold=95.0)
    assert h.tick_with_usage({
        "1": _usage7(100, 10),
        "2": _usage7(0, 30, _R_LATER),         # 70% life
        "3": _usage7(0, 92, _R_SOON),          # 8% life: under the useful floor
    }) is TickOutcome.SWITCHED
    assert h.active_number() == 2


def test_the_home_still_comes_first(temp_home):
    h = preferred(temp_home, live=2, threshold=95.0)
    assert h.tick_with_usage({
        "1": _usage7(10, 10, _R_LATEST),       # home with room, resets last
        "2": _usage7(100, 10, _R_LATER),       # active, at its 5h limit
        "3": _usage7(0, 40, _R_SOON),
    }) is TickOutcome.SWITCHED
    assert h.active_number() == 1


def test_unknown_resets_sort_after_known_ones(temp_home):
    h = preferred(temp_home, live=1, threshold=95.0)
    assert h.tick_with_usage({
        "1": _usage7(100, 10),
        "2": _usage7(0, 10),                   # reset unknown, 90% life
        "3": _usage7(0, 50, _R_LATEST),        # reset known, 50% life
    }) is TickOutcome.SWITCHED
    assert h.active_number() == 3
