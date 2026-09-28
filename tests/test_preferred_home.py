"""Medium: real account-routing engine with isolated filesystem and fake usage."""

from __future__ import annotations

import pytest

from claude_swap.autoswitch import SwitchEvent, TickOutcome
from claude_swap.exceptions import ConfigError
from claude_swap.settings import AutoSwitchSettings, load_settings, set_setting
from tests.test_autoswitch import _scoped_usage, _usage, _usage7, _write_transcript
from tests.test_home_account import _harness


def preferred(temp_home, *, live=1, **overrides):
    return _harness(
        temp_home,
        live=live,
        **{
            "strategy": "best",
            "threshold": 99.9,
            "home_mode": "prefer",
            "switch_under_load": True,
            **overrides,
        },
    )


def switches(harness):
    return [event for event in harness.events if isinstance(event, SwitchEvent)]


class TestPreferredHome:
    def test_with_equal_resets_the_most_life_wins_not_the_sequence(self, temp_home):
        # Was test_fallback_sequence_wins_over_larger_remaining_quota: slot
        # order decided the escape. Since 2026-09-28 the soonest weekly reset
        # decides (tests/test_expiring_quota_first.py), and with none known
        # the most life does — slot 3 at 100% life, not slot 2 at 30%.
        h = preferred(temp_home)
        assert h.tick_with_usage({
            "1": _usage(100), "2": _usage(70), "3": _usage(0),
        }) is TickOutcome.SWITCHED
        assert h.active_number() == 3

    def test_small_quota_jitter_does_not_cause_return_flapping(self, temp_home):
        h = preferred(temp_home, live=2, threshold=95)
        assert h.tick_with_usage({
            "1": _usage(94.9), "2": _usage(30), "3": _usage(0),
        }) is TickOutcome.NO_ACTION
        assert h.active_number() == 2
        assert switches(h) == []

    def test_return_is_allowed_at_exact_headroom_margin(self, temp_home):
        # The margin is HOME_RETURN_MIN_HEADROOM_PCT (20) once it exceeds
        # 100 - threshold + hysteresis — see tests/test_home_near_limit.py.
        h = preferred(temp_home, live=2, threshold=95)
        assert h.tick_with_usage({
            "1": _usage(80), "2": _usage(30), "3": _usage(0),
        }) is TickOutcome.SWITCHED
        assert h.active_number() == 1

    def test_third_account_is_used_when_first_two_are_exhausted(self, temp_home):
        h = preferred(temp_home)
        assert h.tick_with_usage({
            "1": _usage(100), "2": _usage(100), "3": _usage(0),
        }) is TickOutcome.SWITCHED
        assert h.active_number() == 3

    @pytest.mark.parametrize("used", [99.9, 100.0])
    def test_exhausted_primary_gives_way_to_available_fallback(self, temp_home, used):
        h = preferred(temp_home)
        outcome = h.tick_with_usage({"1": _usage(used), "2": _usage(10), "3": _usage(70)})
        assert outcome is TickOutcome.SWITCHED
        assert h.active_number() == 2
        assert switches(h)[0].to_ref == {"number": 2, "email": "b@example.com"}

    @pytest.mark.parametrize("used", [0.0, 50.0, 99.8])
    def test_primary_keeps_priority_while_it_has_capacity(self, temp_home, used):
        h = preferred(temp_home, strategy="consume-first")
        outcome = h.tick_with_usage({
            "1": _usage7(used, 0, "2030-12-30T00:00:00Z"),
            "2": _usage7(0, 0, "2030-01-01T00:00:00Z"),
            "3": _usage(0),
        })
        assert outcome is TickOutcome.NO_ACTION
        assert h.active_number() == 1
        assert switches(h) == []

    @pytest.mark.parametrize("used", [99.9, 100.0])
    def test_does_not_return_to_primary_with_no_capacity(self, temp_home, used):
        h = preferred(temp_home, live=2)
        outcome = h.tick_with_usage({"1": _usage(used), "2": _usage(40), "3": _usage(0)})
        assert outcome is TickOutcome.NO_ACTION
        assert h.active_number() == 2
        assert switches(h) == []

    def test_reset_returns_to_primary_despite_recent_switch_and_live_traffic(self, temp_home):
        h = preferred(temp_home, cooldown_seconds=300)
        _write_transcript(h, age_s=1)
        assert h.tick_with_usage({
            "1": _usage(100), "2": _usage(10), "3": _usage(80),
        }) is TickOutcome.SWITCHED
        assert h.active_number() == 2
        h.clock.advance(15)
        assert h.tick_with_usage({
            "1": _usage(0), "2": _usage(10), "3": _usage(80),
        }) is TickOutcome.SWITCHED
        assert h.active_number() == 1
        assert [event.trigger for event in switches(h)] == ["at-limit", "return-home"]

    def test_busy_return_waits_when_switch_under_load_is_disabled(self, temp_home):
        h = preferred(temp_home, live=2, switch_under_load=False)
        _write_transcript(h, age_s=1)
        assert h.tick_with_usage({
            "1": _usage(0), "2": _usage(10), "3": _usage(80),
        }) is TickOutcome.NO_ACTION
        assert h.active_number() == 2
        assert switches(h) == []

    @pytest.mark.parametrize("missing", [None, "token expired", "re-login needed"])
    def test_unknown_or_unavailable_primary_is_not_ready(self, temp_home, missing):
        h = preferred(temp_home, live=2)
        assert h.tick_with_usage({
            "1": missing, "2": _usage(10), "3": _usage(80),
        }) is TickOutcome.NO_ACTION
        assert h.active_number() == 2
        assert switches(h) == []

    def test_weekly_limit_still_blocks_return_if_the_provider_reports_one(self, temp_home):
        h = preferred(temp_home, live=2)
        assert h.tick_with_usage({
            "1": _usage7(0, 100), "2": _usage(10), "3": _usage(80),
        }) is TickOutcome.NO_ACTION
        assert h.active_number() == 2

    def test_missing_weekly_window_does_not_block_primary(self, temp_home):
        h = preferred(temp_home, live=2)
        assert h.tick_with_usage({
            "1": {"five_hour": {"pct": 0}}, "2": _usage(10), "3": _usage(80),
        }) is TickOutcome.SWITCHED
        assert h.active_number() == 1
        assert switches(h)[0].trigger == "return-home"

    def test_existing_pin_mode_still_waits_on_exhausted_primary(self, temp_home):
        h = preferred(temp_home, home_mode="pin")
        assert h.tick_with_usage({
            "1": _usage(100), "2": _usage(10), "3": _usage(80),
        }) is TickOutcome.NO_ACTION
        assert h.active_number() == 1
        assert switches(h) == []

    def test_disabled_primary_is_not_reactivated(self, temp_home):
        h = preferred(temp_home, live=2)
        h.switcher.set_account_disabled("1", True)
        assert h.tick_with_usage({
            "1": _usage(0), "2": _usage(10), "3": _usage(80),
        }) is TickOutcome.NO_ACTION
        assert h.active_number() == 2
        assert switches(h) == []

    def test_burned_model_prevents_return_even_after_five_hour_reset(self, temp_home):
        h = preferred(temp_home, live=2, model="Fable")
        usage = _scoped_usage(0, 100)
        assert h.tick_with_usage({
            "1": usage, "2": _usage(10), "3": _usage(80),
        }) is TickOutcome.NO_ACTION
        assert h.active_number() == 2
        assert switches(h) == []


class TestPreferredHomeConfiguration:
    def test_default_mode_preserves_pin_policy(self):
        assert AutoSwitchSettings().home_mode == "pin"

    def test_prefer_mode_round_trips_through_validated_config(self, tmp_path):
        set_setting(tmp_path, "autoswitch.homeMode", "prefer")
        assert load_settings(tmp_path).home_mode == "prefer"

    def test_invalid_mode_is_rejected(self, tmp_path):
        with pytest.raises(ConfigError, match="pin.*prefer"):
            set_setting(tmp_path, "autoswitch.homeMode", "random")


class TestPreferredEscapeHonoursTheReturnMargin:
    """An escape must not land on a home `_return_home` would refuse.

    `_return_home` demands `available >= (100 - threshold) + hysteresis_pct`
    before returning to the preferred account. An at-limit / failover escape
    reaches the generic ranking path instead, which ordered a preferred home
    by bare account sequence — so it could land on a home holding 5% while a
    90% account sat beside it, and escape again on the next tick. That is the
    flapping `test_small_quota_jitter_does_not_cause_return_flapping` pins for
    the return path, defeated by the one path that never checked the margin.
    """

    def test_escape_prefers_headroom_over_a_home_below_the_margin(self, temp_home):
        h = preferred(temp_home, live=2)
        assert h.tick_with_usage({
            "1": _usage(95),    # home: 5% life — under the margin
            "2": _usage(100),   # active, exhausted -> at-limit escape
            "3": _usage(10),    # 90% life
        }) is TickOutcome.SWITCHED
        assert h.active_number() == 3

    def test_a_home_above_the_margin_still_wins_the_sequence(self, temp_home):
        h = preferred(temp_home, live=2)
        assert h.tick_with_usage({
            "1": _usage(20),    # home: 80% life — clears the margin
            "2": _usage(100),
            "3": _usage(10),    # healthier, but home keeps priority
        }) is TickOutcome.SWITCHED
        assert h.active_number() == 1
