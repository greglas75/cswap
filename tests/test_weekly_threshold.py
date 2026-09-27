"""Medium: weekly windows switch at their own threshold (autoswitch.weeklyThreshold).

Owner, 2026-09-27: at threshold 95 every account was left with 5% of its WEEKLY
quota unused — a weekly point is ~34x a 5h point. The 5h window keeps 95; the
7d and model-scoped weekly windows switch at 99.
"""

from __future__ import annotations

from claude_swap import oauth
from claude_swap.autoswitch import SwitchEvent, TickOutcome
from claude_swap.settings import AutoSwitchSettings, load_settings, set_setting
from tests.test_autoswitch import EngineHarness, _usage7


def _fleet(temp_home):
    h = EngineHarness(
        temp_home, strategy="best", threshold=95.0, weekly_threshold=99.0,
        hysteresis_pct=3.0, cooldown_seconds=0.0, switch_under_load=True,
    )
    h.seed(1, "a@x.com")
    h.seed(2, "b@x.com")
    h.make_live("a@x.com", 1)
    return h


class TestHeadroomShift:
    def test_weekly_window_is_discounted_5h_is_not(self):
        usage = {"five_hour": {"pct": 10.0}, "seven_day": {"pct": 97.0}}
        assert oauth.account_headroom(usage, weekly_shift=4.0) == 7.0
        usage = {"five_hour": {"pct": 96.0}, "seven_day": {"pct": 20.0}}
        assert oauth.account_headroom(usage, weekly_shift=4.0) == 4.0

    def test_an_exhausted_weekly_window_stays_exhausted(self):
        usage = {"five_hour": {"pct": 0.0}, "seven_day": {"pct": 100.0}}
        assert oauth.account_headroom(usage, weekly_shift=4.0) == 0.0

    def test_no_shift_is_the_old_behaviour(self):
        usage = {"five_hour": {"pct": 10.0}, "seven_day": {"pct": 97.0}}
        assert oauth.account_headroom(usage) == 3.0


class TestSettings:
    def test_shift_is_the_gap_between_the_two_thresholds(self):
        assert AutoSwitchSettings(threshold=95.0, weekly_threshold=99.0).weekly_shift == 4.0
        assert AutoSwitchSettings(threshold=95.0).weekly_shift == 0.0
        assert AutoSwitchSettings(threshold=95.0, weekly_threshold=90.0).weekly_shift == 0.0

    def test_round_trips_through_config(self, tmp_path):
        set_setting(tmp_path, "autoswitch.weeklyThreshold", "99")
        assert load_settings(tmp_path).weekly_threshold == 99.0


class TestEngine:
    def test_weekly_at_97_no_longer_switches(self, temp_home):
        h = _fleet(temp_home)
        assert h.tick_with_usage({"1": _usage7(10, 97), "2": _usage7(0, 10)}) is TickOutcome.NO_ACTION
        assert h.active_number() == 1

    def test_weekly_at_99_switches(self, temp_home):
        h = _fleet(temp_home)
        assert h.tick_with_usage({"1": _usage7(10, 99), "2": _usage7(0, 10)}) is TickOutcome.SWITCHED
        assert h.active_number() == 2

    def test_5h_still_switches_at_95(self, temp_home):
        h = _fleet(temp_home)
        assert h.tick_with_usage({"1": _usage7(95, 20), "2": _usage7(0, 10)}) is TickOutcome.SWITCHED
        assert h.active_number() == 2
        assert any(isinstance(e, SwitchEvent) for e in h.events)
