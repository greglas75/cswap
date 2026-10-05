"""Medium: the transcript scan is what made a switch slow, not the token refresh.

Measured 2026-09-22 with the new SwitchEvent timing, on the first real swap
after it shipped: freshenMs=24, switchMs=337, quietScanMs=12546. The scan stats
every *.jsonl under the projects dir — 9530 files / 10.7 GB on that machine —
and a switch asks for it twice.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from claude_swap.autoswitch import (
    QUIET_WINDOW_S,
    SESSION_SCAN_CACHE_S,
    latest_session_activity_ts,
)
from tests.test_home_account import _harness


def transcripts(root: Path, ages_s: dict[str, float], now: float) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name, age in ages_s.items():
        f = root / f"{name}.jsonl"
        f.write_text("{}\n")
        os.utime(f, (now - age, now - age))


class TestScanEarlyExit:
    def test_it_stops_at_the_first_transcript_inside_the_window(self, tmp_path):
        now = time.time()
        transcripts(tmp_path / "p", {"a": 10.0, "b": 5.0, "c": 99_999.0}, now)
        stop_at = now - QUIET_WINDOW_S
        got = latest_session_activity_ts(tmp_path / "p", stop_at=stop_at)
        # Whatever it returns is inside the window — that settles "not quiet",
        # which is the only thing either caller asks.
        assert got is not None and got >= stop_at

    def test_without_a_cutoff_it_still_returns_the_true_newest(self, tmp_path):
        now = time.time()
        transcripts(tmp_path / "p", {"old": 5_000.0, "new": 1.0}, now)
        got = latest_session_activity_ts(tmp_path / "p")
        assert got == max(
            os.stat(tmp_path / "p" / n).st_mtime for n in ("old.jsonl", "new.jsonl")
        )

    def test_all_old_transcripts_need_the_full_scan_and_report_the_newest(self, tmp_path):
        now = time.time()
        transcripts(tmp_path / "p", {"a": 9_000.0, "b": 8_000.0}, now)
        got = latest_session_activity_ts(tmp_path / "p", stop_at=now - QUIET_WINDOW_S)
        assert got == os.stat(tmp_path / "p" / "b.jsonl").st_mtime

    def test_no_transcripts_reads_as_no_activity(self, tmp_path):
        assert latest_session_activity_ts(tmp_path / "missing") is None

    def test_an_unreadable_entry_never_raises(self, tmp_path):
        now = time.time()
        transcripts(tmp_path / "p", {"a": 3.0}, now)
        (tmp_path / "p" / "not-a-transcript.txt").write_text("x")
        assert latest_session_activity_ts(tmp_path / "p") is not None


class TestScanCache:
    def _engine(self, temp_home):
        return _harness(temp_home, live=1, strategy="best", threshold=97.0)

    def test_one_scan_serves_the_whole_tick(self, temp_home):
        h = self._engine(temp_home)
        calls: list[float | None] = []

        def counting(_dir, *, stop_at=None):
            calls.append(stop_at)
            return None

        import claude_swap.autoswitch as mod

        mod.latest_session_activity_ts, original = counting, mod.latest_session_activity_ts
        try:
            h.engine._latest_session_activity()
            h.engine._latest_session_activity()
            h.engine._latest_session_activity()
            assert len(calls) == 1  # the other two were served from the cache
            assert calls[0] is not None  # and it passed the early-exit cutoff
            h.clock.advance(SESSION_SCAN_CACHE_S + 1)
            h.engine._latest_session_activity()
            assert len(calls) == 2  # cache expired: scanned again
        finally:
            mod.latest_session_activity_ts = original

    def test_the_quiet_verdict_is_unchanged_by_caching(self, temp_home):
        h = self._engine(temp_home)
        import claude_swap.autoswitch as mod

        fixed = h.clock.now - (QUIET_WINDOW_S / 2)  # activity inside the window
        original = mod.latest_session_activity_ts
        mod.latest_session_activity_ts = lambda _d, *, stop_at=None: fixed
        try:
            quiet, detail = h.engine._session_quiet()
            assert quiet is False
            assert "waits" in detail
        finally:
            mod.latest_session_activity_ts = original

    def test_force_bypasses_the_cache(self, temp_home):
        """The re-measure inside _perform exists to catch a session that woke
        up between the gate and the swap. A cached answer is, by definition,
        from before that window — so `force` must reach the disk.

        This is not hypothetical: caching without it failed
        test_perform_rechecks_quiet_under_lock, i.e. the swap went through
        while a session was writing.
        """
        h = self._engine(temp_home)
        calls: list[float | None] = []
        import claude_swap.autoswitch as mod

        original = mod.latest_session_activity_ts

        def counting(_dir, *, stop_at=None):
            calls.append(stop_at)
            return None

        mod.latest_session_activity_ts = counting
        try:
            h.engine._latest_session_activity()
            h.engine._latest_session_activity()
            assert len(calls) == 1
            h.engine._latest_session_activity(force=True)
            assert len(calls) == 2
            h.engine._session_quiet(force=True)
            assert len(calls) == 3
        finally:
            mod.latest_session_activity_ts = original


class TestEarlyExitVerdict:
    def test_a_slow_early_exit_scan_still_reads_as_active(self, temp_home):
        """review 2026-10-05: the scan stops at the FIRST file inside the window,
        which may be the oldest one there; aged by the scan's own duration it
        flipped the verdict to quiet while sessions were writing."""
        h = _harness(temp_home, live=1, strategy="best", threshold=97.0)
        import claude_swap.autoswitch as mod

        def slow_scan(_d, *, stop_at=None):
            h.clock.advance(20)          # a 20 s walk on a loaded tree
            return stop_at               # the oldest write still inside the window

        original = mod.latest_session_activity_ts
        mod.latest_session_activity_ts = slow_scan
        try:
            quiet, _ = h.engine._session_quiet(force=True)
        finally:
            mod.latest_session_activity_ts = original
        assert quiet is False


def test_the_scan_stops_at_the_first_recent_transcript(tmp_path, monkeypatch):
    """review 2026-10-05: the old early-exit test passed for a full scan too.
    Here a second directory blows up if the walk ever reaches it."""
    import claude_swap.autoswitch as mod

    now = time.time()
    first = tmp_path / "a"
    transcripts(first, {"recent": 10}, now)

    def walk(_root):
        yield str(first), [], ["recent.jsonl"]
        raise AssertionError("walked past a transcript inside the window")

    monkeypatch.setattr(mod.os, "walk", walk)
    assert latest_session_activity_ts(tmp_path, stop_at=now - QUIET_WINDOW_S) is not None
