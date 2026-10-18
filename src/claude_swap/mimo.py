"""Xiaomi MiMo Code (``mimo``) account rotation (``cswap mimo …``).

``mimo`` keeps its login as one provider entry in
``~/.local/share/mimocode/auth.json``: ``{"xiaomi": {"type": "api", "key":
"tp-…", "metadata": {"uid": …, "base_url": …}}}`` — a Token Plan key that
``/login`` fetches from the MiMo platform. cswap keeps a copy per account under
``<backup root>/mimo/<uid>.json`` and swaps only that entry (other providers in
the file stay as they are).

The Token Plan's usage is on the platform's web console only (a Xiaomi account
session, not the key), so an account cannot be measured ahead. The auto loop
sends a 1-token request instead: a quota/limit refusal means the account is
used up, and the next stored account that answers takes over.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from claude_swap import filestore
from claude_swap.real_store_guard import refuse_test_identity

PROVIDER = "xiaomi"
PROBE_MODEL = "mimo-v2.5"
RECHECK_EXHAUSTED_S = 3600


class MimoError(Exception):
    """A user-facing failure of a ``cswap mimo`` command."""


def live_file() -> Path:
    override = os.environ.get("CSWAP_MIMO_HOME")
    return (Path(override) if override else Path("~/.local/share/mimocode").expanduser()) / "auth.json"


def store_dir(backup_root: Path) -> Path:
    return Path(backup_root) / "mimo"


def uid_of(entry: dict | None) -> str | None:
    if not isinstance(entry, dict) or not entry.get("key"):
        return None
    uid = (entry.get("metadata") or {}).get("uid")
    return str(uid) if isinstance(uid, (str, int)) and str(uid) else None


def read_live() -> dict | None:
    entry = (filestore.read_json(live_file()) or {}).get(PROVIDER)
    return entry if isinstance(entry, dict) and entry.get("key") else None


def write_live(entry: dict | None) -> None:
    """Put ``entry`` in as the xiaomi provider (None removes it); the rest of
    auth.json is kept, and an unreadable file is never replaced."""
    data = filestore.read_settings(live_file(), MimoError)
    if entry is None:
        data.pop(PROVIDER, None)
    else:
        refuse_test_identity(uid_of(entry), live_file())
        data[PROVIDER] = entry
    filestore.write_private(live_file(), data)


def stored_path(backup_root: Path, uid: str) -> Path:
    return store_dir(backup_root) / f"{filestore.safe_name(uid, MimoError)}.json"


def stored_accounts(backup_root: Path) -> list[str]:
    root = store_dir(backup_root)
    if not root.is_dir():
        return []
    return sorted(p.name[: -len(".json")] for p in root.iterdir()
                  if p.name.endswith(".json") and not p.name.startswith(".")
                  and uid_of(filestore.read_json(p)) == p.name[: -len(".json")])


def _sync_live(backup_root: Path) -> str | None:
    live = read_live()
    uid = uid_of(live)
    if uid is not None and stored_path(backup_root, uid).exists():
        filestore.write_private(stored_path(backup_root, uid), live)
    return uid


def sync_live(backup_root: Path) -> str | None:
    with filestore.lock(store_dir(backup_root)):
        return _sync_live(backup_root)


def add(backup_root: Path) -> str:
    live = read_live()
    uid = uid_of(live)
    if uid is None:
        raise MimoError("mimo is not logged in to Xiaomi — run mimo, then /login")
    with filestore.lock(store_dir(backup_root)):
        filestore.write_private(stored_path(backup_root, uid), live)
    return uid


def _binary() -> str:
    found = shutil.which("mimo") or str(Path("~/.mimocode/bin/mimo").expanduser())
    if not Path(found).exists():
        raise MimoError("mimo is not installed (curl -fsSL https://mimo.xiaomi.com/install | bash)")
    return found


def login(backup_root: Path) -> str:
    """Store the current login, take it out of auth.json, open mimo for a
    `/login` with another account, store that one. Without a new account the
    previous login is put back."""
    binary = _binary()
    with filestore.lock(store_dir(backup_root)):
        previous = read_live()
        if uid_of(previous) is not None:
            filestore.write_private(stored_path(backup_root, uid_of(previous)), previous)
    fresh = None
    try:
        with filestore.lock(store_dir(backup_root)):
            write_live(None)
        print("In mimo: /login, pick Xiaomi, finish in the browser, then quit mimo (ctrl+c).")
        # The first start imports all Claude Code history (tens of GB) before it shows anything.
        subprocess.call([binary], env={**os.environ, "MIMOCODE_DISABLE_CLAUDE_IMPORT": "1"})
        fresh = read_live()
    finally:
        if uid_of(fresh) is None and previous is not None:
            with filestore.lock(store_dir(backup_root)):
                write_live(previous)
    uid = uid_of(fresh)
    if uid is None:
        raise MimoError("no new login — the previous one is back in place" if previous else "no new login")
    with filestore.lock(store_dir(backup_root)):
        filestore.write_private(stored_path(backup_root, uid), fresh)
    return uid


def remove(backup_root: Path, uid: str) -> None:
    with filestore.lock(store_dir(backup_root)):
        path = stored_path(backup_root, uid)
        if not path.exists():
            raise MimoError(f"{uid} is not stored")
        if uid_of(read_live()) == uid:
            raise MimoError(f"{uid} is the live mimo login — switch to another one first")
        path.unlink()


def _switch(backup_root: Path, uid: str) -> str | None:
    target = filestore.read_json(stored_path(backup_root, uid))
    if uid_of(target) != uid:
        raise MimoError(f"{uid} is not stored — log in with it and run: cswap mimo add")
    live = read_live()
    live_uid = uid_of(live)
    if live is not None and (live_uid is None or not stored_path(backup_root, live_uid).exists()):
        raise MimoError(f"the live mimo login {live_uid or '(no uid)'} is not stored — run `cswap mimo add` first")
    previous = _sync_live(backup_root)
    if previous != uid:
        write_live(target)
    return previous


def switch(backup_root: Path, uid: str) -> str | None:
    with filestore.lock(store_dir(backup_root)):
        return _switch(backup_root, uid)


@dataclass(frozen=True)
class Probe:
    state: str          # ok | exhausted | invalid | unknown
    status: int | None
    detail: str = ""


def classify(status: int | None, body: str) -> Probe:
    if status == 200:
        return Probe("ok", status)
    text = body[:300]
    if status == 401:
        return Probe("invalid", status, text)
    if status in (402, 403, 429):
        return Probe("exhausted", status, text)
    return Probe("unknown", status, text)


def probe(entry: dict, timeout: float = 30.0) -> Probe:
    """One 1-token request on the account's Token Plan endpoint."""
    base = str((entry.get("metadata") or {}).get("base_url") or "").rstrip("/")
    if not base.startswith("https://"):
        return Probe("unknown", None, "no https base_url in the login")
    req = urllib.request.Request(
        base + "/chat/completions", method="POST",
        data=json.dumps({"model": PROBE_MODEL, "max_tokens": 1,
                         "messages": [{"role": "user", "content": "hi"}]}).encode(),
        headers={"Authorization": f"Bearer {entry['key']}", "Content-Type": "application/json",
                 "User-Agent": "cswap"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            return classify(resp.status, "")
    except urllib.error.HTTPError as e:
        return classify(e.code, e.read(600).decode(errors="replace"))
    except (urllib.error.URLError, OSError) as e:
        return Probe("unknown", None, type(e).__name__)


def state_path(backup_root: Path) -> Path:
    return store_dir(backup_root) / ".state.json"


def load_state(backup_root: Path) -> dict:
    return filestore.read_json(state_path(backup_root)) or {}


def record(backup_root: Path, uid: str, result: Probe, now: float) -> None:
    state = load_state(backup_root)
    state[uid] = {"state": result.state, "status": result.status, "detail": result.detail, "at": now}
    filestore.write_private(state_path(backup_root), state)


def auto_tick(backup_root: Path, *, dry_run: bool = False, probe_fn=probe, clock=time.time) -> dict:
    with filestore.lock(store_dir(backup_root)):
        current = _sync_live(backup_root)
        uids = stored_accounts(backup_root)
        if current is None or current not in uids:
            return {"event": "mimo-no-switch", "reason": "live-login-not-stored", "live": current}
        now = clock()
        live = probe_fn(filestore.read_json(stored_path(backup_root, current)))
        record(backup_root, current, live, now)
        if live.state not in ("exhausted", "invalid"):
            return {"event": "mimo-no-switch", "reason": f"live-{live.state}", "account": current}
        state = load_state(backup_root)
        # The others in a fixed round, starting after the live one.
        at = uids.index(current)
        for uid in uids[at + 1:] + uids[:at]:
            last = state.get(uid) or {}
            if last.get("state") in ("exhausted", "invalid") and now - last.get("at", 0) < RECHECK_EXHAUSTED_S:
                continue
            result = probe_fn(filestore.read_json(stored_path(backup_root, uid)))
            record(backup_root, uid, result, now)
            if result.state == "ok":
                if not dry_run:
                    _switch(backup_root, uid)
                return {"event": "mimo-switch", "from": current, "to": uid, "liveState": live.state,
                        "liveStatus": live.status, "dryRun": dry_run}
        return {"event": "mimo-no-switch", "reason": "no-account-answers", "account": current,
                "liveState": live.state, "liveStatus": live.status}
