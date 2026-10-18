"""Medium: Cursor CLI login rotation (cswap cursor), on a temp cursor home.

Origin 2026-10-18: the owner wants cursor-agent to move to another account when
the monthly plan usage runs out, like cswap does for Codex and agy.
"""

from __future__ import annotations

import base64
import json

import pytest

from claude_swap import cursor
from claude_swap.cursor import Usage


def _jwt(sub: str, exp: float = 4102444800) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"sub": sub, "exp": exp}).encode()).decode().rstrip("=")
    return f"h.{body}.s"


def _login(email: str, sub: str | None = None, refresh: str = "r") -> dict:
    sub = sub or f"auth0|{email}"
    return {"accessToken": _jwt(sub), "refreshToken": refresh,
            "authInfo": {"email": email, "displayName": email, "userId": 1, "authId": sub}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "cursor"
    home.mkdir()
    monkeypatch.setenv("CSWAP_CURSOR_HOME", str(home))   # also keeps the keychain out
    return tmp_path / "backup"


def _live(login: dict, extra: dict | None = None) -> None:
    cursor.auth_file().write_text(json.dumps({"accessToken": login["accessToken"],
                                              "refreshToken": login["refreshToken"]}))
    cursor.config_file().write_text(json.dumps({**(extra or {}), "authInfo": login["authInfo"]}))


def _two(env) -> None:
    _live(_login("b@x.com"))
    cursor.add(env)
    _live(_login("a@x.com"), {"model": "auto"})
    cursor.add(env)


def test_half_written_login_is_not_named():
    """Bug caught: tokens of one user stored under the authInfo email of another."""
    login = _login("a@x.com")
    login["authInfo"]["authId"] = "auth0|someone-else"
    assert cursor.email_of(login) is None


@pytest.mark.parametrize(
    ("payload", "pct", "reset", "error"),
    [
        pytest.param({"billingCycleEnd": "1793370023000", "planUsage": {"totalPercentUsed": 42}},
                     42.0, 1793370023.0, None, id="ms-cycle-end"),
        pytest.param({"planUsage": {"totalPercentUsed": True}}, None, None,
                     "no plan usage in the reply", id="bool-is-not-a-percent"),
        pytest.param({}, None, None, "no plan usage in the reply", id="empty-reply"),
    ],
)
def test_parse_usage(payload, pct, reset, error):
    """Bug caught: the cycle end read as seconds, or a reply without usage counted as 0%."""
    usage = cursor.parse_usage(payload)
    assert (usage.pct, usage.reset_at, usage.error) == (pct, reset, error)


def test_expired_token_is_unknown_without_calling_out():
    """Bug caught: an expired token read as an account with room."""
    login = _login("a@x.com")
    login["accessToken"] = _jwt("auth0|a@x.com", exp=1)
    assert cursor.fetch_usage(login).error.startswith("access token expired")


def test_rank_prefers_soonest_cycle_end_among_accounts_with_room():
    """Bug caught: moving to an exhausted or unreadable account, or wasting quota
    that expires first."""
    usages = {
        "live@x.com": Usage(99.0, 100.0),
        "late@x.com": Usage(10.0, 900.0),
        "soon@x.com": Usage(50.0, 500.0),
        "empty@x.com": Usage(95.0, 10.0),
        "tiny@x.com": Usage(93.0, 1.0),
        "broken@x.com": Usage(None, None, error="http-401"),
    }
    assert cursor.rank(usages, "live@x.com", 95.0) == ["soon@x.com", "late@x.com", "tiny@x.com"]


def test_switch_swaps_tokens_and_identity_and_keeps_settings(env):
    """Bug caught: cli-config.json settings lost, or the old account's refreshed
    tokens dropped on a switch."""
    _two(env)
    _live(_login("a@x.com", refresh="r-refreshed"), {"model": "auto"})
    assert cursor.switch(env, "b@x.com") == "a@x.com"
    assert cursor.email_of(cursor.read_live()) == "b@x.com"
    assert json.loads(cursor.config_file().read_text())["model"] == "auto"
    stored_a = json.loads(cursor.stored_path(env, "a@x.com").read_text())
    assert stored_a["refreshToken"] == "r-refreshed"


def test_switch_refuses_when_the_live_login_is_not_stored(env):
    """Bug caught: a switch overwriting the only copy of a login."""
    _two(env)
    _live(_login("new@x.com"))
    with pytest.raises(cursor.CursorError, match="not stored"):
        cursor.switch(env, "b@x.com")
    assert cursor.email_of(cursor.read_live()) == "new@x.com"


@pytest.mark.parametrize(
    ("live_pct", "other_pct", "event", "to"),
    [
        pytest.param(50.0, 0.0, "cursor-no-switch", None, id="below-threshold"),
        pytest.param(100.0, 10.0, "cursor-switch", "b@x.com", id="exhausted-moves"),
        pytest.param(100.0, 100.0, "cursor-no-switch", None, id="nowhere-to-go"),
    ],
)
def test_auto_tick(env, live_pct, other_pct, event, to):
    """Bug caught: switching below the threshold or onto another exhausted account."""
    _two(env)

    def usages(_root, emails, current):
        return {e: Usage(live_pct if e == current else other_pct, 100.0) for e in emails}

    out = cursor.auto_tick(env, 95.0, usages_fn=usages)
    assert (out["event"], out.get("to")) == (event, to)
    assert cursor.email_of(cursor.read_live()) == (to or "a@x.com")


def test_failed_login_puts_the_previous_one_back(env, monkeypatch):
    """Bug caught: an aborted `cursor-agent login` leaving the machine logged out."""
    _live(_login("a@x.com"))
    monkeypatch.setattr(cursor.shutil, "which", lambda _name: "/bin/cursor-agent")
    monkeypatch.setattr(cursor.subprocess, "call", lambda _argv: 1)
    with pytest.raises(cursor.CursorError, match="previous one is back"):
        cursor.login(env)
    assert cursor.email_of(cursor.read_live()) == "a@x.com"


def test_remove_refuses_the_live_login(env):
    """Bug caught: forgetting the account cursor-agent is using right now."""
    _two(env)
    with pytest.raises(cursor.CursorError, match="live Cursor login"):
        cursor.remove(env, "a@x.com")
    cursor.remove(env, "b@x.com")
    assert cursor.stored_accounts(env) == ["a@x.com"]


def test_foreign_files_in_the_store_are_not_accounts(env):
    """Bug caught: a stray JSON in the store counted as a rotation target."""
    _two(env)
    (cursor.store_dir(env) / "notes.json").write_text("{}")
    (cursor.store_dir(env) / "c@x.com.json").write_text(json.dumps(_login("d@x.com")))
    assert cursor.stored_accounts(env) == ["a@x.com", "b@x.com"]
