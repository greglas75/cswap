"""Medium: two machines, one pool (autoswitch.peerActiveFile / peerYield).

Owner, 2026-10-17: the Mac and ryzen-dev switched onto the same account in
the same minute all day, burned it at twice the rate, hit 100% before the
pace switch and stopped agents."""

from __future__ import annotations

import json

from claude_swap.autoswitch import NoSwitchEvent, TickOutcome
from tests.test_autoswitch import _usage
from tests.test_home_account import _harness


def peered(temp_home, tmp_path, *, live=1, peer="c@example.com", age=0.0, **overrides):
    path = tmp_path / "peer-active.json"
    h = _harness(
        temp_home, live=live,
        **{"strategy": "best", "home_account": None, "threshold": 95.0,
           "hysteresis_pct": 3.0, "switch_under_load": True,
           "peer_active_file": str(path), **overrides},
    )
    path.write_text(json.dumps({"email": peer, "at": h.clock.now - age}))
    return h


def test_the_other_machines_account_is_never_a_landing(temp_home, tmp_path):
    """Bug caught: both machines picking the same best account."""
    h = peered(temp_home, tmp_path)
    assert h.tick_with_usage({"1": _usage(100), "2": _usage(70), "3": _usage(0)}) is TickOutcome.SWITCHED
    assert h.active_number() == 2


def test_on_a_collision_the_yielding_side_leaves(temp_home, tmp_path):
    """Bug caught: two fleets riding one account into the wall."""
    h = peered(temp_home, tmp_path, live=3, peer_yield=True)
    assert h.tick_with_usage({"1": _usage(100), "2": _usage(50), "3": _usage(10)}) is TickOutcome.SWITCHED
    assert h.active_number() == 2


def test_without_room_elsewhere_the_collision_stays_quietly(temp_home, tmp_path):
    h = peered(temp_home, tmp_path, live=3, peer_yield=True)
    assert h.tick_with_usage({"1": _usage(100), "2": _usage(100), "3": _usage(10)}) is TickOutcome.NO_ACTION
    assert [e.reason for e in h.events if isinstance(e, NoSwitchEvent)] == ["peer-collision"]


def test_the_side_that_does_not_yield_stays(temp_home, tmp_path):
    """Bug caught: both sides leaving the shared account at once."""
    h = peered(temp_home, tmp_path, live=3)
    assert h.tick_with_usage({"1": _usage(100), "2": _usage(50), "3": _usage(10)}) is TickOutcome.NO_ACTION
    assert h.active_number() == 3


def test_a_stale_record_says_nothing(temp_home, tmp_path):
    """Bug caught: an account held out forever after the sync stopped."""
    h = peered(temp_home, tmp_path, age=600.0)
    assert h.tick_with_usage({"1": _usage(100), "2": _usage(70), "3": _usage(0)}) is TickOutcome.SWITCHED
    assert h.active_number() == 3
