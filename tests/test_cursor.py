"""Medium: Cursor CLI login rotation (cswap cursor), on a temp cursor home.

Origin 2026-10-18: the owner wants cursor-agent to move to another account when
the monthly plan usage runs out, like cswap does for Codex and agy.
"""

from __future__ import annotations

import base64
import json
import time

import pytest

from claude_swap import cursor
from claude_swap.cursor import Usage


def _jwt(sub: str, exp: float = 4102444800) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"sub": sub, "exp": exp}).encode()).decode().rstrip("=")
    return f"h.{body}.s"


def _login(email: str, version: int = 0) -> dict:
    sub = f"auth0|{email}"
    return {"accessToken": _jwt(sub), "refreshToken": _jwt(sub, exp=4102444800 + version),
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


@pytest.mark.parametrize("piece", ["accessToken", "refreshToken"])
def test_half_written_login_is_not_named(piece):
    """Bug caught: tokens of one user stored under the authInfo email of another."""
    login = _login("a@x.com")
    login[piece] = _jwt("auth0|someone-else")
    assert cursor.email_of(login) is None


@pytest.mark.parametrize(
    ("payload", "pct", "reset", "error"),
    [
        pytest.param({"billingCycleEnd": "1793370023000", "planUsage": {"totalPercentUsed": 42}},
                     42.0, 1793370023.0, None, id="ms-cycle-end"),
        pytest.param({"billingCycleEnd": 1793370023, "planUsage": {"totalPercentUsed": 1}},
                     1.0, 1793370023.0, None, id="seconds-cycle-end"),
        pytest.param({"planUsage": {"limit": 40000}}, 0.0, None, None,
                     id="proto3-omits-zero-percent"),
        pytest.param({"planUsage": {"totalPercentUsed": True}}, None, None,
                     "no plan usage in the reply", id="bool-is-not-a-percent"),
        pytest.param({}, None, None, "no plan usage in the reply", id="empty-reply"),
    ],
)
def test_parse_usage(payload, pct, reset, error):
    """Bug caught: the cycle end read as seconds, or a reply without usage counted as 0%."""
    usage = cursor.parse_usage(payload)
    assert (usage.pct, usage.reset_at, usage.error) == (pct, reset, error)


def test_on_demand_cents_may_arrive_as_strings():
    """Bug caught: int64 cents sent as JSON strings dropped from the list."""
    usage = cursor.parse_usage({"planUsage": {"totalPercentUsed": 5},
                                "spendLimitUsage": {"individualUsed": "1250", "individualLimit": 2000}})
    assert (usage.on_demand_used, usage.on_demand_limit) == (12.5, 20.0)


def test_expired_token_is_unknown_without_calling_out(monkeypatch):
    """Bug caught: an expired token sent to the API, or read as an account with room."""
    def no_network(*_a, **_k):
        raise AssertionError("called the usage endpoint")

    monkeypatch.setattr(cursor.urllib.request, "urlopen", no_network)
    login = _login("a@x.com")
    login["accessToken"] = _jwt("auth0|a@x.com", exp=1)
    assert cursor.fetch_usage(login).error == cursor.EXPIRED


def test_rank_order():
    """Bug caught: moving to an exhausted or broken account, wasting the quota
    that expires first, or stranding the owner while idle accounts' tokens expired."""
    now = time.time()
    usages = {
        "live@x.com": Usage(99.0, now + 100),
        "late@x.com": Usage(10.0, now + 900),
        "soon@x.com": Usage(50.0, now + 500),
        "tiny@x.com": Usage(93.0, now + 50),          # soonest, but under 5% of room
        "idle@x.com": Usage(None, None, error=cursor.EXPIRED),
        "empty@x.com": Usage(95.0, now + 10),
        "broken@x.com": Usage(None, None, error="http-401"),
    }
    assert cursor.rank(usages, "live@x.com", 95.0) == [
        "soon@x.com", "late@x.com", "tiny@x.com", "idle@x.com"]


def test_switch_swaps_tokens_and_identity_and_keeps_settings(env):
    """Bug caught: cli-config.json settings lost, or the old account's refreshed
    tokens dropped on a switch."""
    _two(env)
    refreshed = _login("a@x.com", version=1)
    _live(refreshed, {"model": "auto"})
    assert cursor.switch(env, "b@x.com") == "a@x.com"
    assert cursor.read_live() == _login("b@x.com")
    assert json.loads(cursor.config_file().read_text())["model"] == "auto"
    stored_a = json.loads(cursor.stored_path(env, "a@x.com").read_text())
    assert stored_a["refreshToken"] == refreshed["refreshToken"]


def test_unparseable_config_stops_a_switch_before_the_tokens(env):
    """Bug caught: cli-config.json (the user's settings) replaced by authInfo alone,
    or new tokens left next to the old authInfo."""
    _two(env)
    cursor.config_file().write_text("{broken")
    with pytest.raises(cursor.CursorError, match="does not parse"):
        cursor.switch(env, "b@x.com")
    assert cursor.config_file().read_text() == "{broken"
    assert json.loads(cursor.auth_file().read_text())["accessToken"] == _login("a@x.com")["accessToken"]


def test_failed_config_write_puts_the_old_tokens_back(env, monkeypatch):
    """Bug caught: a switch failing half-way leaving tokens of B with authInfo of A."""
    _two(env)
    real = cursor._write_private

    def fail_on_config(path, data):
        if path == cursor.config_file() and data.get("authInfo", {}).get("email") == "b@x.com":
            raise OSError("disk full")
        real(path, data)

    monkeypatch.setattr(cursor, "_write_private", fail_on_config)
    with pytest.raises(OSError):
        cursor.switch(env, "b@x.com")
    assert cursor.read_live() == _login("a@x.com")


def test_switch_refuses_an_unidentifiable_live_login(env):
    """Bug caught: overwriting a live login nobody has a copy of because its
    pieces disagree."""
    _two(env)
    torn = _login("a@x.com")
    torn["accessToken"] = _jwt("auth0|someone-else")
    _live(torn)
    with pytest.raises(cursor.CursorError, match="cannot be identified"):
        cursor.switch(env, "b@x.com")
    assert cursor.read_live() == torn


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
        pytest.param(95.0, 10.0, "cursor-switch", "b@x.com", id="at-threshold-moves"),
        pytest.param(None, 10.0, "cursor-no-switch", None, id="live-usage-unreadable-stays"),
    ],
)
def test_auto_tick(env, live_pct, other_pct, event, to):
    """Bug caught: switching below the threshold or onto another exhausted account."""
    _two(env)

    def usages(_root, emails, current):
        return {e: Usage(other_pct, 100.0) if e != current else
                Usage(live_pct, 100.0) if live_pct is not None else
                Usage(None, None, error="http-500") for e in emails}

    out = cursor.auto_tick(env, 95.0, usages_fn=usages)
    assert (out["event"], out.get("to")) == (event, to)
    assert cursor.email_of(cursor.read_live()) == (to or "a@x.com")


def _fake_login(monkeypatch, then=None):
    calls = []

    def call(argv):
        calls.append(argv)
        if then is not None:
            _live(then)
        return 0 if then is not None else 1

    monkeypatch.setattr(cursor.shutil, "which", lambda name: "/bin/cursor-agent" if name == "cursor-agent" else None)
    monkeypatch.setattr(cursor.subprocess, "call", call)
    return calls


def test_failed_login_puts_the_previous_one_back(env, monkeypatch):
    """Bug caught: an aborted `cursor-agent login` leaving the machine logged out."""
    _live(_login("a@x.com"), {"model": "auto"})
    calls = _fake_login(monkeypatch)
    with pytest.raises(cursor.CursorError, match="previous one is back"):
        cursor.login(env)
    assert calls == [["cursor-agent", "login"]]
    assert cursor.read_live() == _login("a@x.com")
    assert cursor.stored_accounts(env) == ["a@x.com"]


def test_relogin_of_the_same_account_keeps_the_fresh_tokens(env, monkeypatch):
    """Bug caught: a repaired login thrown away for the old (maybe revoked) tokens."""
    _live(_login("a@x.com"))
    _fake_login(monkeypatch, then=_login("a@x.com", version=1))
    assert cursor.login(env) == "a@x.com"
    assert cursor.read_live() == _login("a@x.com", version=1)
    assert json.loads(cursor.stored_path(env, "a@x.com").read_text()) == _login("a@x.com", version=1)


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
