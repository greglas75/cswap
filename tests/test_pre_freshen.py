"""Medium: pre-freshen moves the switch's only network step off its critical path.

Measured 2026-09-22 on the owner's fleet: a proactive swap took 25-88 s from the
tick that crossed the threshold to the logged switch, while a manual
`cswap switch` took ~1 s — and the last points of the window burned at a median
5.8 pts/min. A 3-point margin (threshold 97) bought ~31 s, less than the swap.
The switch-time freshen is the one step on that path that goes to the network,
and only when a candidate's token is within FRESHEN_BUFFER_MS of expiry, so it
is refreshed here, at the pre-band, with a wider buffer.
"""

from __future__ import annotations

from claude_swap.autoswitch import (
    FRESHEN_BUFFER_MS,
    PREFRESHEN_BUFFER_MS,
    PREFRESHEN_INTERVAL_S,
    SwitchEvent,
    TickOutcome,
)
from tests.test_autoswitch import _usage
from tests.test_home_account import _harness


def engine(temp_home, *, live=1, **overrides):
    """Slot 1 active; pre-band 92, threshold 97; no home pin in the way."""
    return _harness(
        temp_home,
        live=live,
        **{
            "strategy": "best",
            "threshold": 97.0,
            "home_account": None,
            "pre_freshen_threshold": 92.0,
            "switch_under_load": True,
            **overrides,
        },
    )


def spy(h, result="ok"):
    """Record every freshen the engine asks for, and answer `result`."""
    calls: list[tuple[str, int]] = []

    def fake(num, email, buffer_ms=FRESHEN_BUFFER_MS):
        calls.append((num, buffer_ms))
        return result

    h.engine._freshen_target = fake
    return calls


IN_BAND = {"1": _usage(94), "2": _usage(20), "3": _usage(10)}


class TestPreFreshen:
    def test_the_pre_band_freshens_candidates_with_the_wide_buffer(self, temp_home):
        h = engine(temp_home)
        calls = spy(h)
        assert h.tick_with_usage(IN_BAND) is TickOutcome.NO_ACTION
        # Best headroom first: slot 3 (90%) is the likely target, then slot 2.
        assert [n for n, _ in calls] == ["3", "2"]
        # The wide buffer is the point: a token refreshed now must still clear
        # the switch-time buffer when the swap lands a minute later.
        assert all(b == PREFRESHEN_BUFFER_MS for _, b in calls)
        assert PREFRESHEN_BUFFER_MS > FRESHEN_BUFFER_MS

    def test_the_active_account_is_never_prefreshened(self, temp_home):
        h = engine(temp_home)
        calls = spy(h)
        h.tick_with_usage(IN_BAND)
        assert "1" not in {n for n, _ in calls}

    def test_below_the_pre_band_nothing_is_freshened(self, temp_home):
        h = engine(temp_home)
        calls = spy(h)
        h.tick_with_usage({"1": _usage(50), "2": _usage(20), "3": _usage(10)})
        assert calls == []

    def test_it_is_off_unless_configured(self, temp_home):
        h = engine(temp_home, pre_freshen_threshold=0.0)
        calls = spy(h)
        h.tick_with_usage(IN_BAND)
        assert calls == []

    def test_a_slot_is_not_refreshed_on_every_tick(self, temp_home):
        """Ticks come every ~15 s; refreshing each one would be a request storm."""
        h = engine(temp_home)
        calls = spy(h)
        h.tick_with_usage(IN_BAND)
        first = len(calls)
        h.tick_with_usage(IN_BAND)
        assert len(calls) == first  # throttled
        h.clock.advance(PREFRESHEN_INTERVAL_S + 1)
        h.tick_with_usage(IN_BAND)
        assert len(calls) == 2 * first  # interval elapsed: refreshed again

    def test_an_exhausted_candidate_is_skipped(self, temp_home):
        h = engine(temp_home)
        calls = spy(h)
        h.tick_with_usage({"1": _usage(94), "2": _usage(100), "3": _usage(10)})
        assert [n for n, _ in calls] == ["3"]

    def test_a_dead_candidate_is_quarantined_at_the_pre_band(self, temp_home):
        """Learning a lineage is dead here, not under the wall, is half the point."""
        h = engine(temp_home)
        spy(h, result="invalid_grant")
        quarantined: list[tuple[str, str]] = []
        h.engine._quarantine = lambda num, email, why: quarantined.append((num, why))
        h.tick_with_usage(IN_BAND)
        assert sorted(quarantined) == [("2", "invalid_grant"), ("3", "invalid_grant")]

    def test_the_reserve_is_prefreshened_last(self, temp_home):
        """The reserve usually has the MOST headroom and is the LEAST likely
        target; sorted naively it would spend the budget first."""
        h = engine(temp_home, reserve_account="3")  # slot 3: 90% headroom
        calls = spy(h)
        h.tick_with_usage(IN_BAND)
        assert [n for n, _ in calls] == ["2", "3"]

    def test_a_failing_prefreshen_never_fails_the_tick(self, temp_home):
        """An optimisation must never be the thing that stops the daemon."""
        h = engine(temp_home)

        def boom(*_a, **_k):
            raise RuntimeError("network down")

        h.engine._freshen_target = boom
        assert h.tick_with_usage(IN_BAND) is TickOutcome.NO_ACTION

    def test_a_transient_failure_is_left_to_the_switch_time_freshen(self, temp_home):
        h = engine(temp_home)
        spy(h, result="transient")
        quarantined: list = []
        h.engine._quarantine = lambda *a: quarantined.append(a)
        h.tick_with_usage(IN_BAND)
        assert quarantined == []


class TestSwitchTiming:
    def test_a_real_switch_reports_where_its_seconds_went(self, temp_home):
        h = engine(temp_home)
        spy(h)
        out = h.tick_with_usage({"1": _usage(99), "2": _usage(20), "3": _usage(10)})
        assert out is TickOutcome.SWITCHED
        ev = next(e for e in h.events if isinstance(e, SwitchEvent))
        assert set(ev.timing) >= {"freshenMs", "freshenAttempts", "quietScanMs", "switchMs"}
        assert ev.timing["freshenAttempts"] == 1
        assert all(isinstance(v, int) and v >= 0 for v in ev.timing.values())
        assert ev._fields()["timing"] == ev.timing

    def test_timing_counts_every_candidate_it_had_to_try(self, temp_home):
        """88 s looked like three attempts; the count must say so, not hide it."""
        h = engine(temp_home)
        answers = iter(["transient", "ok"])
        h.engine._freshen_target = lambda num, email, buffer_ms=FRESHEN_BUFFER_MS: next(answers)
        h.tick_with_usage({"1": _usage(99), "2": _usage(20), "3": _usage(10)})
        ev = next(e for e in h.events if isinstance(e, SwitchEvent))
        assert ev.timing["freshenAttempts"] == 2

    def test_an_aborted_switch_leaves_no_timing_behind(self, temp_home):
        """Every early return in _perform used to leave the freshen timing set,
        so the NEXT switch would have reported this one's freshen as its own."""
        h = engine(temp_home)
        spy(h)
        h.engine.switcher.switch_to = lambda *_a, **_k: {"switched": False, "reason": "t"}
        h.tick_with_usage({"1": _usage(99), "2": _usage(20), "3": _usage(10)})
        assert h.engine._switch_timing is None
        assert not any(isinstance(e, SwitchEvent) for e in h.events)
