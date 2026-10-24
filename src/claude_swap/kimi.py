"""Kimi Code CLI (``kimi``) account rotation (``cswap kimi …``).

``kimi`` keeps its login as one OAuth token file,
``~/.kimi-code/credentials/<key>.json`` (``access_token`` valid 15 min,
``refresh_token`` that rotates on every refresh). ``<key>`` comes from
``config.toml`` (``providers."managed:kimi-code".oauth.key``). Every kimi
process re-reads that file before each request and refreshes it under the lock
directory ``~/.kimi-code/oauth/<key>.lock`` (proper-lockfile: a lock older than
5 s is stale), so a switch reaches running sessions on their next request.
cswap writes the live file under the same lock.

cswap keeps a copy per account (the token's ``sub``) under
``<backup root>/kimi/<user id>.json``. Usage comes from the coding API's
``/usages`` (``limit_5h``, ``limit_7d``, ``limit_month_*``: used ratio and reset
time); an idle account's stored token is refreshed by cswap to read it. Past
the threshold the next account is the one with room whose longest window
resets soonest.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import platform
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from claude_swap import filestore
from claude_swap.real_store_guard import refuse_test_identity

PROVIDER = "managed:kimi-code"
DEFAULT_OAUTH_HOST = "https://auth.kimi.com"
DEFAULT_BASE_URL = "https://api.kimi.com/coding/v1"
CLIENT_ID = "17e5f671-d194-4dfb-9706-5516cb48c098"
CLIENT_VERSION = "2.3.1"
REFRESH_MARGIN_S = 120
LOCK_STALE_S = 5.0
LOCK_WAIT_S = 60.0
MIN_USEFUL_LIFE_PCT = 5.0
# Longest window first: its reset decides which account to use up first.
WINDOWS = (("limit_month_total", "month"), ("limit_month_code", "month code"),
           ("limit_7d", "7d"), ("limit_5h", "5h"))


class KimiError(Exception):
    """A user-facing failure of a ``cswap kimi`` command."""


def home() -> Path:
    return Path(os.environ.get("CSWAP_KIMI_HOME") or "~/.kimi-code").expanduser()


def store_dir(backup_root: Path) -> Path:
    return Path(backup_root) / "kimi"


@dataclass(frozen=True)
class Env:
    """Where the live login lives and which servers it belongs to."""
    key: str
    oauth_host: str
    base_url: str


def live_env(kimi_home: Path | None = None) -> Env:
    path = (kimi_home or home()) / "config.toml"
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        config = {}
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise KimiError(f"{path} is unreadable ({e})")
    provider = (config.get("providers") or {}).get(PROVIDER) or {}
    oauth = provider.get("oauth") or {}
    key = str(oauth.get("key") or "oauth/kimi-code")
    # Kimi's resolveKimiTokenStorageName.
    name = "kimi-code" if key in ("kimi-code", "oauth/kimi-code") else key.removeprefix("oauth/")
    filestore.safe_name(name, KimiError)
    return Env(name, str(oauth.get("oauth_host") or DEFAULT_OAUTH_HOST).rstrip("/"),
               str(provider.get("base_url") or DEFAULT_BASE_URL).rstrip("/"))


def live_file(env: Env, kimi_home: Path | None = None) -> Path:
    return (kimi_home or home()) / "credentials" / f"{env.key}.json"


def claims(token: dict | None) -> dict:
    """The unverified JWT payload of the access token (or the refresh token)."""
    for field in ("access_token", "refresh_token"):
        parts = str((token or {}).get(field) or "").split(".")
        if len(parts) != 3:
            continue
        try:
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def user_of(token: dict | None) -> str | None:
    """The account a token file belongs to; None for a revoked tombstone."""
    if not isinstance(token, dict) or not token.get("refresh_token"):
        return None
    sub = claims(token).get("sub")
    return sub if isinstance(sub, str) and sub else None


def account_user(account: dict | None) -> str | None:
    return user_of(account.get("token")) if isinstance(account, dict) else None


@contextlib.contextmanager
def kimi_lock(env: Env, kimi_home: Path | None = None):
    """Kimi's own refresh lock: the directory ``oauth/<key>.lock``, its mtime
    kept fresh while held (kimi takes over a lock older than 5 s)."""
    target = (kimi_home or home()) / "oauth" / env.key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch(exist_ok=True)
    lockdir = Path(f"{target}.lock")
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            lockdir.mkdir()
            break
        except FileExistsError:
            with contextlib.suppress(FileNotFoundError):
                if time.time() - lockdir.stat().st_mtime > LOCK_STALE_S:
                    lockdir.rmdir()
                    continue
            if time.monotonic() > deadline:
                raise KimiError(f"kimi holds its login lock ({lockdir}) — try again")
            time.sleep(0.25)
    stop = threading.Event()

    def keep_fresh():
        while not stop.wait(1.0):
            with contextlib.suppress(OSError):
                os.utime(lockdir)

    keeper = threading.Thread(target=keep_fresh, daemon=True)
    keeper.start()
    try:
        yield
    finally:
        stop.set()
        keeper.join()
        with contextlib.suppress(OSError):
            lockdir.rmdir()


def read_live() -> dict | None:
    """The live account: ``{"token": …, "oauthHost": …, "baseUrl": …}``."""
    env = live_env()
    token = filestore.read_json(live_file(env))
    if token is None:
        return None
    return {"token": token, "oauthHost": env.oauth_host, "baseUrl": env.base_url}


def stored_path(backup_root: Path, user: str) -> Path:
    return store_dir(backup_root) / f"{filestore.safe_name(user, KimiError)}.json"


def stored_account(backup_root: Path, user: str) -> dict | None:
    return filestore.read_json(stored_path(backup_root, user))


def stored_accounts(backup_root: Path) -> list[str]:
    root = store_dir(backup_root)
    if not root.is_dir():
        return []
    return sorted(p.name[: -len(".json")] for p in root.iterdir()
                  if p.name.endswith(".json") and not p.name.startswith(".")
                  and account_user(filestore.read_json(p)) == p.name[: -len(".json")])


def _store(backup_root: Path, user: str, account: dict) -> None:
    """Store ``account``, keeping the label (email) a previous copy has."""
    old = filestore.read_json(stored_path(backup_root, user)) or {}
    label = {"email": old["email"]} if old.get("email") and not account.get("email") else {}
    filestore.write_private(stored_path(backup_root, user), {**account, **label})


def _sync_live(backup_root: Path) -> str | None:
    """Copy the live token over its stored one — kimi rotates the refresh
    token, so only the newest copy can still refresh. A tombstone is never
    stored."""
    live = read_live()
    user = account_user(live)
    if user is not None and stored_path(backup_root, user).exists():
        _store(backup_root, user, live)
    return user


def sync_live(backup_root: Path) -> str | None:
    with filestore.lock(store_dir(backup_root)):
        return _sync_live(backup_root)


def add(backup_root: Path) -> str:
    live = read_live()
    user = account_user(live)
    if user is None:
        raise KimiError("kimi is not logged in (or its login was revoked) — run kimi, then /login")
    with filestore.lock(store_dir(backup_root)):
        _store(backup_root, user, live)
    return user


def _check_writable(env: Env, account: dict) -> None:
    user = account_user(account)
    if user is None:
        raise KimiError("refusing to write a token without an account")
    refuse_test_identity(user, live_file(env))
    if account.get("oauthHost") != env.oauth_host:
        raise KimiError(f"{user} was logged in on {account.get('oauthHost')}, kimi is set up for "
                        f"{env.oauth_host} — not mixing regions")


def write_live(account: dict) -> None:
    """Put ``account``'s token in as kimi's login, under kimi's lock."""
    env = live_env()
    _check_writable(env, account)
    with kimi_lock(env):
        filestore.write_private(live_file(env), account["token"])


def remove(backup_root: Path, user: str) -> None:
    with filestore.lock(store_dir(backup_root)):
        path = stored_path(backup_root, user)
        if not path.exists():
            raise KimiError(f"{user} is not stored")
        if account_user(read_live()) == user:
            raise KimiError(f"{user} is the live kimi login — switch to another one first")
        path.unlink()


def _switch(backup_root: Path, user: str) -> str | None:
    target = filestore.read_json(stored_path(backup_root, user))
    if account_user(target) != user:
        raise KimiError(f"{user} is not stored — log in with it and run: cswap kimi add")
    env = live_env()
    _check_writable(env, target)
    # Saving the live token and replacing it happen under kimi's lock: a kimi
    # refresh in between would rotate a refresh token cswap never stores.
    with kimi_lock(env):
        live_user = account_user(read_live())
        if live_user is not None and not stored_path(backup_root, live_user).exists():
            raise KimiError(f"the live kimi login {live_user} is not stored — run `cswap kimi add` first")
        previous = _sync_live(backup_root)
        if previous != user:
            filestore.write_private(live_file(env), target["token"])
    return previous


def switch(backup_root: Path, user: str) -> str | None:
    with filestore.lock(store_dir(backup_root)):
        return _switch(backup_root, user)


def _headers() -> dict:
    try:
        device = (home() / "device_id").read_text(encoding="utf-8").strip()
    except OSError:
        device = ""
    # Cloudflare in front of the API refuses a bare urllib User-Agent (error 1010).
    headers = {"User-Agent": f"kimi-code-cli/{CLIENT_VERSION}", "Accept": "application/json",
               "X-Msh-Platform": "kimi_code_cli", "X-Msh-Version": CLIENT_VERSION,
               "X-Msh-Device-Name": socket.gethostname().encode("ascii", "ignore").decode() or "unknown",
               "X-Msh-Os-Version": platform.release()}
    if device:
        headers["X-Msh-Device-Id"] = device
    return headers


def refresh(account: dict, timeout: float = 30.0) -> dict:
    """A new token pair for ``account`` (the old refresh token is spent)."""
    data = urllib.parse.urlencode({"client_id": CLIENT_ID, "grant_type": "refresh_token",
                                   "refresh_token": account["token"]["refresh_token"]}).encode()
    req = urllib.request.Request(account["oauthHost"] + "/api/oauth/token", data=data, method="POST",
                                 headers={**_headers(), "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise KimiError(f"token refresh refused (HTTP {e.code})") from e
    except (OSError, ValueError) as e:
        raise KimiError(f"token refresh failed ({type(e).__name__})") from e
    if not isinstance(payload, dict) or not payload.get("access_token") or not payload.get("refresh_token"):
        raise KimiError("token refresh gave no token pair")
    try:
        expires_in = int(payload.get("expires_in") or 0)
    except (TypeError, ValueError):
        expires_in = 0   # expires_at = now: the next use refreshes again
    return {**account, "token": {
        "access_token": payload["access_token"], "refresh_token": payload["refresh_token"],
        "expires_at": int(time.time()) + expires_in, "scope": payload.get("scope", ""),
        "token_type": payload.get("token_type", "Bearer"), "expires_in": expires_in}}


def needs_refresh(account: dict, now: float) -> bool:
    expires_at = account["token"].get("expires_at")
    return not isinstance(expires_at, (int, float)) or expires_at - now < REFRESH_MARGIN_S


@dataclass(frozen=True)
class Usage:
    pct: float | None                 # the most-used window, 0-100
    reset_at: float | None            # reset of the longest window
    windows: tuple = ()               # ((label, pct, reset_at), …)
    error: str | None = None
    no_plan: bool = False             # counted as used up: Kimi Code refuses the account

    def life(self, threshold: float) -> float | None:
        if self.error is not None or self.pct is None:
            return None
        return max(0.0, threshold - self.pct)


def _ts(value) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_usage(payload: dict) -> Usage:
    raw = payload.get("usages") if isinstance(payload, dict) else None
    if isinstance(payload, dict) and not raw and set(payload) <= {"usages"}:
        # A Free account gets `{}` here and 403 "does not have access to Kimi
        # Code" on every request (measured 2026-10-24).
        return Usage(100.0, None, no_plan=True)
    windows = []
    for key, label in WINDOWS:
        entry = raw.get(key) if isinstance(raw, dict) else None
        ratio = entry.get("used_ratio") if isinstance(entry, dict) else None
        if isinstance(ratio, str):
            try:
                ratio = float(ratio)
            except ValueError:
                ratio = None
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not 0 <= ratio <= 10:
            continue
        windows.append((label, float(ratio) * 100.0, _ts(entry.get("reset_time"))))
    if not windows:
        return Usage(None, None, error="no usage windows in the reply")
    return Usage(max(w[1] for w in windows), windows[0][2], tuple(windows))


def fetch_usage(account: dict, timeout: float = 20.0) -> Usage:
    req = urllib.request.Request(account["baseUrl"] + "/usages", headers={
        **_headers(), "Authorization": f"Bearer {account['token']['access_token']}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return parse_usage(json.loads(resp.read()))
    except urllib.error.HTTPError as e:
        return Usage(None, None, error=f"HTTP {e.code}")
    except (OSError, ValueError) as e:
        return Usage(None, None, error=type(e).__name__)


def fetch_email(account: dict, timeout: float = 20.0) -> str | None:
    """The account's email (or nickname) from the coding API's /me — a label only."""
    req = urllib.request.Request(account["baseUrl"] + "/me", headers={
        **_headers(), "Authorization": f"Bearer {account['token']['access_token']}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            me = json.loads(resp.read())
    except (OSError, ValueError):
        return None
    label = me.get("email") or me.get("nickname") if isinstance(me, dict) else None
    return label if isinstance(label, str) and label else None


def _fresh_live(backup_root: Path, user: str, now: float) -> dict | None:
    """The live account with a usable access token: refreshed the way kimi
    does it (under its lock, re-read first), so a running kimi never spends
    the same refresh token."""
    env = live_env()
    with kimi_lock(env):
        live = read_live()
        if account_user(live) != user:
            return None
        if needs_refresh(live, now):
            live = refresh(live)
            filestore.write_private(live_file(env), live["token"])
    with filestore.lock(store_dir(backup_root)):
        if stored_path(backup_root, user).exists():
            _store(backup_root, user, live)
    return live


def _fresh_stored(backup_root: Path, user: str, now: float) -> dict | None:
    with filestore.lock(store_dir(backup_root)):
        account = filestore.read_json(stored_path(backup_root, user))
        if account_user(account) != user or account_user(read_live()) == user:
            return None   # gone, or kimi uses it now: only kimi refreshes the live login
        if needs_refresh(account, now):
            account = refresh(account)
            filestore.write_private(stored_path(backup_root, user), account)
    return account


def usages_for(backup_root: Path, users: list[str], current: str | None, *, clock=time.time,
               fetch_fn=None, email_fn=None) -> dict[str, Usage]:
    fetch_fn = fetch_fn or fetch_usage
    email_fn = email_fn or fetch_email
    out = {}
    for user in users:
        try:
            account = (_fresh_live if user == current else _fresh_stored)(backup_root, user, clock())
        except KimiError as e:
            out[user] = Usage(None, None, error=str(e))
            continue
        out[user] = fetch_fn(account) if account else Usage(None, None, error="login changed meanwhile")
        if account and not account.get("email"):
            email = email_fn(account)
            if email:
                with filestore.lock(store_dir(backup_root)):
                    stored = filestore.read_json(stored_path(backup_root, user))
                    if account_user(stored) == user:
                        _store(backup_root, user, {**stored, "email": email})
    return out


def rank(usages: dict[str, Usage], current: str | None, threshold: float) -> list[str]:
    """Accounts with room, soonest reset of the longest window first, then most room."""
    keyed = []
    for user, usage in usages.items():
        life = usage.life(threshold)
        if user == current or life is None or life <= 0:
            continue
        keyed.append(((life < MIN_USEFUL_LIFE_PCT,
                       usage.reset_at if usage.reset_at is not None else float("inf"), -life), user))
    keyed.sort()
    return [user for _, user in keyed]


def auto_tick(backup_root: Path, threshold: float, *, dry_run: bool = False, usages_fn=usages_for) -> dict:
    with filestore.lock(store_dir(backup_root)):
        current = _sync_live(backup_root)
        users = stored_accounts(backup_root)
    if current is None or current not in users:
        return {"event": "kimi-no-switch", "reason": "live-login-not-stored", "live": current}
    usages = usages_fn(backup_root, users, current)   # takes the store lock per account only
    with filestore.lock(store_dir(backup_root)):
        if account_user(read_live()) != current:
            return {"event": "kimi-no-switch", "reason": "live-login-changed", "account": current}
        live = usages[current]
        if live.error is not None:
            return {"event": "kimi-no-switch", "reason": "active-usage-unknown", "detail": live.error}
        if live.pct < threshold:
            return {"event": "kimi-no-switch", "reason": "below-threshold", "account": current,
                    "usedPct": live.pct}
        order = rank(usages, current, threshold)
        if not order:
            return {"event": "kimi-no-switch", "reason": "no-account-with-room", "account": current,
                    "usedPct": live.pct}
        if not dry_run:
            _switch(backup_root, order[0])
        return {"event": "kimi-switch", "from": current, "to": order[0], "usedPct": live.pct,
                "dryRun": dry_run}


def _binary() -> str:
    found = shutil.which("kimi") or str(Path("~/.kimi-code/bin/kimi").expanduser())
    if not Path(found).exists():
        raise KimiError("kimi is not installed")
    return found


def login(backup_root: Path) -> str:
    """Store the current login; run ``kimi login`` under a temporary
    KIMI_CODE_HOME (the live login is untouched while you sign in); store the
    new account and make it live."""
    binary = _binary()
    env = live_env()
    with filestore.lock(store_dir(backup_root)):
        previous = read_live()
        if account_user(previous) is not None:
            _store(backup_root, account_user(previous), previous)
    region = "mainland-cn" if env.oauth_host == DEFAULT_OAUTH_HOST else "global"
    with tempfile.TemporaryDirectory(prefix="cswap-kimi-login-") as tmp:
        with contextlib.suppress(OSError):   # the same device as the live login
            shutil.copy2(home() / "device_id", Path(tmp) / "device_id")
        rc = subprocess.call([binary, "login", "--region", region], env={**os.environ, "KIMI_CODE_HOME": tmp})
        # The region decides the servers; the file name kimi chose there does not matter.
        found = sorted((Path(tmp) / "credentials").glob("*.json"))
        token = filestore.read_json(found[0]) if len(found) == 1 else None
    fresh = {"token": token, "oauthHost": env.oauth_host, "baseUrl": env.base_url}
    user = account_user(fresh)
    if rc != 0 or user is None:
        raise KimiError("no new login (kimi login gave no token) — nothing changed")
    with filestore.lock(store_dir(backup_root)):
        _store(backup_root, user, fresh)
        # kimi kept running during the sign-in and may have rotated the live
        # token since it was stored above: _switch stores it again first.
        _switch(backup_root, user)
    return user
