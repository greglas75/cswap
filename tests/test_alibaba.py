"""Medium: Alibaba Token Plan rotation for Qwen Code (cswap alibaba), on a temp HOME.

Origin 2026-10-18: the owner wants Qwen Code to move to another Alibaba Token
Plan account when the monthly quota runs out.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from claude_swap import alibaba
from claude_swap.alibaba import Usage


def _account(ws: str, key: str | None = None) -> dict:
    return {"qwenKey": key or f"sk-{ws}",
            "bailian": {"access_token": f"tok-{ws}", "workspace_id": ws, "api_key": "sk-other"}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".qwen").mkdir(parents=True)
    (home / ".bailian").mkdir()
    monkeypatch.setenv("CSWAP_ALIBABA_HOME", str(home))
    return tmp_path / "backup"


def _live(account: dict, settings: dict | None = None) -> None:
    alibaba.qwen_settings().write_text(json.dumps(
        {**(settings or {"model": {"name": "qwen3.8-max"}}), "env": {alibaba.ENV_KEY: account["qwenKey"]}}))
    alibaba.bailian_config().write_text(json.dumps(account["bailian"]))


def _two(env) -> None:
    _live(_account("ws-b"))
    alibaba.add(env)
    _live(_account("ws-a"))
    alibaba.add(env)


@pytest.mark.parametrize(
    ("payload", "pct", "reset"),
    [
        pytest.param({"per1MonthPercentage": 0.916, "per1MonthResetTime": 1792771200000}, 91.6, 1792771200.0,
                     id="fraction-and-ms"),
        pytest.param({"per1MonthResetTime": 1792771200000}, None, 1792771200.0, id="missing-fraction"),
        pytest.param({"per1MonthPercentage": True}, None, None, id="bool-is-not-a-fraction"),
    ],
)
def test_parse_usage(payload, pct, reset):
    """Bug caught: the fraction read as a percent (0.9% instead of 91.6%), or a reply
    without it counted as 0% used."""
    usage = alibaba.parse_usage(payload)
    assert (usage.pct if usage.pct is None else round(usage.pct, 6), usage.reset_at) == (pct, reset)
    assert (usage.error is None) == (pct is not None)


def test_switch_swaps_key_and_bl_login_and_keeps_settings(env):
    """Bug caught: Qwen Code's settings (models, hooks) lost, or the key and the
    usage login of two different accounts live together."""
    _two(env)
    assert alibaba.switch(env, "ws-b") == "ws-a"
    settings = json.loads(alibaba.qwen_settings().read_text())
    assert settings["model"] == {"name": "qwen3.8-max"}
    assert alibaba.read_live() == _account("ws-b")


def test_failed_write_puts_the_old_account_back(env, monkeypatch):
    """Bug caught: a switch failing half-way leaving B's bl login next to A's key."""
    _two(env)
    real = alibaba.filestore.write_private

    def fail_on_settings(path, data):
        if path == alibaba.qwen_settings() and data["env"][alibaba.ENV_KEY] == "sk-ws-b":
            raise OSError("disk full")
        real(path, data)

    monkeypatch.setattr(alibaba.filestore, "write_private", fail_on_settings)
    with pytest.raises(OSError):
        alibaba.switch(env, "ws-b")
    assert alibaba.read_live() == _account("ws-a")


def test_rank_soonest_reset_with_room():
    """Bug caught: moving to an exhausted or unreadable account, or wasting the
    quota that resets first."""
    now = time.time()
    usages = {"live": Usage(99.0, now + 10), "late": Usage(10.0, now + 900), "soon": Usage(50.0, now + 500),
              "full": Usage(96.0, now + 5), "broken": Usage(None, None, error="bl rc=1")}
    assert alibaba.rank(usages, "live", 95.0) == ["soon", "late"]


@pytest.mark.parametrize(
    ("live", "other", "event", "to"),
    [
        pytest.param(50.0, 0.0, "alibaba-no-switch", None, id="below-threshold"),
        pytest.param(97.0, 10.0, "alibaba-switch", "ws-b", id="used-up-moves"),
        pytest.param(None, 10.0, "alibaba-no-switch", None, id="live-unreadable-stays"),
        pytest.param(97.0, 99.0, "alibaba-no-switch", None, id="nowhere-to-go"),
    ],
)
def test_auto_tick(env, live, other, event, to):
    """Bug caught: switching below the threshold, on an unreadable reading, or onto
    another used-up account."""
    _two(env)

    def usages(_root, workspaces, current):
        return {w: Usage(other, 100.0) if w != current else
                Usage(live, 100.0) if live is not None else Usage(None, None, error="bl rc=1")
                for w in workspaces}

    out = alibaba.auto_tick(env, 95.0, usages_fn=usages)
    assert (out["event"], out.get("to")) == (event, to)
    assert alibaba.workspace_of(alibaba.read_live()) == (to or "ws-a")


def test_idle_accounts_refreshed_bl_login_is_kept(env, monkeypatch):
    """Bug caught: bl refreshing an idle account's console token under the temp
    HOME and cswap throwing the new one away."""
    _two(env)
    refreshed = {**_account("ws-b")["bailian"], "access_token": "tok-new"}
    monkeypatch.setattr(alibaba, "fetch_usage",
                        lambda bailian, live: (Usage(1.0, 1.0), bailian if live else refreshed))
    alibaba.usages_for(env, ["ws-a", "ws-b"], "ws-a")
    stored = json.loads(alibaba.stored_path(env, "ws-b").read_text())
    assert stored["bailian"]["access_token"] == "tok-new"


def test_hand_edited_key_of_another_account_is_never_stored(env):
    """Bug caught: account B's key stored under A's workspace after someone pasted
    it into ~/.qwen/settings.json by hand."""
    _two(env)
    _live({"qwenKey": "sk-ws-b", "bailian": _account("ws-a")["bailian"]})
    assert alibaba.sync_live(env) is None
    assert json.loads(alibaba.stored_path(env, "ws-a").read_text())["qwenKey"] == "sk-ws-a"
    with pytest.raises(alibaba.AlibabaError, match="another workspace"):
        alibaba.add(env)


def _fake_bl_login(monkeypatch, bailian: dict | None, key: str, verdict: str = "ok"):
    def call(argv, env):
        if bailian is not None:
            cfg = Path(env["HOME"]) / ".bailian" / "config.json"
            cfg.parent.mkdir(parents=True)
            cfg.write_text(json.dumps(bailian))
        return 0 if bailian is not None else 1

    monkeypatch.setattr(alibaba, "_bl", lambda: "/bin/bl")
    monkeypatch.setattr(alibaba.subprocess, "call", call)
    monkeypatch.setattr(alibaba.getpass, "getpass", lambda _prompt: key)
    monkeypatch.setattr(alibaba, "key_check", lambda _key: verdict)


def test_login_makes_a_complete_new_account_live(env, monkeypatch):
    """Bug caught: the old account lost, or the new key paired with the old bl login."""
    _live(_account("ws-a"))
    _fake_bl_login(monkeypatch, _account("ws-b")["bailian"], "sk-ws-b")
    assert alibaba.login(env) == "ws-b"
    assert alibaba.read_live() == _account("ws-b")
    assert alibaba.stored_accounts(env) == ["ws-a", "ws-b"]


@pytest.mark.parametrize(
    ("bailian", "key", "verdict", "message"),
    [
        pytest.param(None, "sk-x", "ok", "no new account", id="bl-login-aborted"),
        pytest.param({"workspace_id": "ws-b"}, "sk-x", "invalid", "invalid", id="key-refused"),
        pytest.param({"workspace_id": "ws-b"}, "sk-x", "unreachable", "unreachable", id="no-verdict"),
        pytest.param({"workspace_id": "ws-b"}, "sk-ws-a", "ok", "already stored for ws-a", id="key-of-another"),
    ],
)
def test_incomplete_login_changes_nothing(env, monkeypatch, bailian, key, verdict, message):
    """Bug caught: a half-done login leaving Qwen Code or bl on another account."""
    _live(_account("ws-a"))
    alibaba.add(env)
    _fake_bl_login(monkeypatch, bailian, key, verdict)
    with pytest.raises(alibaba.AlibabaError, match=message):
        alibaba.login(env)
    assert alibaba.read_live() == _account("ws-a")
    assert alibaba.stored_accounts(env) == ["ws-a"]


def test_failed_login_restores_a_bl_login_no_account_owns(env, monkeypatch):
    """Bug caught: an aborted `bl auth login` wiping the bl config when Qwen Code
    had no Token Plan key yet."""
    alibaba.bailian_config().write_text(json.dumps({"access_token": "t", "console_site": "international"}))
    monkeypatch.setattr(alibaba, "_bl", lambda: "/bin/bl")
    monkeypatch.setattr(alibaba.subprocess, "call", lambda argv, env: 1)
    with pytest.raises(alibaba.AlibabaError, match="no new account"):
        alibaba.login(env)
    assert json.loads(alibaba.bailian_config().read_text()) == {"access_token": "t",
                                                               "console_site": "international"}
