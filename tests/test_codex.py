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
        assert codex.rank(usages, "active@x.com", 95.0, 99.0) == [
            "soon@x.com", "late@x.com", "thin@x.com", "blind@x.com",
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
