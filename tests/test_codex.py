"""Medium: Codex CLI login rotation (cswap codex), on a temp CODEX_HOME.

Origin 2026-09-29: a second `codex login` overwrote the only copy of a Pro
login — Codex keeps one account in ~/.codex/auth.json.
"""

from __future__ import annotations

import base64
import json
import os
import stat

import pytest

from claude_swap import codex
from claude_swap.codex import Usage


def _jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"h.{body}.s"


def _auth(email: str, refresh: str = "r0") -> dict:
    return {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": _jwt({
                "email": email,
                "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"},
            }),
            "access_token": "a",
            "refresh_token": refresh,
            "account_id": "acct",
        },
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    return tmp_path / "backup"


def _login(auth: dict) -> None:
    codex.live_auth_path().write_text(json.dumps(auth))


def _usage(weekly: float, reset: float | None = 1000.0, error: str | None = None) -> Usage:
    return Usage(weekly, reset, None, None, weekly >= 100, error=error)


class TestStore:
    def test_add_stores_a_private_copy_keyed_by_email(self, env):
        _login(_auth("A@x.com"))
        email, plan = codex.add(env)
        assert (email, plan) == ("a@x.com", "pro")
        path = codex.stored_path(env, "a@x.com")
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert codex.stored_accounts(env) == ["a@x.com"]

    def test_add_refuses_an_api_key_login(self, env):
        _login({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-x", "tokens": None})
        with pytest.raises(codex.CodexError):
            codex.add(env)

    def test_the_live_login_overrides_its_stored_copy(self, env):
        _login(_auth("a@x.com", refresh="r0"))
        codex.add(env)
        _login(_auth("a@x.com", refresh="r1"))  # Codex rotated the token
        assert codex.sync_live(env) == "a@x.com"
        stored = json.loads(codex.stored_path(env, "a@x.com").read_text())
        assert stored["tokens"]["refresh_token"] == "r1"

    def test_an_unstored_live_login_is_not_adopted_silently(self, env):
        _login(_auth("stray@x.com"))
        assert codex.sync_live(env) == "stray@x.com"
        assert codex.stored_accounts(env) == []


class TestSwitch:
    def test_switch_saves_the_rotated_live_token_before_replacing_it(self, env):
        _login(_auth("b@x.com"))
        codex.add(env)
        _login(_auth("a@x.com", refresh="r0"))
        codex.add(env)
        _login(_auth("a@x.com", refresh="r9"))  # rotated since add
        assert codex.switch(env, "b@x.com") == "a@x.com"
        assert codex.email_of(codex.read_auth(codex.live_auth_path())) == "b@x.com"
        kept = json.loads(codex.stored_path(env, "a@x.com").read_text())
        assert kept["tokens"]["refresh_token"] == "r9"

    def test_switch_to_an_unknown_account_fails(self, env):
        _login(_auth("a@x.com"))
        codex.add(env)
        with pytest.raises(codex.CodexError):
            codex.switch(env, "nobody@x.com")


class TestRank:
    def test_soonest_reset_with_real_room_first_then_low_then_unknown(self):
        usages = {
            "active@x.com": _usage(99),
            "late@x.com": _usage(10, reset=5000),
            "soon@x.com": _usage(50, reset=2000),
            "thin@x.com": _usage(95, reset=100),     # 4 points before 99: too thin
            "dead@x.com": _usage(100, reset=50),     # exhausted: never
            "blind@x.com": _usage(0, error="http 500"),
        }
        # blind@ is unreadable: never a target (review 2026-10-05, BEHAV-1).
        assert codex.rank(usages, "active@x.com", 95.0, 99.0) == [
            "soon@x.com", "late@x.com", "thin@x.com",
        ]


class TestParse:
    def test_weekly_and_short_windows_are_told_apart_by_length(self):
        u = codex.parse_usage({"rate_limit": {
            "limit_reached": False,
            "primary_window": {"used_percent": 42, "limit_window_seconds": 604800, "reset_at": 7},
            "secondary_window": {"used_percent": 80, "limit_window_seconds": 18000, "reset_at": 3},
        }})
        assert (u.weekly_pct, u.weekly_reset_at, u.short_pct, u.short_reset_at) == (42, 7, 80, 3)
        assert u.over(95.0, 99.0) is False
        assert u.life(95.0, 99.0) == 15.0


class TestAutoTick:
    def _two(self, env):
        _login(_auth("b@x.com"))
        codex.add(env)
        _login(_auth("a@x.com"))
        codex.add(env)

    def test_below_the_weekly_threshold_stays(self, env, monkeypatch):
        self._two(env)
        monkeypatch.setattr(codex, "fetch_usage", lambda auth, timeout=20.0: _usage(97))
        event = codex.auto_tick(env, 95.0, 99.0)
        assert event["reason"] == "below-threshold"
        assert codex.email_of(codex.read_auth(codex.live_auth_path())) == "a@x.com"

    def test_at_the_weekly_threshold_switches(self, env, monkeypatch):
        self._two(env)
        by_email = {"a@x.com": _usage(99), "b@x.com": _usage(20)}
        monkeypatch.setattr(
            codex, "fetch_usage",
            lambda auth, timeout=20.0: by_email[codex.email_of(auth)],
        )
        event = codex.auto_tick(env, 95.0, 99.0)
        assert (event["event"], event["to"]) == ("codex-switch", "b@x.com")
        assert codex.email_of(codex.read_auth(codex.live_auth_path())) == "b@x.com"

    def test_unknown_active_usage_never_switches(self, env, monkeypatch):
        self._two(env)
        monkeypatch.setattr(codex, "fetch_usage", lambda auth, timeout=20.0: _usage(0, error="URLError"))
        assert codex.auto_tick(env, 95.0, 99.0)["reason"] == "active-usage-unknown"


class TestReserve:
    def test_the_reserve_comes_last_even_with_the_most_room(self):
        usages = {
            "active@x.com": _usage(99),
            "res@x.com": _usage(0, reset=10),      # soonest reset and empty: still last
            "late@x.com": _usage(60, reset=5000),
            "thin@x.com": _usage(95, reset=100),   # too thin, but ordinary: before the reserve
        }
        assert codex.rank(usages, "active@x.com", 95.0, 99.0, "RES@x.com", 30.0) == [
            "late@x.com", "thin@x.com", "res@x.com",
        ]

    def test_a_reserve_under_30pct_of_its_week_drops_out(self):
        usages = {"active@x.com": _usage(99), "res@x.com": _usage(71)}
        assert codex.rank(usages, "active@x.com", 95.0, 99.0, "res@x.com", 30.0) == []
        usages["res@x.com"] = _usage(70)
        assert codex.rank(usages, "active@x.com", 95.0, 99.0, "res@x.com", 30.0) == ["res@x.com"]

    def test_an_active_reserve_is_left_once_its_floor_is_crossed(self, env, monkeypatch):
        _login(_auth("b@x.com"))
        codex.add(env)
        _login(_auth("res@x.com"))
        codex.add(env)
        by_email = {"res@x.com": _usage(75), "b@x.com": _usage(20)}
        monkeypatch.setattr(codex, "fetch_usage", lambda auth, timeout=20.0: by_email[codex.email_of(auth)])
        event = codex.auto_tick(env, 95.0, 99.0, reserve="res@x.com", reserve_min_life=30.0)
        assert (event["event"], event["to"]) == ("codex-switch", "b@x.com")


class TestLogin:
    def test_login_runs_in_a_throwaway_home_and_leaves_the_live_login(self, env, monkeypatch):
        _login(_auth("live@x.com", refresh="keep"))
        codex.add(env)
        seen = {}

        def fake_call(argv, env):
            seen["argv"], seen["home"] = argv, env["CODEX_HOME"]
            with open(os.path.join(env["CODEX_HOME"], "auth.json"), "w") as fh:
                json.dump(_auth("new@x.com"), fh)
            return 0

        monkeypatch.setattr(codex.shutil, "which", lambda name: "/bin/codex")
        monkeypatch.setattr(codex.subprocess, "call", fake_call)
        assert codex.login(env) == ("new@x.com", "pro")
        assert seen["argv"] == ["codex", "login", "--device-auth"]
        assert seen["home"] != str(codex.codex_home())
        assert not os.path.exists(seen["home"])           # cleaned up
        live = codex.read_auth(codex.live_auth_path())
        assert codex.email_of(live) == "live@x.com" and live["tokens"]["refresh_token"] == "keep"
        assert codex.stored_accounts(env) == ["live@x.com", "new@x.com"]

    def test_an_aborted_login_stores_nothing(self, env, monkeypatch):
        monkeypatch.setattr(codex.shutil, "which", lambda name: "/bin/codex")
        monkeypatch.setattr(codex.subprocess, "call", lambda argv, env: 1)
        with pytest.raises(codex.CodexError):
            codex.login(env)
        assert codex.stored_accounts(env) == []


class TestAfterSwitch:
    def _two(self, env, monkeypatch):
        _login(_auth("b@x.com"))
        codex.add(env)
        _login(_auth("a@x.com"))
        codex.add(env)
        by_email = {"a@x.com": _usage(99), "b@x.com": _usage(20)}
        monkeypatch.setattr(codex, "fetch_usage", lambda auth, timeout=20.0: by_email[codex.email_of(auth)])

    def test_a_real_switch_starts_the_hook_with_both_accounts(self, env, monkeypatch):
        """Running Codex keeps the old login in memory: the hook moves them."""
        self._two(env, monkeypatch)
        started = []
        monkeypatch.setattr(
            codex.subprocess, "Popen",
            lambda cmd, **kw: started.append((cmd, kw["env"]["CSWAP_CODEX_FROM"], kw["env"]["CSWAP_CODEX_TO"])),
        )
        event = codex.auto_tick(env, 95.0, 99.0, after_switch="restart-codex")
        assert event["afterSwitch"] == "started"
        assert started == [("restart-codex", "a@x.com", "b@x.com")]

    def test_a_dry_run_never_starts_it(self, env, monkeypatch):
        self._two(env, monkeypatch)
        monkeypatch.setattr(codex.subprocess, "Popen", lambda *a, **kw: pytest.fail("hook ran"))
        event = codex.auto_tick(env, 95.0, 99.0, after_switch="restart-codex", dry_run=True)
        assert "afterSwitch" not in event

    def test_a_hook_that_cannot_start_is_reported_not_raised(self, env, monkeypatch):
        self._two(env, monkeypatch)

        def boom(*a, **kw):
            raise FileNotFoundError("sh")

        monkeypatch.setattr(codex.subprocess, "Popen", boom)
        event = codex.auto_tick(env, 95.0, 99.0, after_switch="restart-codex")
        assert event["event"] == "codex-switch" and event["afterSwitch"].startswith("failed")


def _cr(weekly: float, credits: float | None, usable: bool = True) -> Usage:
    return Usage(weekly, 1000.0, None, None, weekly >= 100, credits_balance=credits, credits_usable=usable)


class TestCredits:
    """Owner, 2026-10-04: credits must be spent by year end anyway — once no
    account has quota (the reserve included), run on the biggest balance."""

    def _three(self, env, monkeypatch, by_email):
        for e in ("c@x.com", "b@x.com", "a@x.com"):
            _login(_auth(e))
            codex.add(env)
        monkeypatch.setattr(codex, "fetch_usage", lambda auth, timeout=20.0: by_email[codex.email_of(auth)])
        monkeypatch.setattr(codex.subprocess, "Popen", lambda *a, **kw: None)

    def _live(self):
        return codex.email_of(codex.read_auth(codex.live_auth_path()))

    def test_no_quota_anywhere_lands_on_the_biggest_balance_reserve_included(self, env, monkeypatch):
        by = {"a@x.com": _cr(100, 30000), "b@x.com": _cr(100, 34000), "c@x.com": _cr(100, 61000)}
        self._three(env, monkeypatch, by)
        event = codex.auto_tick(env, 95.0, 99.0, reserve="c@x.com", reserve_min_life=30.0)
        assert (event["to"], event["mode"]) == ("c@x.com", "credits")
        assert self._live() == "c@x.com"

    def test_it_stays_on_its_credits_account_while_balances_shift(self, env, monkeypatch):
        """Every switch restarts the daemon: no re-picking the biggest each tick."""
        by = {"a@x.com": _cr(100, 30000), "b@x.com": _cr(100, 34000), "c@x.com": _cr(100, 61000)}
        self._three(env, monkeypatch, by)
        codex.auto_tick(env, 95.0, 99.0)
        by["c@x.com"] = _cr(100, 20000)          # drained below b — still stays
        assert codex.auto_tick(env, 95.0, 99.0)["reason"] == "on-credits"
        assert self._live() == "c@x.com"

    def test_an_empty_credits_account_hands_over_to_the_next_biggest(self, env, monkeypatch):
        by = {"a@x.com": _cr(100, 30000), "b@x.com": _cr(100, 34000), "c@x.com": _cr(100, 61000)}
        self._three(env, monkeypatch, by)
        codex.auto_tick(env, 95.0, 99.0)
        by["c@x.com"] = _cr(100, 10.0)
        event = codex.auto_tick(env, 95.0, 99.0)
        assert (event["to"], event["mode"]) == ("b@x.com", "credits")

    def test_a_spend_cap_takes_an_account_out_of_the_credits_pool(self, env, monkeypatch):
        by = {"a@x.com": _cr(100, 30000), "b@x.com": _cr(100, 34000), "c@x.com": _cr(100, 61000, usable=False)}
        self._three(env, monkeypatch, by)
        assert codex.auto_tick(env, 95.0, 99.0)["to"] == "b@x.com"

    def test_a_week_that_resets_brings_it_back_off_credits(self, env, monkeypatch):
        by = {"a@x.com": _cr(100, 30000), "b@x.com": _cr(100, 34000), "c@x.com": _cr(100, 61000)}
        self._three(env, monkeypatch, by)
        codex.auto_tick(env, 95.0, 99.0)
        by["a@x.com"] = _cr(0, 30000)            # a's week reset
        event = codex.auto_tick(env, 95.0, 99.0)
        assert (event["to"], event["mode"]) == ("a@x.com", "quota")
        assert codex._credits_account(env) is None

    def test_staying_put_when_the_live_account_already_has_the_most(self, env, monkeypatch):
        by = {"a@x.com": _cr(100, 90000), "b@x.com": _cr(100, 34000), "c@x.com": _cr(100, 61000)}
        self._three(env, monkeypatch, by)
        event = codex.auto_tick(env, 95.0, 99.0)
        assert event["reason"] == "on-credits" and self._live() == "a@x.com"
        assert codex._credits_account(env) == "a@x.com"

    def test_parse_reads_balance_and_spend_cap(self):
        u = codex.parse_usage({
            "rate_limit": {"primary_window": {"used_percent": 100, "limit_window_seconds": 604800}},
            "credits": {"has_credits": True, "balance": "32041.65", "overage_limit_reached": False},
            "spend_control": {"reached": True},
        })
        assert u.credits_balance == 32041.65 and u.credits_usable is False


class TestReviewFixes:
    """zuvo:review 2026-10-05 on the codex rotation."""

    def _accounts(self, env, monkeypatch, by_email, calls=None):
        for e in sorted(by_email, reverse=True):
            _login(_auth(e))
            codex.add(env)
        _login(_auth("a@x.com"))

        def fetch(auth, timeout=20.0):
            if calls is not None:
                calls.append(codex.email_of(auth))
            return by_email[codex.email_of(auth)]

        monkeypatch.setattr(codex, "fetch_usage", fetch)
        monkeypatch.setattr(codex.subprocess, "Popen", lambda *a, **kw: None)

    def test_a_revoked_account_is_never_switched_to_credits_win_instead(self, env, monkeypatch):
        """A revoked login used to outrank credits; the daemon then sat on it
        forever answering active-usage-unknown."""
        dead = Usage(None, None, None, None, False, error="login revoked — log in again: cswap codex login")
        self._accounts(env, monkeypatch, {"a@x.com": _cr(100, 30000), "b@x.com": dead})
        event = codex.auto_tick(env, 95.0, 99.0)
        assert event["reason"] == "on-credits"
        assert codex.email_of(codex.read_auth(codex.live_auth_path())) == "a@x.com"

    def test_the_credits_pin_goes_when_the_week_resets(self, env, monkeypatch):
        by = {"a@x.com": _cr(100, 30000), "b@x.com": _cr(100, 100)}
        self._accounts(env, monkeypatch, by)
        codex.auto_tick(env, 95.0, 99.0)
        assert codex._credits_account(env) == "a@x.com"
        by["a@x.com"] = _cr(3, 30000)               # a's week reset: on quota again
        assert codex.auto_tick(env, 95.0, 99.0)["reason"] == "below-threshold"
        assert codex._credits_account(env) is None

    def test_usage_is_fetched_once_per_account_per_tick(self, env, monkeypatch):
        calls: list[str] = []
        self._accounts(env, monkeypatch, {"a@x.com": _cr(100, 30000), "b@x.com": _cr(100, 9000),
                                          "c@x.com": _cr(100, 50000)}, calls)
        codex.auto_tick(env, 95.0, 99.0)
        assert sorted(calls) == ["a@x.com", "b@x.com", "c@x.com"]

    def test_re_logging_the_live_account_replaces_the_live_tokens(self, env, monkeypatch):
        """Otherwise the next sync copies the old (revoked) live tokens back."""
        _login(_auth("a@x.com", refresh="old"))
        codex.add(env)

        def fake_call(argv, env):
            with open(os.path.join(env["CODEX_HOME"], "auth.json"), "w") as fh:
                json.dump(_auth("a@x.com", refresh="new"), fh)
            return 0

        monkeypatch.setattr(codex.shutil, "which", lambda name: "/bin/codex")
        monkeypatch.setattr(codex.subprocess, "call", fake_call)
        codex.login(env)
        assert codex.read_auth(codex.live_auth_path())["tokens"]["refresh_token"] == "new"
        codex.sync_live(env)
        assert codex.read_auth(codex.stored_path(env, "a@x.com"))["tokens"]["refresh_token"] == "new"

    @pytest.mark.parametrize("name", ["../x", "a/b@x.com", ".hidden", ""])
    def test_an_account_name_cannot_leave_the_store(self, env, name):
        with pytest.raises(codex.CodexError):
            codex.stored_path(env, name)


class TestReviewPass2:
    """Second round of zuvo:review 2026-10-05 findings."""

    def test_an_expired_token_stays_a_candidate_after_readable_ones(self):
        """An idle account's access token expires (~10 days); Codex refreshes it
        on use, so excluding it would retire healthy accounts for good."""
        expired = Usage(None, None, None, None, False, error="token expired — refreshes on the next switch to it")
        revoked = Usage(None, None, None, None, False, error="login revoked — log in again: cswap codex login")
        usages = {"active@x.com": _usage(99), "idle@x.com": expired, "dead@x.com": revoked,
                  "ok@x.com": _usage(40)}
        assert codex.rank(usages, "active@x.com", 95.0, 99.0) == ["ok@x.com", "idle@x.com"]

    def test_switch_refuses_to_overwrite_an_unstored_live_login(self, env):
        _login(_auth("b@x.com"))
        codex.add(env)
        _login(_auth("stray@x.com"))           # logged in by hand, never stored
        with pytest.raises(codex.CodexError, match="not stored"):
            codex.switch(env, "b@x.com")
        assert codex.email_of(codex.read_auth(codex.live_auth_path())) == "stray@x.com"

    def test_switch_refuses_a_stored_copy_of_another_account(self, env):
        _login(_auth("b@x.com"))
        codex.add(env)
        codex.write_private(codex.stored_path(env, "c@x.com"), _auth("b@x.com"))
        with pytest.raises(codex.CodexError, match="belongs to"):
            codex.switch(env, "c@x.com")


class TestFetchUsageErrors:
    """rank() keys on these strings: a revoked login must never read as expired."""

    def _http(self, monkeypatch, code, body):
        import io
        import urllib.error

        def boom(*a, **kw):
            raise urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body))

        monkeypatch.setattr(codex.urllib.request, "urlopen", boom)

    def test_a_revoked_token_is_not_refreshable(self, monkeypatch):
        self._http(monkeypatch, 401, b'{"error":{"code":"token_revoked"}}')
        u = codex.fetch_usage(_auth("a@x.com"))
        assert u.error.startswith("login revoked") and not u.refreshable()

    def test_an_expired_token_is_refreshable(self, monkeypatch):
        self._http(monkeypatch, 401, b'{"error":{"code":"token_expired"}}')
        assert codex.fetch_usage(_auth("a@x.com")).refreshable()

    def test_other_http_errors_are_neither(self, monkeypatch):
        self._http(monkeypatch, 500, b"oops")
        u = codex.fetch_usage(_auth("a@x.com"))
        assert u.error == "http 500" and not u.refreshable()

    def test_a_network_error_is_reported_not_raised(self, monkeypatch):
        def down(*a, **kw):
            raise OSError("no route")
        monkeypatch.setattr(codex.urllib.request, "urlopen", down)
        assert codex.fetch_usage(_auth("a@x.com")).error == "OSError"


class TestCodexListJson:
    def test_json_marks_held_reserve_and_unreadable_accounts(self, env, monkeypatch, capsys):
        from claude_swap import cli, paths
        from claude_swap.settings import set_setting
        for e in ("res@x.com", "dead@x.com", "b@x.com", "a@x.com"):
            _login(_auth(e))
            codex.add(env)
        _login(_auth("a@x.com"))
        set_setting(env, "autoswitch.codexReserveAccount", "res@x.com")
        set_setting(env, "autoswitch.codexReserveMinLifePct", "30")
        by = {"a@x.com": _usage(50), "b@x.com": _usage(20), "res@x.com": _usage(80),
              "dead@x.com": Usage(None, None, None, None, False, error="login revoked — log in again: cswap codex login")}
        monkeypatch.setattr(codex, "fetch_usage", lambda auth, timeout=20.0: by[codex.email_of(auth)])
        monkeypatch.setattr(paths, "get_backup_root", lambda: env)
        cli._codex_command(["list", "--json"])
        rows = {r["email"]: r for r in json.loads(capsys.readouterr().out)["accounts"]}
        assert rows["a@x.com"]["active"] and rows["b@x.com"]["next"]
        assert rows["res@x.com"]["held"] and not rows["res@x.com"]["exhausted"]
        assert not rows["dead@x.com"]["exhausted"] and rows["dead@x.com"]["error"].startswith("login revoked")


class TestReviewPass3:
    """Third round of zuvo:review 2026-10-05 findings."""

    def test_a_revoked_live_login_is_left_not_waited_on(self, env, monkeypatch):
        revoked = Usage(None, None, None, None, False, error="login revoked — log in again: cswap codex login")
        by = {"a@x.com": revoked, "b@x.com": _usage(20)}
        for e in ("b@x.com", "a@x.com"):
            _login(_auth(e))
            codex.add(env)
        monkeypatch.setattr(codex, "fetch_usage", lambda auth, timeout=20.0: by[codex.email_of(auth)])
        event = codex.auto_tick(env, 95.0, 99.0)
        assert (event["event"], event["to"]) == ("codex-switch", "b@x.com")

    def test_switch_never_replaces_an_api_key_login(self, env):
        _login(_auth("b@x.com"))
        codex.add(env)
        codex.live_auth_path().write_text(json.dumps({"OPENAI_API_KEY": "sk-test", "tokens": None}))
        with pytest.raises(codex.CodexError, match="API key"):
            codex.switch(env, "b@x.com")

    def test_unlimited_credits_count_without_a_balance(self):
        u = codex.parse_usage({
            "rate_limit": {"primary_window": {"used_percent": 100, "limit_window_seconds": 604800}},
            "credits": {"has_credits": True, "unlimited": True, "balance": None},
        })
        assert u.on_credits_ok()

    def test_the_more_used_of_two_weekly_windows_binds(self):
        u = codex.parse_usage({"rate_limit": {
            "primary_window": {"used_percent": 80, "limit_window_seconds": 604800, "reset_at": 7},
            "secondary_window": {"used_percent": 10, "limit_window_seconds": 604800, "reset_at": 9},
        }})
        assert u.weekly_pct == 80
