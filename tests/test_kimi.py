"""Medium: Kimi Code CLI login rotation (cswap kimi), on a temp ~/.kimi-code.

Origin 2026-10-24: the owner wants kimi to move to another account when one
runs out, like cswap does for Codex, Cursor, mimo and Qwen Code.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

import pytest

from claude_swap import kimi
from claude_swap.kimi import Usage

KEY = "kimi-code-env-0e4f99c69cc27850"
HOST = "https://auth.kimi.ai"
BASE = "https://api.kimi.ai/coding/v1"


def _jwt(sub: str, kind: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"sub": sub, "type": kind}).encode()).decode().rstrip("=")
    return f"h.{body}.s"


def _token(user: str, gen: int = 1, expires_at: float | None = None) -> dict:
    return {"access_token": _jwt(user, "access") + str(gen), "refresh_token": _jwt(user, "refresh") + str(gen),
            "expires_at": int(expires_at if expires_at is not None else 4_000_000_000),
            "scope": "kimi-code", "token_type": "Bearer", "expires_in": 900}


def _account(user: str, gen: int = 1, **kw) -> dict:
    return {"token": _token(user, gen, **kw), "oauthHost": HOST, "baseUrl": BASE}


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "kimi-code"
    (home / "credentials").mkdir(parents=True)
    (home / "config.toml").write_text(
        f'[providers."managed:kimi-code"]\nbase_url = "{BASE}"\n'
        f'[providers."managed:kimi-code".oauth]\nkey = "oauth/{KEY}"\noauth_host = "{HOST}"\n')
    monkeypatch.setenv("CSWAP_KIMI_HOME", str(home))
    return tmp_path / "backup"


def _live_path() -> Path:
    return kimi.home() / "credentials" / f"{KEY}.json"


def _live(token: dict) -> None:
    _live_path().write_text(json.dumps(token))


def _two(env) -> None:
    _live(_token("u-b"))
    kimi.add(env)
    _live(_token("u-a"))
    kimi.add(env)


@pytest.mark.parametrize(
    ("usages", "pct", "reset", "error"),
    [
        pytest.param({"limit_5h": {"used_ratio": 0.2, "reset_time": "2026-10-24T15:14:47Z"},
                      "limit_month_total": {"used_ratio": 1, "reset_time": "2026-10-27T08:39:07Z"}},
                     100.0, 1793090347.0, None, id="month-used-up-binds-and-its-reset-ranks"),
        pytest.param({"limit_5h": {"used_ratio": "0.5"}}, 50.0, None, None, id="ratio-as-string"),
        pytest.param({"limit_5h": {"used_ratio": True}}, None, None, "no usage windows", id="bool-is-not-a-ratio"),
        pytest.param({"limit_5h": {}}, None, None, "no usage windows", id="nothing-readable"),
        pytest.param({}, 100.0, None, None, id="free-account-has-no-plan"),
    ],
)
def test_parse_usage(usages, pct, reset, error):
    """Bug caught: a used-up month hidden behind an empty 5h window, the ratio
    read as a percent, an unreadable reply counted as 0% used, or a Free
    account (no plan, every request refused) kept live as "usage unknown"."""
    usage = kimi.parse_usage({"usages": usages})
    assert (usage.pct, usage.reset_at) == (pct, reset)
    assert (usage.error or "").startswith(error or "")


def test_switch_stores_the_rotated_live_token_and_writes_only_the_login(env):
    """Bug caught: the live account's newest refresh token (kimi rotates it)
    thrown away on a switch, or kimi's config touched."""
    _two(env)
    config = (kimi.home() / "config.toml").read_text()
    _live(_token("u-a", gen=2))
    assert kimi.switch(env, "u-b") == "u-a"
    assert json.loads(_live_path().read_text()) == _token("u-b")
    assert json.loads(kimi.stored_path(env, "u-a").read_text())["token"]["refresh_token"].endswith("2")
    assert (kimi.home() / "config.toml").read_text() == config


def test_a_revoked_live_login_never_overwrites_the_stored_copy(env):
    """Bug caught: kimi's revoked tombstone stored as the account, or a dead
    login blocking the switch away from it."""
    _two(env)
    _live({"access_token": "", "refresh_token": "", "expires_at": 0, "scope": "kimi-code",
           "token_type": "Bearer", "expires_in": 0})
    assert kimi.switch(env, "u-b") is None
    assert json.loads(_live_path().read_text()) == _token("u-b")
    assert json.loads(kimi.stored_path(env, "u-a").read_text())["token"] == _token("u-a")


def test_account_from_another_region_is_never_written(env):
    """Bug caught: a kimi.com token put into kimi.ai's slot (a login that cannot work)."""
    _two(env)
    stored = json.loads(kimi.stored_path(env, "u-b").read_text())
    kimi.filestore.write_private(kimi.stored_path(env, "u-b"), {**stored, "oauthHost": "https://auth.kimi.com"})
    with pytest.raises(kimi.KimiError, match="not mixing regions"):
        kimi.switch(env, "u-b")
    assert json.loads(_live_path().read_text()) == _token("u-a")


def test_switch_waits_for_kimis_lock_and_takes_over_a_stale_one(env, monkeypatch):
    """Bug caught: writing while kimi refreshes (its refresh lands after the
    switch and undoes it), or a crashed kimi's lock blocking cswap forever."""
    _two(env)
    lockdir = kimi.home() / "oauth" / f"{KEY}.lock"
    lockdir.mkdir(parents=True)
    monkeypatch.setattr(kimi, "LOCK_WAIT_S", 0.5)
    with pytest.raises(kimi.KimiError, match="holds its login lock"):
        kimi.switch(env, "u-b")
    assert json.loads(_live_path().read_text()) == _token("u-a")
    old = time.time() - 30
    os.utime(lockdir, (old, old))
    kimi.switch(env, "u-b")
    assert json.loads(_live_path().read_text()) == _token("u-b")
    assert not lockdir.exists()


def test_usage_refreshes_an_expired_token_and_keeps_the_new_pair_everywhere(env, monkeypatch):
    """Bug caught: a refreshed (rotated) token kept only in memory, so the next
    refresh of that account fails and kimi revokes the login."""
    _live(_token("u-b", expires_at=0))
    kimi.add(env)
    _live(_token("u-a", expires_at=0))
    kimi.add(env)
    monkeypatch.setattr(kimi, "refresh", lambda account: {**account, "token": _token(
        kimi.account_user(account), gen=9)})
    seen = []
    usages = kimi.usages_for(env, ["u-a", "u-b"], "u-a",
                             fetch_fn=lambda a: seen.append(a["token"]["access_token"][-1]) or Usage(1.0, None))
    assert seen == ["9", "9"] and usages["u-a"].pct == 1.0
    assert json.loads(_live_path().read_text())["refresh_token"].endswith("9")
    for user in ("u-a", "u-b"):
        assert json.loads(kimi.stored_path(env, user).read_text())["token"]["refresh_token"].endswith("9")


@pytest.mark.parametrize(
    ("live", "other", "event", "to"),
    [
        pytest.param(50.0, 0.0, "kimi-no-switch", None, id="below-threshold"),
        pytest.param(100.0, 10.0, "kimi-switch", "u-b", id="used-up-moves"),
        pytest.param(None, 10.0, "kimi-no-switch", None, id="live-unreadable-stays"),
        pytest.param(100.0, 99.0, "kimi-no-switch", None, id="nowhere-to-go"),
    ],
)
def test_auto_tick(env, live, other, event, to):
    """Bug caught: switching below the threshold, on an unreadable reading, or
    onto another used-up account."""
    _two(env)

    def usages(_root, users, current):
        return {u: Usage(other, 100.0) if u != current else
                Usage(live, 100.0) if live is not None else Usage(None, None, error="HTTP 401")
                for u in users}

    out = kimi.auto_tick(env, 95.0, usages_fn=usages)
    assert (out["event"], out.get("to")) == (event, to)
    assert kimi.user_of(json.loads(_live_path().read_text())) == (to or "u-a")


def test_login_stores_both_accounts_and_makes_the_new_one_live(env, monkeypatch):
    """Bug caught: the old login lost, or `kimi login` run on the live home
    (it would replace the live login before the new one is stored)."""
    _live(_token("u-a"))
    homes = []

    def call(argv, env):
        assert argv[1:] == ["login", "--region", "global"]
        homes.append(env["KIMI_CODE_HOME"])
        creds = Path(env["KIMI_CODE_HOME"]) / "credentials"
        creds.mkdir()
        (creds / "kimi-code-env-other.json").write_text(json.dumps(_token("u-b")))
        return 0

    monkeypatch.setattr(kimi, "_binary", lambda: "/bin/kimi")
    monkeypatch.setattr(kimi.subprocess, "call", call)
    assert kimi.login(env) == "u-b"
    assert homes and homes[0] != str(kimi.home())
    assert kimi.stored_accounts(env) == ["u-a", "u-b"]
    assert json.loads(_live_path().read_text()) == _token("u-b")


def test_aborted_login_changes_nothing(env, monkeypatch):
    """Bug caught: quitting `kimi login` leaving kimi logged out or on another account."""
    _live(_token("u-a"))
    monkeypatch.setattr(kimi, "_binary", lambda: "/bin/kimi")
    monkeypatch.setattr(kimi.subprocess, "call", lambda argv, env: 1)
    with pytest.raises(kimi.KimiError, match="nothing changed"):
        kimi.login(env)
    assert json.loads(_live_path().read_text()) == _token("u-a")
