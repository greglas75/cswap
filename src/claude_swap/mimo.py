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
import signal
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
    metadata = entry.get("metadata")
    uid = metadata.get("uid") if isinstance(metadata, dict) else None
    return str(uid) if isinstance(uid, (str, int)) and str(uid) else None


def read_live(strict: bool = False) -> dict | None:
    """The xiaomi entry. ``strict``: an unreadable auth.json raises instead of
    looking logged out (callers about to change or delete something)."""
    data = filestore.read_settings(live_file(), MimoError) if strict else filestore.read_json(live_file())
    entry = (data or {}).get(PROVIDER)
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
        previous = read_live(strict=True)
        if previous is not None and uid_of(previous) is None:
            raise MimoError("the live mimo login has no uid and cannot be stored — not replacing it")
        if previous is not None:
            filestore.write_private(stored_path(backup_root, uid_of(previous)), previous)
    fresh = None
    # Ctrl+C quits mimo; it must not also stop cswap before the new login is read.
    old_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        with filestore.lock(store_dir(backup_root)):
            write_live(None)
        print("In mimo: /login, pick Xiaomi, finish in the browser, then quit mimo.")
        # The first start imports all Claude Code history (tens of GB) before it shows anything.
        subprocess.call([binary], env={**os.environ, "MIMOCODE_DISABLE_CLAUDE_IMPORT": "1"})
    finally:
        signal.signal(signal.SIGINT, old_handler)
        fresh = read_live()
        # Only an empty slot gets the previous login back: anything mimo wrote stays.
        if fresh is None and previous is not None:
            with filestore.lock(store_dir(backup_root)):
                write_live(previous)
    uid = uid_of(fresh)
    if fresh is None:
        raise MimoError("no new login — the previous one is back in place" if previous else "no new login")
    if uid is None:
        raise MimoError("mimo wrote a login without a uid — it stays live but cswap cannot store it")
    with filestore.lock(store_dir(backup_root)):
        filestore.write_private(stored_path(backup_root, uid), fresh)
    return uid


def remove(backup_root: Path, uid: str) -> None:
    with filestore.lock(store_dir(backup_root)):
        path = stored_path(backup_root, uid)
        if not path.exists():
            raise MimoError(f"{uid} is not stored")
        if uid_of(read_live(strict=True)) == uid:
            raise MimoError(f"{uid} is the live mimo login — switch to another one first")
        path.unlink()


def _switch(backup_root: Path, uid: str) -> str | None:
    target = filestore.read_json(stored_path(backup_root, uid))
    if uid_of(target) != uid:
        raise MimoError(f"{uid} is not stored — log in with it and run: cswap mimo add")
    live = read_live(strict=True)
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


QUOTA_WORDS = ("quota", "credit", "balance", "insufficient", "exhaust", "expired", "plan", "subscription")


def classify(status: int | None, body: str) -> Probe:
    """402 is a used-up plan; 403/429 only when the reply talks about the quota
    — a plain rate limit or a permission error is not a reason to switch."""
    if status == 200:
        return Probe("ok", status)
    text = body[:300]
    if status == 401:
        return Probe("invalid", status, text)
    if status == 402 or (status in (403, 429) and any(w in body.lower() for w in QUOTA_WORDS)):
        return Probe("exhausted", status, text)
    return Probe("unknown", status, text)


def probe(entry: dict, timeout: float = 30.0) -> Probe:
    """One 1-token request on the account's Token Plan endpoint."""
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    base = str(metadata.get("base_url") or "").rstrip("/")
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
    except Exception as e:  # noqa: BLE001 — any other failure says nothing about the quota
        return Probe("unknown", None, type(e).__name__)


def state_path(backup_root: Path) -> Path:
    return store_dir(backup_root) / ".state.json"


def load_state(backup_root: Path) -> dict:
    return filestore.read_json(state_path(backup_root)) or {}


def record(backup_root: Path, uid: str, result: Probe, now: float) -> None:
    state = load_state(backup_root)
    state[uid] = {"state": result.state, "status": result.status, "detail": result.detail, "at": now}
    filestore.write_private(state_path(backup_root), state)


def _probe_and_record(backup_root: Path, uid: str, probe_fn, now: float) -> Probe:
    """The HTTP call runs outside the store lock; only the record takes it."""
    result = probe_fn(filestore.read_json(stored_path(backup_root, uid)) or {})
    with filestore.lock(store_dir(backup_root)):
        record(backup_root, uid, result, now)
    return result


def auto_tick(backup_root: Path, *, dry_run: bool = False, probe_fn=probe, clock=time.time) -> dict:
    with filestore.lock(store_dir(backup_root)):
        current = _sync_live(backup_root)
        uids = stored_accounts(backup_root)
        state = load_state(backup_root)
    if current is None or current not in uids:
        return {"event": "mimo-no-switch", "reason": "live-login-not-stored", "live": current}
    now = clock()
    live = _probe_and_record(backup_root, current, probe_fn, now)
    if live.state not in ("exhausted", "invalid"):
        return {"event": "mimo-no-switch", "reason": f"live-{live.state}", "account": current}
    # The others in a fixed round, starting after the live one.
    at = uids.index(current)
    for uid in uids[at + 1:] + uids[:at]:
        last = state.get(uid) if isinstance(state.get(uid), dict) else {}
        when = last.get("at") if isinstance(last.get("at"), (int, float)) else 0
        if last.get("state") in ("exhausted", "invalid") and now - when < RECHECK_EXHAUSTED_S:
            continue
        if _probe_and_record(backup_root, uid, probe_fn, now).state != "ok":
            continue
        with filestore.lock(store_dir(backup_root)):
            if uid_of(read_live()) != current:
                return {"event": "mimo-no-switch", "reason": "live-login-changed", "account": current}
            if not dry_run:
                _switch(backup_root, uid)
        return {"event": "mimo-switch", "from": current, "to": uid, "liveState": live.state,
                "liveStatus": live.status, "dryRun": dry_run}
    return {"event": "mimo-no-switch", "reason": "no-account-answers", "account": current,
            "liveState": live.state, "liveStatus": live.status}
