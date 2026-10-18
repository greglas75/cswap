"""Cursor CLI (``cursor-agent``) account rotation (``cswap cursor …``).

cursor-agent keeps ONE login: the access and refresh tokens (macOS: login keychain,
services ``cursor-access-token`` / ``cursor-refresh-token``, account ``cursor-user``;
Linux: ``~/.config/cursor/auth.json``) plus ``authInfo`` (email, ids) in
``~/.cursor/cli-config.json``. cswap keeps a copy per account under
``<backup root>/cursor/<email>.json`` (0600) and swaps all three pieces.

Usage comes from the endpoint the dashboard reads (``GetCurrentPeriodUsage``): the
plan's ``totalPercentUsed`` and the billing cycle end. Cursor's quota is monthly, so
past the threshold the next account is the one whose cycle ends soonest, among
accounts with room. Access tokens live ~60 days, so idle accounts are readable too.

Never copy one account's stored file to a second machine — log in on each.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from claude_swap import macos_keychain
from claude_swap.real_store_guard import refuse_test_identity

USAGE_URL = "https://api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage"
KEYCHAIN_ACCOUNT = "cursor-user"
ACCESS_SERVICE = "cursor-access-token"
REFRESH_SERVICE = "cursor-refresh-token"
MIN_USEFUL_LIFE_PCT = 5.0
EXPIRED = "access token expired (cursor-agent refreshes it once the account is live)"


class CursorError(Exception):
    """A user-facing failure of a ``cswap cursor`` command."""


def cursor_home() -> Path:
    return Path(os.environ.get("CSWAP_CURSOR_HOME") or "~/.cursor").expanduser()


def config_file() -> Path:
    return cursor_home() / "cli-config.json"


def auth_file() -> Path:
    override = os.environ.get("CSWAP_CURSOR_HOME")
    return Path(override) / "auth.json" if override else Path("~/.config/cursor/auth.json").expanduser()


def use_keychain() -> bool:
    return sys.platform == "darwin" and not os.environ.get("CSWAP_CURSOR_HOME")


def store_dir(backup_root: Path) -> Path:
    return Path(backup_root) / "cursor"


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_private(path: Path, data: dict) -> None:
    """Atomic 0600 write (temp file in the same directory, then rename)."""
    path = Path(path)
    if not path.parent.is_dir():
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _claims(token: str | None) -> dict:
    if not isinstance(token, str) or token.count(".") < 2:
        return {}
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(part))
    except ValueError:
        return {}
    return claims if isinstance(claims, dict) else {}


def email_of(login: dict | None) -> str | None:
    """The account's email — only when authInfo and BOTH tokens name the same
    user (their JWT ``sub`` is authInfo's ``authId``): a half-written login must
    never be stored under the wrong name, so anything unprovable is None."""
    info = (login or {}).get("authInfo") or {}
    email = info.get("email")
    auth_id = info.get("authId")
    if not isinstance(email, str) or not email or not auth_id:
        return None
    for key in ("accessToken", "refreshToken"):
        if _claims((login or {}).get(key)).get("sub") != auth_id:
            return None
    return email.lower()


def read_live(strict: bool = False) -> dict | None:
    """The live login: tokens plus authInfo. ``strict`` raises when the keychain
    cannot be read (callers about to overwrite the login)."""
    if use_keychain():
        try:
            access = macos_keychain.get_password(ACCESS_SERVICE, KEYCHAIN_ACCOUNT)
            refresh = macos_keychain.get_password(REFRESH_SERVICE, KEYCHAIN_ACCOUNT)
        except Exception as e:  # noqa: BLE001
            if strict:
                raise CursorError(f"the keychain is unreadable ({e}) — try again once it is unlocked")
            return None
    else:
        tokens = _read_json(auth_file()) or {}
        access, refresh = tokens.get("accessToken"), tokens.get("refreshToken")
    info = (_read_json(config_file()) or {}).get("authInfo")
    if not access or not isinstance(info, dict):
        return None
    return {"accessToken": access, "refreshToken": refresh, "authInfo": info}


def _read_config() -> dict:
    """cli-config.json, {} only when absent: it holds the user's settings too,
    so an unreadable one is never replaced by a file with authInfo alone."""
    try:
        text = config_file().read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise CursorError(f"{config_file()} is unreadable ({e})")
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise CursorError(f"{config_file()} does not parse — fix or remove it, then retry")
    return data


def _write_pieces(login: dict) -> None:
    if use_keychain():
        macos_keychain.set_password(ACCESS_SERVICE, KEYCHAIN_ACCOUNT, login["accessToken"])
        macos_keychain.set_password(REFRESH_SERVICE, KEYCHAIN_ACCOUNT, login["refreshToken"])
    else:
        _write_private(auth_file(), {"accessToken": login["accessToken"],
                                     "refreshToken": login["refreshToken"]})
    config = _read_config()
    config["authInfo"] = login["authInfo"]
    _write_private(config_file(), config)


def write_live(login: dict, previous: dict | None = None) -> None:
    """Make ``login`` the live one: access + refresh token + authInfo, all of
    one verified account. A failure part-way puts ``previous`` back, so the
    live pieces never describe two accounts."""
    email = email_of(login)
    if email is None:
        raise CursorError("refusing to write a login whose tokens and authInfo disagree")
    refuse_test_identity(email, config_file())
    _read_config()   # fail before touching the tokens, not after
    try:
        _write_pieces(login)
    except BaseException:
        if previous is not None and email_of(previous) is not None:
            with contextlib.suppress(Exception):
                _write_pieces(previous)
        raise


def clear_live() -> None:
    if use_keychain():
        for service in (ACCESS_SERVICE, REFRESH_SERVICE):
            with contextlib.suppress(Exception):
                macos_keychain.delete_password(service, KEYCHAIN_ACCOUNT)
    else:
        with contextlib.suppress(FileNotFoundError):
            auth_file().unlink()
    config = _read_json(config_file())
    if config is not None and "authInfo" in config:
        del config["authInfo"]
        _write_private(config_file(), config)


def stored_path(backup_root: Path, email: str) -> Path:
    email = email.lower()
    if not email or "/" in email or "\\" in email or ".." in email or email.startswith("."):
        raise CursorError(f"not a usable account name: {email!r}")
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
    return sorted(
        p.name[: -len(".json")] for p in root.iterdir()
        if p.name.endswith(".json") and not p.name.startswith(".")
        and email_of(_read_json(p)) == p.name[: -len(".json")]
    )


def _sync_live(backup_root: Path) -> str | None:
    """Copy the live login over its stored copy (cursor-agent refreshed it)."""
    live = read_live()
    email = email_of(live)
    if email is not None and live is not None and stored_path(backup_root, email).exists():
        _write_private(stored_path(backup_root, email), live)
    return email


def sync_live(backup_root: Path) -> str | None:
    with store_lock(backup_root):
        return _sync_live(backup_root)


def add(backup_root: Path) -> str:
    live = read_live(strict=True)
    if live is None:
        raise CursorError("cursor-agent is not logged in — run: cursor-agent login")
    email = email_of(live)
    if email is None:
        raise CursorError("the live Cursor login's tokens and authInfo name different users — "
                          "run `cursor-agent login` again")
    with store_lock(backup_root):
        _write_private(stored_path(backup_root, email), live)
    return email


def login(backup_root: Path) -> str:
    """Store the current login, clear it locally (no `logout`: that may revoke
    the tokens server-side), run `cursor-agent login`, store the new one.
    Without a new login the previous one is put back."""
    if shutil.which("cursor-agent") is None:
        raise CursorError("cursor-agent is not on PATH")
    previous = read_live(strict=True)
    if previous is not None:
        add(backup_root)
    with store_lock(backup_root):   # not mid-switch of the auto daemon
        clear_live()
    fresh = email = None
    try:
        subprocess.call(["cursor-agent", "login"])
        fresh = read_live(strict=True)
        email = email_of(fresh)
    finally:
        if email is None and previous is not None:
            with store_lock(backup_root):
                write_live(previous)
    if email is None:
        raise CursorError("no new login — the previous one is back in place" if previous is not None
                          else "no new login")
    # The same account again (a repaired login) is kept: the old refresh token
    # may be dead server-side now.
    with store_lock(backup_root):
        _write_private(stored_path(backup_root, email), fresh)
    return email


def remove(backup_root: Path, email: str) -> None:
    email = email.lower()
    with store_lock(backup_root):
        path = stored_path(backup_root, email)
        if not path.exists():
            raise CursorError(f"{email} is not stored")
        if email_of(read_live()) == email:
            raise CursorError(f"{email} is the live Cursor login — switch to another one first")
        path.unlink()


@dataclass(frozen=True)
class Usage:
    pct: float | None            # plan usage this billing cycle
    reset_at: float | None       # cycle end (epoch)
    on_demand_used: float | None = None    # dollars
    on_demand_limit: float | None = None
    error: str | None = None

    def life(self, threshold: float) -> float | None:
        if self.error is not None or self.pct is None:
            return None
        return max(0.0, threshold - self.pct)


def parse_usage(payload: dict) -> Usage:
    plan = payload.get("planUsage") if isinstance(payload.get("planUsage"), dict) else {}
    pct = plan.get("totalPercentUsed")
    end = payload.get("billingCycleEnd")
    try:
        reset = float(end) if end is not None else None
    except (TypeError, ValueError):
        reset = None
    if reset is not None and reset > 1e11:   # milliseconds (the reply today)
        reset /= 1000.0
    spend = payload.get("spendLimitUsage") if isinstance(payload.get("spendLimitUsage"), dict) else {}

    def dollars(v) -> float | None:   # cents; int64 may come as a string
        if isinstance(v, bool):
            return None
        try:
            return float(v) / 100.0
        except (TypeError, ValueError):
            return None

    if pct is None and plan:
        pct = 0   # proto3 JSON leaves zero-valued fields out (a fresh cycle)
    if not isinstance(pct, (int, float)) or isinstance(pct, bool) or not 0 <= pct <= 1000:
        return Usage(None, reset, error="no plan usage in the reply")
    return Usage(float(pct), reset, dollars(spend.get("individualUsed")),
                 dollars(spend.get("individualLimit")))


def fetch_usage(login: dict, timeout: float = 20.0) -> Usage:
    token = (login or {}).get("accessToken")
    exp = _claims(token).get("exp")
    if isinstance(exp, (int, float)) and exp <= time.time():
        return Usage(None, None, error=EXPIRED)
    req = urllib.request.Request(
        USAGE_URL, data=b"{}", method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "cswap"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        return Usage(None, None, error=f"http-{e.code}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return Usage(None, None, error=type(e).__name__)
    return parse_usage(payload if isinstance(payload, dict) else {})


def usages_for(backup_root: Path, emails: list[str], current: str | None) -> dict[str, Usage]:
    live = read_live()
    out = {}
    for email in emails:
        login_ = live if email == current and live is not None else _read_json(stored_path(backup_root, email))
        out[email] = fetch_usage(login_) if login_ else Usage(None, None, error="missing")
    return out


def rank(usages: dict[str, Usage], current: str | None, threshold: float) -> list[str]:
    """Targets: accounts with room, soonest cycle end first (that quota expires
    first), then most room; under MIN_USEFUL_LIFE_PCT of room after those.
    An idle account whose access token expired is unreadable but not known to
    be empty: it comes last (cursor-agent refreshes it once it is live).
    Exhausted and otherwise unreadable accounts never."""
    keyed = []
    for email, usage in usages.items():
        if email == current:
            continue
        if usage.error == EXPIRED:
            keyed.append(((2, float("inf"), 0.0), email))
            continue
        life = usage.life(threshold)
        if life is None or life <= 0:
            continue
        keyed.append(((int(life < MIN_USEFUL_LIFE_PCT),
                       usage.reset_at if usage.reset_at is not None else float("inf"),
                       -life), email))
    keyed.sort()
    return [email for _, email in keyed]


def _switch(backup_root: Path, email: str) -> str | None:
    email = email.lower()
    target = _read_json(stored_path(backup_root, email))
    if target is None or email_of(target) != email:
        raise CursorError(f"{email} is not stored — log in with it and run: cswap cursor add")
    live = read_live(strict=True)
    live_email = email_of(live)
    if live is not None and live_email is None:
        raise CursorError("the live Cursor login cannot be identified — not overwriting it")
    if live_email is not None and live_email != email and not stored_path(backup_root, live_email).exists():
        raise CursorError(f"the live login {live_email} is not stored — run `cswap cursor add` first")
    previous = _sync_live(backup_root)
    if previous != email:
        write_live(target, previous=live)
    return previous


def switch(backup_root: Path, email: str) -> str | None:
    with store_lock(backup_root):
        return _switch(backup_root, email)


def auto_tick(backup_root: Path, threshold: float, *, dry_run: bool = False,
              usages_fn=usages_for) -> dict:
    with store_lock(backup_root):
        current = _sync_live(backup_root)
        emails = stored_accounts(backup_root)
    if current is None or current not in emails:
        return {"event": "cursor-no-switch", "reason": "live-login-not-stored", "live": current}
    # The network calls (20 s timeout each) run outside the lock; the decision
    # is re-checked under it, in case a manual switch landed meanwhile.
    usages = usages_fn(backup_root, emails, current)
    with store_lock(backup_root):
        if email_of(read_live()) != current:
            return {"event": "cursor-no-switch", "reason": "live-login-changed", "account": current}
        live = usages[current]
        if live.error is not None:
            return {"event": "cursor-no-switch", "reason": "active-usage-unknown", "detail": live.error}
        if live.pct is not None and live.pct < threshold:
            return {"event": "cursor-no-switch", "reason": "below-threshold", "account": current,
                    "usedPct": live.pct}
        order = rank(usages, current, threshold)
        if not order:
            return {"event": "cursor-no-switch", "reason": "no-account-with-room", "account": current,
                    "usedPct": live.pct}
        if not dry_run:
            _switch(backup_root, order[0])
        return {"event": "cursor-switch", "from": current, "to": order[0], "usedPct": live.pct,
                "dryRun": dry_run}
