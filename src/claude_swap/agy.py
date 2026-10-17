"""Antigravity CLI (``agy``) account rotation (``cswap agy …``).

agy keeps ONE Google login. On macOS the authority is the login keychain item
service ``gemini`` / account ``antigravity`` (go-keyring: ``go-keyring-base64:``
+ base64 JSON); ``~/.gemini/jetski-standalone-oauth-token`` holds the same JSON
and is what agy reads where no keychain exists (Linux). cswap keeps a copy per
account under ``<backup root>/agy/<email>.json`` (0600) and swaps the live one.

Quota comes from the endpoint agy itself reads (``fetchAvailableModels``): a
``remainingFraction`` and ``resetTime`` per model. An account's life is its
tightest model (``autoswitch.agyModels`` narrows which ones count). Only the
LIVE account can be read: an idle account's access token expires within the
hour and agy's OAuth client secret is not ours to lift, so each account's last
reading is kept (``quota.json``) and an account last seen exhausted is no
target until that reading's reset time.

Never copy one account's stored file to a second machine — log in on each.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from claude_swap import macos_keychain
from claude_swap.real_store_guard import refuse_test_identity

API = "https://daily-cloudcode-pa.googleapis.com/v1internal:"
# The endpoint answers 403 to any other client name.
USER_AGENT = "antigravity"
KEYCHAIN_SERVICE = "gemini"
KEYCHAIN_ACCOUNT = "antigravity"
KEYRING_PREFIX = "go-keyring-base64:"
TOKEN_FILE = "jetski-standalone-oauth-token"
# agy 1.4 on Linux keeps the login here instead (measured 2026-10-16, ryzen-dev).
CLI_TOKEN_FILE = "antigravity-cli/antigravity-oauth-token"
QUOTA_STATE = "quota.json"
# A reading without a usable reset time stops describing the account after this.
READING_MAX_AGE_S = 24 * 3600


class AgyError(Exception):
    """A user-facing failure of a ``cswap agy`` command."""


def gemini_home() -> Path:
    return Path(os.environ.get("CSWAP_AGY_HOME") or "~/.gemini").expanduser()


def live_files() -> list[Path]:
    """Every token file agy may read; the newest existing one is live."""
    found = []
    for p in (gemini_home() / TOKEN_FILE, gemini_home() / CLI_TOKEN_FILE):
        try:
            found.append((p.stat().st_mtime, p))   # one stat: agy may rewrite it meanwhile
        except OSError:
            continue
    return [p for _, p in sorted(found, reverse=True)]


def live_file() -> Path:
    files = live_files()
    if files:
        return files[0]
    # Nothing on disk (a login just cleared): where this platform's agy reads.
    return gemini_home() / (TOKEN_FILE if sys.platform == "darwin" else CLI_TOKEN_FILE)


def use_keychain() -> bool:
    return sys.platform == "darwin" and not os.environ.get("CSWAP_AGY_HOME")


def store_dir(backup_root: Path) -> Path:
    return Path(backup_root) / "agy"


def _parse(raw: str | None) -> dict | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith(KEYRING_PREFIX):
        try:
            raw = base64.b64decode(raw[len(KEYRING_PREFIX):]).decode("utf-8")
        except ValueError:
            return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("token"), dict) else None


def read_file(path: Path) -> dict | None:
    try:
        return _parse(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return None


def read_live(strict: bool = False) -> dict | None:
    """The live login. On macOS the keychain is the authority: an unreadable
    keychain (locked, denied) is "unknown", never the possibly stale file —
    ``strict`` raises instead, for callers about to overwrite the login."""
    if use_keychain():
        try:
            raw = macos_keychain.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
        except Exception as e:  # noqa: BLE001
            if strict:
                raise AgyError(f"the keychain is unreadable ({e}) — try again once it is unlocked")
            return None
        if raw is not None:
            return _parse(raw)
    return read_file(live_file())


def email_of(data: dict | None) -> str | None:
    token = (data or {}).get("id_token")
    if not isinstance(token, str) or token.count(".") < 2:
        return None
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(part))
    except ValueError:
        return None
    email = claims.get("email") if isinstance(claims, dict) else None
    return email.lower() if isinstance(email, str) and email else None


def write_private(path: Path, data: dict) -> None:
    """Atomic 0600 write (temp file in the same directory, then rename)."""
    path = Path(path)
    refuse_test_identity(email_of(data), path)
    if not path.parent.is_dir():
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def write_live(data: dict, files: list[Path] | None = None) -> None:
    """The keychain item (macOS) and the token file both get the login."""
    if use_keychain():
        refuse_test_identity(email_of(data), live_file())
        encoded = base64.b64encode(json.dumps(data).encode("utf-8")).decode("ascii")
        macos_keychain.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, KEYRING_PREFIX + encoded)
    # Both files agy versions read, when present: a stale one left behind
    # would be read back as the live login by a version that prefers it.
    for path in files or live_files() or [live_file()]:
        write_private(path, data)


def clear_live() -> None:
    if use_keychain():
        with contextlib.suppress(Exception):
            macos_keychain.delete_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    for path in live_files():
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def stored_path(backup_root: Path, email: str) -> Path:
    email = email.lower()
    if not email or "/" in email or "\\" in email or ".." in email or email.startswith("."):
        raise AgyError(f"not a usable account name: {email!r}")
    return store_dir(backup_root) / f"{email}.json"


@contextlib.contextmanager
def store_lock(backup_root: Path):
    d = store_dir(backup_root)
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    with open(d / ".lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def stored_accounts(backup_root: Path) -> list[str]:
    root = store_dir(backup_root)
    if not root.is_dir():
        return []
    # Only files that hold the login they are named after: other tools have
    # dropped their own JSON here (2026-10-16: `*.token.json`, probe-state.json).
    return sorted(
        p.name[: -len(".json")]
        for p in root.iterdir()
        if p.name.endswith(".json") and not p.name.startswith(".")
        and email_of(read_file(p)) == p.name[: -len(".json")]
    )


def _sync_live(backup_root: Path) -> str | None:
    """Copy the live login over its stored copy (agy refreshed it); return its email."""
    live = read_live()
    email = email_of(live)
    if email is not None and live is not None and stored_path(backup_root, email).exists():
        write_private(stored_path(backup_root, email), live)
    return email


def sync_live(backup_root: Path) -> str | None:
    with store_lock(backup_root):
        return _sync_live(backup_root)


def add(backup_root: Path) -> str:
    live = read_live()
    email = email_of(live)
    if live is None or email is None:
        raise AgyError("agy is not logged in — run `agy` once and sign in")
    with store_lock(backup_root):
        write_private(stored_path(backup_root, email), live)
    return email


def login(backup_root: Path) -> str:
    """Store the current login, clear it, let agy sign in another account, store that.

    Without a new sign-in the previous login is put back.
    """
    if shutil.which("agy") is None:
        raise AgyError("agy is not on PATH")
    previous = read_live(strict=True)
    if previous is not None:
        add(backup_root)
    # Put a restored login back where this agy version reads it.
    were = live_files()
    clear_live()
    fresh = None
    try:
        print("Sign in with the other Google account in agy, then exit it (Ctrl-D or /quit).")
        subprocess.call(["agy"])
        fresh = read_live()
    finally:
        # Ctrl-C or a crash in agy must not leave the machine logged out.
        email = email_of(fresh)
        if (fresh is None or email is None or (previous is not None and email == email_of(previous))) \
                and previous is not None:
            write_live(previous, were)
    if fresh is None or email is None or (previous is not None and email == email_of(previous)):
        raise AgyError("no new login — the previous one is back in place")
    with store_lock(backup_root):
        write_private(stored_path(backup_root, email), fresh)
    return email


@dataclass(frozen=True)
class Quota:
    life: float | None          # pct left in the tightest counted model
    model: str | None           # that model
    reset_at: float | None      # its reset (epoch)
    error: str | None = None


def _ts(value) -> float | None:
    """RFC 3339 → epoch. Nanosecond fractions are cut to micro; no offset = UTC."""
    if not isinstance(value, str):
        return None
    text = re.sub(r"(\.\d{6})\d+", r"\1", value.strip()).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _expired(data: dict) -> bool:
    expiry = _ts((data.get("token") or {}).get("expiry"))
    return expiry is not None and expiry <= time.time()


def _post(path: str, token: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                 "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.load(resp)
    return payload if isinstance(payload, dict) else {}


def parse_models(payload: dict, models: list[str]) -> Quota:
    """Tightest model with a reset time; ``models`` (prefixes) narrows the set."""
    worst: Quota | None = None
    entries = payload.get("models")
    if not isinstance(entries, dict):
        return Quota(None, None, None, error="unexpected quota response")
    for name, info in entries.items():
        quota = (info or {}).get("quotaInfo") if isinstance(info, dict) else None
        if not isinstance(quota, dict) or "resetTime" not in quota:
            continue   # internal tab/chat models: no quota of their own
        if models and not any(name.startswith(m) for m in models):
            continue
        fraction = quota.get("remainingFraction", 0)
        if not isinstance(fraction, (int, float)):
            continue
        reset = _ts(quota["resetTime"])
        life = max(0.0, min(100.0, float(fraction) * 100.0))
        if worst is None or life < (worst.life or 0.0):
            worst = Quota(life, name, reset)
    return worst or Quota(None, None, None, error="no counted model reported a quota")


def fetch_quota(data: dict, models: list[str], timeout: float = 20.0) -> Quota:
    if _expired(data):
        return Quota(None, None, None, error="access token expired (agy refreshes it on its next run)")
    token = (data.get("token") or {}).get("access_token")
    if not isinstance(token, str):
        return Quota(None, None, None, error="no access token")
    try:
        assist = _post("loadCodeAssist", token, {"metadata": {"ideType": "ANTIGRAVITY"}}, timeout)
        project = assist.get("cloudaicompanionProject")
        payload = _post("fetchAvailableModels", token, {"project": project} if project else {}, timeout)
    except urllib.error.HTTPError as e:
        return Quota(None, None, None, error=f"http-{e.code}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return Quota(None, None, None, error=type(e).__name__)
    return parse_models(payload, models)


def _state_path(backup_root: Path) -> Path:
    return store_dir(backup_root) / QUOTA_STATE


def load_state(backup_root: Path) -> dict:
    try:
        data = json.loads(_state_path(backup_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record(backup_root: Path, email: str, quota: Quota, now: float) -> None:
    state = load_state(backup_root)
    state[email] = {"life": quota.life, "model": quota.model, "resetAt": quota.reset_at, "at": now}
    path = _state_path(backup_root)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".quota.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


def known_life(entry: dict | None, now: float) -> float | None:
    """An account's last reading while it still describes the window: after
    its reset time the account counts as fresh (100); never read = None."""
    if not isinstance(entry, dict) or not isinstance(entry.get("life"), (int, float)):
        return None
    reset = entry.get("resetAt")
    if isinstance(reset, (int, float)):
        if reset <= now:
            return 100.0
    elif now - float(entry.get("at") or 0) > READING_MAX_AGE_S:
        return None   # no reset time: an old reading says nothing any more
    return float(entry["life"])


def rank(emails: list[str], current: str | None, state: dict, threshold: float, now: float) -> list[str]:
    """Targets: never-read and recovered accounts and those with room, most
    life first; an account last seen past the threshold waits for its reset."""
    keyed = []
    for email in emails:
        if email == current:
            continue
        life = known_life(state.get(email), now)
        if life is not None and 100.0 - life >= threshold:
            continue
        keyed.append((-(life if life is not None else 100.0), email))
    keyed.sort()
    return [email for _, email in keyed]


def _switch(backup_root: Path, email: str) -> str | None:
    email = email.lower()
    target = read_file(stored_path(backup_root, email))
    if target is None or email_of(target) != email:
        raise AgyError(f"{email} is not stored — sign in with it and run: cswap agy add")
    live = read_live(strict=True)
    live_email = email_of(live)
    if live is None and live_file().exists():
        raise AgyError(f"the live login in {live_file()} is unreadable — switching would replace it")
    if live_email is not None and live_email != email and not stored_path(backup_root, live_email).exists():
        raise AgyError(f"the live login {live_email} is not stored — run `cswap agy add` first")
    previous = _sync_live(backup_root)
    if previous != email:
        write_live(target)
    return previous


def switch(backup_root: Path, email: str) -> str | None:
    with store_lock(backup_root):
        return _switch(backup_root, email)


def auto_tick(backup_root: Path, threshold: float, models: list[str], *, dry_run: bool = False,
              fetch=fetch_quota, now: float | None = None) -> dict:
    with store_lock(backup_root):
        now = time.time() if now is None else now
        current = _sync_live(backup_root)
        emails = stored_accounts(backup_root)
        if current is None or current not in emails:
            return {"event": "agy-no-switch", "reason": "live-login-not-stored", "live": current}
        quota = fetch(read_live() or {}, models)
        if quota.error is not None:
            return {"event": "agy-no-switch", "reason": "active-quota-unknown", "detail": quota.error}
        record(backup_root, current, quota, now)
        used = 100.0 - (quota.life or 0.0)
        if used < threshold:
            return {"event": "agy-no-switch", "reason": "below-threshold", "account": current,
                    "usedPct": round(used, 1), "model": quota.model}
        order = rank(emails, current, load_state(backup_root), threshold, now)
        if not order:
            return {"event": "agy-no-switch", "reason": "no-account-with-room", "account": current,
                    "usedPct": round(used, 1), "model": quota.model}
        if not dry_run:
            _switch(backup_root, order[0])
        return {"event": "agy-switch", "from": current, "to": order[0], "usedPct": round(used, 1),
                "model": quota.model, "dryRun": dry_run}
