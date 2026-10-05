"""Medium: `cswap list` shows slots in the order the auto-switch will use them."""

from __future__ import annotations

from claude_swap.settings import set_setting
from claude_swap.usage_store import UsageEntry
from tests.test_autoswitch import EngineHarness, _usage, _usage7


def _row(num, email, active=False):
    return (num, email, "", "", active, "", "")


def test_active_then_most_life_then_reserve_then_unusable(temp_home):
    h = EngineHarness(temp_home)
    for n, e in [(1, "home@x.com"), (2, "a@x.com"), (3, "b@x.com"), (4, "res@x.com"), (5, "dead@x.com")]:
        h.seed(n, e)
    h.make_live("a@x.com", 2)
    root = h.switcher.backup_dir
    set_setting(root, "autoswitch.homeAccount", "home@x.com")
    set_setting(root, "autoswitch.homeMode", "prefer")
    set_setting(root, "autoswitch.reserveAccount", "res@x.com")
    rows = [_row(1, "home@x.com"), _row(2, "a@x.com", True), _row(3, "b@x.com"),
            _row(4, "res@x.com"), _row(5, "dead@x.com")]
    entries = {
        "1": UsageEntry(last_good=_usage(90)),   # home, 10% life: below the return margin
        "2": UsageEntry(last_good=_usage(50)),
        "3": UsageEntry(last_good=_usage(20)),   # 80% life
        "4": UsageEntry(last_good=_usage7(0, 85)),  # reserve, 15% of its week: held
        "5": UsageEntry(sentinel="re-login needed"),
    }
    ordered, notes = h.switcher._in_switch_order(rows, entries, h.switcher._get_sequence_data())
    assert [r[0] for r in ordered] == [2, 3, 1, 4, 5]
    assert notes["3"] == "(next)"
    assert notes["4"].startswith("(reserve — held")
    assert notes["5"] == "(not usable now)"


def test_home_with_room_comes_first(temp_home):
    h = EngineHarness(temp_home)
    for n, e in [(1, "home@x.com"), (2, "a@x.com"), (3, "b@x.com")]:
        h.seed(n, e)
    h.make_live("a@x.com", 2)
    root = h.switcher.backup_dir
    set_setting(root, "autoswitch.homeAccount", "home@x.com")
    set_setting(root, "autoswitch.homeMode", "prefer")
    rows = [_row(1, "home@x.com"), _row(2, "a@x.com", True), _row(3, "b@x.com")]
    entries = {"1": UsageEntry(last_good=_usage(60)), "2": UsageEntry(last_good=_usage(50)),
               "3": UsageEntry(last_good=_usage(0))}
    ordered, _ = h.switcher._in_switch_order(rows, entries, h.switcher._get_sequence_data())
    assert [r[0] for r in ordered] == [2, 1, 3]


def test_without_a_home_the_list_follows_best_most_headroom(temp_home):
    """review 2026-10-05: the engine ranks by soonest reset only with a prefer
    home (or consume-first); plain `best` takes the most headroom."""
    from tests.test_autoswitch import _R_LATER, _R_SOON, _usage7
    h = EngineHarness(temp_home)
    for n, e in [(1, "a@x.com"), (2, "roomy@x.com"), (3, "soon@x.com")]:
        h.seed(n, e)
    h.make_live("a@x.com", 1)
    rows = [_row(1, "a@x.com", True), _row(2, "roomy@x.com"), _row(3, "soon@x.com")]
    entries = {
        "1": UsageEntry(last_good=_usage(50)),
        "2": UsageEntry(last_good=_usage7(5, 20, _R_LATER)),   # 80% room, resets later
        "3": UsageEntry(last_good=_usage7(5, 60, _R_SOON)),    # 40% room, resets sooner
    }
    ordered, notes = h.switcher._in_switch_order(rows, entries, h.switcher._get_sequence_data())
    assert [r[0] for r in ordered] == [1, 2, 3]
    assert notes["2"] == "(next)"
