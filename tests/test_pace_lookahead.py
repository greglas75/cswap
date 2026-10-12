"""Medium: the pace look-ahead switches before a fast climb reaches the wall.

Usage readings change only every 1-3 minutes. On 2026-10-12 a small plan carrying
the whole fleet read 24 -> 57 -> 100 % of its 5h window in 7 minutes: the 95 %
threshold was never seen, the switch came only at the wall, and the requests in
flight failed with the session limit. ``paceLookaheadSeconds`` projects the climb
measured between the last two distinct readings and treats a projected crossing
as a real one.
"""

from __future__ import annotations

import pytest

from claude_swap.autoswitch import PaceEvent, SwitchEvent, TickOutcome
from claude_swap.settings import AutoSwitchSettings
from tests.test_autoswitch import _usage
from tests.test_home_account import _harness


def engine(temp_home, **overrides):
    """Slot 1 active, threshold 95, look-ahead 180 s; no home pin in the way."""
    return _harness(
        temp_home,
        live=1,
        **{
            "strategy": "best",
            "threshold": 95.0,
            "home_account": None,
            "switch_under_load": True,
            "pace_lookahead_seconds": 180.0,
            **overrides,
        },
    )


def fleet(active_pct: float) -> dict:
    return {"1": _usage(active_pct), "2": _usage(10), "3": _usage(10)}


class TestPaceAtTheTick:
    def test_a_climb_of_33_points_in_151_s_switches_at_57(self, temp_home):
        # The 2026-10-12 trace: 57 + 33/151 * 180 = 96.3 >= 95.
        h = engine(temp_home)
        assert h.tick_with_usage(fleet(24)) is TickOutcome.NO_ACTION
        h.clock.advance(151)
        h.tick_with_usage(fleet(57))
        assert h.active_number() != 1
        assert [e for e in h.events if isinstance(e, SwitchEvent)][0].trigger == "proactive"
        (pace,) = [e for e in h.events if isinstance(e, PaceEvent)]
        assert (pace.utilization_pct, round(pace.projected_pct, 1)) == (57, 96.3)

    def test_a_slow_climb_stays_until_the_threshold(self, temp_home):
        # 84 + 8/145 * 180 = 93.9 < 95: a few points per reading is no reason to leave.
        h = engine(temp_home)
        h.tick_with_usage(fleet(76))
        h.clock.advance(145)
        assert h.tick_with_usage(fleet(84)) is TickOutcome.NO_ACTION
        assert h.active_number() == 1
        assert not [e for e in h.events if isinstance(e, PaceEvent)]

    def test_it_is_off_unless_configured(self, temp_home):
        h = engine(temp_home, pace_lookahead_seconds=0.0)
        h.tick_with_usage(fleet(24))
        h.clock.advance(151)
        assert h.tick_with_usage(fleet(57)) is TickOutcome.NO_ACTION
        assert h.active_number() == 1


# Each step: (seconds since the previous one, active slot, its utilization).
SEQUENCES = {
    # Bug caught: firing on a single reading, with no rise ever measured.
    "a first reading never fires": ([(0, "1", 94)], False),
    # Bug caught: a blocked switch forgetting the crossing on the next same-value tick.
    "the crossing holds while the reading stands": (
        [(0, "1", 24), (151, "1", 57), (15, "1", 57)], True),
    # Bug caught: the rate measured across a switch, from another account's reading.
    "a new slot starts over": ([(0, "1", 24), (151, "2", 57)], False),
    # Bug caught: after a window reset the climb measured from the stale high value
    # (60 vs 50 over 211 s projects 68.5) instead of from the reset (2 -> 60 in 151 s).
    "a window reset is a new baseline": (
        [(0, "1", 50), (60, "1", 2), (151, "1", 60)], True),
}


@pytest.mark.parametrize("steps,fires", SEQUENCES.values(), ids=SEQUENCES.keys())
def test_pace_crossing(temp_home, steps, fires):
    h = engine(temp_home)
    settings = AutoSwitchSettings(threshold=95.0, pace_lookahead_seconds=180.0)
    result = None
    for advance, slot, pct in steps:
        h.clock.advance(advance)
        result = h.engine._pace_crosses(slot, float(pct), settings)
    assert result is fires
