"""Medium: Xiaomi MiMo Code login rotation (cswap mimo), on a temp auth.json.

Origin 2026-10-18: the owner wants mimo to move to another Token Plan account
when one runs out, like cswap does for Codex, agy and Cursor.
"""

from __future__ import annotations

import json

import pytest

from claude_swap import mimo
from claude_swap.mimo import Probe


def _entry(uid: str, key: str = "tp-k") -> dict:
    return {"type": "api", "key": f"{key}-{uid}",
            "metadata": {"uid": uid, "base_url": "https://token-plan-ams.example/v1"}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "mimocode"
    home.mkdir()
    monkeypatch.setenv("CSWAP_MIMO_HOME", str(home))
    return tmp_path / "backup"


def _live(entry: dict | None, other: dict | None = None) -> None:
    data = dict(other or {})
    if entry is not None:
        data[mimo.PROVIDER] = entry
    mimo.live_file().write_text(json.dumps(data))


def _three(env) -> None:
    for uid in ("3", "2", "1"):
        _live(_entry(uid), {"kimi": {"type": "api", "key": "other"}})
        mimo.add(env)


def test_switch_replaces_only_the_xiaomi_entry(env):
    """Bug caught: other providers' logins in auth.json lost on a switch, or the
    old account's current entry dropped."""
    _three(env)
    _live(_entry("1", key="tp-new"), {"kimi": {"type": "api", "key": "other"}})
    assert mimo.switch(env, "2") == "1"
    data = json.loads(mimo.live_file().read_text())
    assert data == {"kimi": {"type": "api", "key": "other"}, "xiaomi": _entry("2")}
    assert json.loads(mimo.stored_path(env, "1").read_text()) == _entry("1", key="tp-new")


@pytest.mark.parametrize(
    ("live", "message"),
    [
        pytest.param(_entry("9"), "9 is not stored", id="unstored"),
        pytest.param({"type": "api", "key": "tp-x", "metadata": {}}, "(no uid)", id="no-uid"),
    ],
)
def test_switch_refuses_to_overwrite_a_login_nobody_has(env, live, message):
    """Bug caught: a switch destroying the only copy of a login."""
    _three(env)
    _live(live)
    with pytest.raises(mimo.MimoError, match=message):
        mimo.switch(env, "2")
    assert mimo.read_live() == live


def test_unparseable_auth_file_is_never_replaced(env):
    """Bug caught: other providers' logins wiped by a rewrite of a half-written file."""
    _three(env)
    mimo.live_file().write_text("{broken")
    with pytest.raises(mimo.MimoError, match="does not parse"):
        mimo.write_live(_entry("2"))
    assert mimo.live_file().read_text() == "{broken"


@pytest.mark.parametrize(
    ("status", "state"),
    [(200, "ok"), (401, "invalid"), (402, "exhausted"), (403, "exhausted"), (429, "exhausted"),
     (500, "unknown"), (None, "unknown")],
)
def test_classify(status, state):
    """Bug caught: a server error or a network blip read as a used-up account (a needless switch)."""
    assert mimo.classify(status, "").state == state


@pytest.mark.parametrize(
    ("answers", "recent_exhausted", "event", "to", "probed"),
    [
        pytest.param({"1": "ok"}, None, "mimo-no-switch", None, ["1"], id="live-ok-probes-nothing-else"),
        pytest.param({"1": "unknown"}, None, "mimo-no-switch", None, ["1"], id="live-unknown-stays"),
        pytest.param({"1": "exhausted", "2": "ok"}, None, "mimo-switch", "2", ["1", "2"], id="next-in-round"),
        pytest.param({"1": "exhausted", "2": "exhausted", "3": "ok"}, "2", "mimo-switch", "3", ["1", "3"],
                     id="skips-recently-refused"),
        pytest.param({"1": "invalid", "2": "exhausted", "3": "unknown"}, None, "mimo-no-switch", None,
                     ["1", "2", "3"], id="nobody-answers"),
    ],
)
def test_auto_tick(env, answers, recent_exhausted, event, to, probed):
    """Bug caught: switching while the live account still works, onto one that
    is refused, or re-probing an account refused minutes ago."""
    _three(env)
    _live(_entry("1"))
    now = 10_000.0
    if recent_exhausted:
        mimo.record(env, recent_exhausted, Probe("exhausted", 429), now - 60)
    seen = []

    def fake_probe(entry):
        uid = entry["metadata"]["uid"]
        seen.append(uid)
        return Probe(answers[uid], 200 if answers[uid] == "ok" else 429)

    out = mimo.auto_tick(env, probe_fn=fake_probe, clock=lambda: now)
    assert (out["event"], out.get("to"), seen) == (event, to, probed)
    assert mimo.uid_of(mimo.read_live()) == (to or "1")


def test_failed_login_puts_the_previous_one_back(env, monkeypatch):
    """Bug caught: quitting mimo without a /login leaving the machine logged out."""
    _live(_entry("1"), {"kimi": {"type": "api", "key": "other"}})
    monkeypatch.setattr(mimo, "_binary", lambda: "/bin/mimo")
    calls = []
    monkeypatch.setattr(mimo.subprocess, "call", lambda argv, env: calls.append(env["MIMOCODE_DISABLE_CLAUDE_IMPORT"]))
    with pytest.raises(mimo.MimoError, match="previous one is back"):
        mimo.login(env)
    assert calls == ["1"]
    assert json.loads(mimo.live_file().read_text()) == {"kimi": {"type": "api", "key": "other"},
                                                        "xiaomi": _entry("1")}
