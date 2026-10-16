"""Medium: Antigravity CLI login rotation (cswap agy), on a temp ~/.gemini.

Origin 2026-10-16: the owner has two Google accounts for agy and wants it to
move to the second one when the first runs out, like cswap does for Codex.
"""

from __future__ import annotations

import base64
import json

import pytest

from claude_swap import agy
from claude_swap.agy import Quota


def _login(email: str, access: str = "a0") -> dict:
    body = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return {
        "token": {"access_token": access, "refresh_token": "r", "token_type": "Bearer",
                  "expiry": "2999-01-01T00:00:00Z"},
        "auth_method": "consumer",
        "id_token": f"h.{body}.s",
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "gemini"
    home.mkdir()
    monkeypatch.setenv("CSWAP_AGY_HOME", str(home))   # also keeps the keychain out
    return tmp_path / "backup"


def _live(data: dict) -> None:
    path = agy.live_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _two(env) -> None:
    _live(_login("b@x.com"))
    agy.add(env)
    _live(_login("a@x.com"))
    agy.add(env)


@pytest.mark.parametrize(
    ("models", "payload", "life", "model"),
    [
        pytest.param([], {"opus": (0.2, True), "flash": (0.8, True)}, 20.0, "opus", id="tightest-counts"),
        pytest.param([], {"tab_x": (0.0, False), "flash": (0.8, True)}, 80.0, "flash",
                     id="internal-models-without-reset-ignored"),
        pytest.param(["flash"], {"opus": (0.0, True), "flash": (0.6, True)}, 60.0, "flash",
                     id="prefixes-narrow"),
    ],
)
def test_parse_models(models, payload, life, model):
    """Bug caught: an unlimited internal model or an uncounted one deciding the switch."""
    data = {"models": {
        name: {"quotaInfo": {"remainingFraction": f, **({"resetTime": "2999-01-01T00:00:00Z"} if r else {})}}
        for name, (f, r) in payload.items()
    }}
    q = agy.parse_models(data, models)
    assert (q.life, q.model) == (pytest.approx(life), model)


def test_switch_puts_the_target_live_and_keeps_the_refreshed_live_copy(env):
    """Bug caught: the token agy refreshed on the live account lost on a switch."""
    _two(env)
    _live(_login("a@x.com", access="a1"))       # agy refreshed it meanwhile
    assert agy.switch(env, "b@x.com") == "a@x.com"
    assert agy.email_of(agy.read_live()) == "b@x.com"
    assert agy.read_file(agy.stored_path(env, "a@x.com"))["token"]["access_token"] == "a1"


def test_switch_refuses_to_overwrite_an_unstored_live_login(env):
    """Bug caught: the only copy of a login overwritten."""
    _live(_login("b@x.com"))
    agy.add(env)
    _live(_login("stray@x.com"))
    with pytest.raises(agy.AgyError):
        agy.switch(env, "b@x.com")
    assert agy.email_of(agy.read_live()) == "stray@x.com"


@pytest.mark.parametrize(
    ("life", "event", "live"),
    [
        pytest.param(2.0, "agy-switch", "b@x.com", id="at-threshold-moves"),
        pytest.param(40.0, "agy-no-switch", "a@x.com", id="below-threshold-stays"),
    ],
)
def test_auto_tick(env, life, event, live):
    _two(env)
    out = agy.auto_tick(env, 95.0, [], fetch=lambda data, models: Quota(life, "opus", 5000.0), now=1000.0)
    assert out["event"] == event
    assert agy.email_of(agy.read_live()) == live


def test_an_account_seen_exhausted_waits_for_its_reset(env):
    """Bug caught: switching back onto the account that just ran out."""
    _two(env)
    agy.record(env, "b@x.com", Quota(0.0, "opus", 2000.0), 900.0)
    out = agy.auto_tick(env, 95.0, [], fetch=lambda d, m: Quota(1.0, "opus", 5000.0), now=1000.0)
    assert out["reason"] == "no-account-with-room"
    assert agy.rank(["a@x.com", "b@x.com"], "a@x.com", agy.load_state(env), 95.0, 2001.0) == ["b@x.com"]


def test_an_unreadable_live_quota_never_switches(env):
    """Bug caught: an expired token (agy idle) read as an exhausted account."""
    _two(env)
    out = agy.auto_tick(env, 95.0, [], fetch=lambda d, m: Quota(None, None, None, error="expired"), now=1000.0)
    assert out["reason"] == "active-quota-unknown"
    assert agy.email_of(agy.read_live()) == "a@x.com"


def test_login_without_a_new_sign_in_puts_the_old_login_back(env, monkeypatch):
    """Bug caught: `cswap agy login` aborted leaves agy logged out."""
    _live(_login("a@x.com"))
    monkeypatch.setattr(agy.shutil, "which", lambda name: "/bin/agy")
    monkeypatch.setattr(agy.subprocess, "call", lambda cmd: 0)
    with pytest.raises(agy.AgyError):
        agy.login(env)
    assert agy.email_of(agy.read_live()) == "a@x.com"


def test_login_interrupted_in_agy_puts_the_old_login_back(env, monkeypatch):
    """Bug caught (review): Ctrl-C inside agy left the machine logged out."""
    _live(_login("a@x.com"))
    monkeypatch.setattr(agy.shutil, "which", lambda name: "/bin/agy")

    def interrupted(cmd):
        raise KeyboardInterrupt

    monkeypatch.setattr(agy.subprocess, "call", interrupted)
    with pytest.raises(KeyboardInterrupt):
        agy.login(env)
    assert agy.email_of(agy.read_live()) == "a@x.com"


@pytest.mark.parametrize(
    ("entry", "life"),
    [
        pytest.param({"life": 0.0, "resetAt": None, "at": 0.0}, None, id="no-reset-old-reading-expires"),
        pytest.param({"life": 0.0, "resetAt": None, "at": 99_000.0}, 0.0, id="no-reset-recent-reading-holds"),
        pytest.param({"life": 0.0, "resetAt": 50_000.0, "at": 0.0}, 100.0, id="past-reset-is-fresh"),
    ],
)
def test_known_life(entry, life):
    """Bug caught (review): a reading without a reset time held an account out forever."""
    assert agy.known_life(entry, 100_000.0) == life


def test_nanosecond_reset_times_parse():
    """Bug caught (review): RFC 3339 nanoseconds made the reset time unreadable."""
    assert agy._ts("2026-10-16T20:36:06.123456789Z") == pytest.approx(agy._ts("2026-10-16T20:36:06.123456Z"))


def test_the_agy_1_4_token_file_is_the_live_login(env):
    """Bug caught (2026-10-16, ryzen-dev): agy 1.4 keeps the login in
    antigravity-cli/antigravity-oauth-token, which cswap did not see."""
    cli = agy.gemini_home() / agy.CLI_TOKEN_FILE
    cli.parent.mkdir(parents=True)
    cli.write_text(json.dumps(_login("b@x.com")))
    assert agy.add(env) == "b@x.com"
    cli.write_text(json.dumps(_login("a@x.com")))
    agy.add(env)
    agy.switch(env, "b@x.com")
    assert agy.email_of(agy.read_file(cli)) == "b@x.com"
