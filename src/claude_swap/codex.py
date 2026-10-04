"""Codex CLI account rotation (``cswap codex …``).

Codex keeps ONE login in ``$CODEX_HOME/auth.json`` (default ``~/.codex``): a
ChatGPT OAuth id/access/refresh token set. A second ``codex login`` simply
overwrites it — which is how the owner lost a Pro login on 2026-09-29. cswap
keeps a copy per account under ``<backup root>/codex/<email>.auth.json`` (0600)
and swaps the live file.

Refresh tokens rotate, so the LIVE file is the authority for whichever account
it holds: every command first copies it back over that account's stored copy,
and a stored copy is only ever written INTO the live file by a switch. Never
copy one account's file to a second machine — the two would rotate the same
refresh token and log each other out; log in on each machine instead.

Usage comes from the same endpoint Codex's own ``/status`` reads
(``/wham/usage``). Codex has a weekly window (and, on some plans, a shorter
one); the weekly one switches at ``autoswitch.weeklyThreshold`` (falling back
to ``threshold``), a shorter one at ``threshold``. Past the threshold the next
account is the one whose weekly quota expires soonest, among accounts with at
least MIN_USEFUL_LIFE_PCT of life — the same rule the Claude side uses.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
MIN_USEFUL_LIFE_PCT = 10.0
# A window this long or shorter is a "short" window (5h class); longer ones
# are weekly.
SHORT_WINDOW_MAX_S = 24 * 3600


class CodexError(Exception):
    """A user-facing failure of a ``cswap codex`` command."""


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()


def live_auth_path() -> Path:
    return codex_home() / "auth.json"


def store_dir(backup_root: Path) -> Path:
    return Path(backup_root) / "codex"


def read_auth(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _jwt_claims(token: str | None) -> dict:
    if not isinstance(token, str) or token.count(".") < 2:
        return {}
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(part))
    except (ValueError, json.JSONDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def email_of(auth: dict | None) -> str | None:
    tokens = (auth or {}).get("tokens")
    if not isinstance(tokens, dict):
        return None
    email = _jwt_claims(tokens.get("id_token")).get("email")
    return email.lower() if isinstance(email, str) and email else None


def plan_of(auth: dict | None) -> str | None:
    tokens = (auth or {}).get("tokens") or {}
    claims = _jwt_claims(tokens.get("id_token"))
    plan = (claims.get("https://api.openai.com/auth") or {}).get("chatgpt_plan_type")
    return plan if isinstance(plan, str) else None


def write_private(path: Path, data: dict) -> None:
    """Atomic 0600 write (temp file in the same directory, then rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def stored_path(backup_root: Path, email: str) -> Path:
    return store_dir(backup_root) / f"{email.lower()}.auth.json"


def stored_accounts(backup_root: Path) -> list[str]:
    root = store_dir(backup_root)
    if not root.is_dir():
        return []
    return sorted(
        p.name[: -len(".auth.json")]
        for p in root.iterdir()
        if p.name.endswith(".auth.json") and not p.name.startswith(".")
    )


def sync_live(backup_root: Path) -> str | None:
    """Copy the live login over its account's stored copy; return its email.

    Only for accounts already stored (``add`` registers new ones), so a stray
    ``codex login`` into an unrelated account is not silently adopted. The
    live copy always wins: it holds the newest rotated refresh token.
    """
    live = read_auth(live_auth_path())
    email = email_of(live)
    if email is None:
        return None
    if stored_path(backup_root, email).exists():
        write_private(stored_path(backup_root, email), live)
    return email


def add(backup_root: Path) -> tuple[str, str | None]:
    live = read_auth(live_auth_path())
    if live is None:
        raise CodexError(f"no Codex login at {live_auth_path()} — run: codex login")
    email = email_of(live)
    if email is None:
        raise CodexError(
            "the current Codex login is not a ChatGPT account (API key?) — "
            "only ChatGPT logins rotate"
        )
    write_private(stored_path(backup_root, email), live)
    return email, plan_of(live)


def login(backup_root: Path, extra_args: list[str] | None = None) -> tuple[str, str | None]:
    """Log a NEW account in and store it, without touching the live login.

    `codex login` over an existing login revokes that login's tokens
    (measured 2026-10-02: logging greg@tgmpanel.com in on ryzen turned the
    stored greg.laski@yahoo.com copy into a 401 ``token_revoked``, and that
    login had revoked gregpaypal's before it). So the login runs in a
    throwaway CODEX_HOME: the live file, and every stored copy, stay valid.
    """
    if shutil.which("codex") is None:
        raise CodexError("codex is not on PATH")
    tmp = Path(tempfile.mkdtemp(prefix="cswap-codex-login-"))
    try:
        os.chmod(tmp, 0o700)
        env = {**os.environ, "CODEX_HOME": str(tmp)}
        args = extra_args if extra_args else ["--device-auth"]
        rc = subprocess.call(["codex", "login", *args], env=env)
        auth = read_auth(tmp / "auth.json")
        if rc != 0 or auth is None:
            raise CodexError(f"codex login did not finish (exit {rc}) — nothing stored")
        email = email_of(auth)
        if email is None:
            raise CodexError("that login is not a ChatGPT account — only ChatGPT logins rotate")
        write_private(stored_path(backup_root, email), auth)
        if read_auth(live_auth_path()) is None:
            write_private(live_auth_path(), auth)
        return email, plan_of(auth)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@dataclass(frozen=True)
class Usage:
    weekly_pct: float | None
    weekly_reset_at: float | None
    short_pct: float | None
    short_reset_at: float | None
    limit_reached: bool
    error: str | None = None

    def over(self, threshold: float, weekly_threshold: float) -> bool:
        if self.limit_reached:
            return True
        if self.weekly_pct is not None and self.weekly_pct >= weekly_threshold:
            return True
        return self.short_pct is not None and self.short_pct >= threshold

    def weekly_life(self) -> float | None:
        """Percent of the week left (100 - weekly use); None when unknown."""
        if self.error is not None or self.weekly_pct is None:
            return None
        return max(0.0, 100.0 - self.weekly_pct)

    def life(self, threshold: float, weekly_threshold: float) -> float | None:
        """Points left before the nearest switch point (binding window)."""
        if self.error is not None:
            return None
        if self.limit_reached:
            return 0.0
        lives = []
        if self.weekly_pct is not None:
            lives.append(weekly_threshold - self.weekly_pct)
        if self.short_pct is not None:
            lives.append(threshold - self.short_pct)
        return max(0.0, min(lives)) if lives else None


def parse_usage(payload: dict) -> Usage:
    rate = payload.get("rate_limit") or {}
    weekly = short = None
    for key in ("primary_window", "secondary_window"):
        window = rate.get(key)
        if not isinstance(window, dict):
            continue
        length = window.get("limit_window_seconds") or 0
        if length and length <= SHORT_WINDOW_MAX_S:
            short = window
        else:
            weekly = window

    def pct(w: dict | None) -> float | None:
        v = (w or {}).get("used_percent")
        return float(v) if isinstance(v, (int, float)) else None

    def reset(w: dict | None) -> float | None:
        v = (w or {}).get("reset_at")
        return float(v) if isinstance(v, (int, float)) else None

    return Usage(
        weekly_pct=pct(weekly),
        weekly_reset_at=reset(weekly),
        short_pct=pct(short),
        short_reset_at=reset(short),
        limit_reached=bool(rate.get("limit_reached")),
    )


def fetch_usage(auth: dict, timeout: float = 20.0) -> Usage:
    """Read an account's rate limits. Never raises: failures come back as
    ``Usage(error=…)`` so one dead login cannot stop a listing or a tick."""
    tokens = auth.get("tokens") or {}
    request = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {tokens.get('access_token', '')}",
            "ChatGPT-Account-Id": tokens.get("account_id", ""),
            "User-Agent": "codex_cli_rs",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return parse_usage(json.load(response))
    except urllib.error.HTTPError as e:
        try:
            body = e.read(4096).decode("utf-8", "replace")
        except Exception:
            body = ""
        if e.code == 401 and "token_revoked" in body:
            # Dead for good — a later `codex login` on this machine revoked it.
            kind = "login revoked — log in again: cswap codex login"
        elif e.code == 401:
            kind = "token expired — refreshes on the next switch to it"
        else:
            kind = f"http {e.code}"
        return Usage(None, None, None, None, False, error=kind)
    except Exception as e:  # network, JSON — reported, never fatal
        return Usage(None, None, None, None, False, error=type(e).__name__)


def rank(
    usages: dict[str, Usage],
    current: str | None,
    threshold: float,
    weekly_threshold: float,
    reserve: str | None = None,
    reserve_min_life: float = 0.0,
) -> list[str]:
    """Candidates in the order a switch would try them: accounts with real
    room by soonest weekly reset (the quota that expires first), then ones
    with little room, then unreadable ones; exhausted accounts never.

    The reserve (owner, 2026-10-02: greg.laski@yahoo.com) always comes LAST,
    and drops out once less than ``reserve_min_life`` pct of its week is left.
    """
    reserve = reserve.lower() if reserve else None
    keyed = []
    for email, usage in usages.items():
        if email == current:
            continue
        life = usage.life(threshold, weekly_threshold)
        if life is not None and life <= 0:
            continue
        if email == reserve and reserve_spent(usage, reserve_min_life):
            continue
        keyed.append((
            (
                email == reserve,
                life is None,
                life is not None and life < MIN_USEFUL_LIFE_PCT,
                usage.weekly_reset_at if usage.weekly_reset_at is not None else float("inf"),
                -(life or 0.0),
            ),
            email,
        ))
    keyed.sort()
    return [email for _, email in keyed]


def reserve_spent(usage: Usage, reserve_min_life: float) -> bool:
    week = usage.weekly_life()
    return week is not None and week < reserve_min_life


def switch(backup_root: Path, email: str) -> str | None:
    """Make ``email`` the live Codex login; return the account it replaced."""
    email = email.lower()
    target = read_auth(stored_path(backup_root, email))
    if target is None:
        raise CodexError(f"{email} is not stored — log in with it and run: cswap codex add")
    previous = sync_live(backup_root)
    if previous == email:
        return previous
    write_private(live_auth_path(), target)
    return previous


def usages_for(backup_root: Path, emails: list[str], current: str | None) -> dict[str, Usage]:
    """Usage per stored account; the live one is read with the live tokens."""
    out: dict[str, Usage] = {}
    live = read_auth(live_auth_path())
    for email in emails:
        auth = live if email == current and live is not None else read_auth(stored_path(backup_root, email))
        out[email] = fetch_usage(auth) if auth else Usage(None, None, None, None, False, error="missing")
    return out


def auto_tick(
    backup_root: Path,
    threshold: float,
    weekly_threshold: float,
    *,
    reserve: str | None = None,
    reserve_min_life: float = 0.0,
    after_switch: str | None = None,
    dry_run: bool = False,
    now: float | None = None,
) -> dict:
    """One decision: stay, or switch to the best-ranked stored account."""
    now = time.time() if now is None else now
    current = sync_live(backup_root)
    emails = stored_accounts(backup_root)
    if current is None or current not in emails:
        return {"event": "codex-no-switch", "reason": "live-login-not-stored", "live": current}
    live_usage = usages_for(backup_root, [current], current)[current]
    if live_usage.error is not None:
        return {"event": "codex-no-switch", "reason": "active-usage-unknown", "detail": live_usage.error}
    reserve = reserve.lower() if reserve else None
    # The reserve is left as soon as its week drops under the floor, not only
    # at the switch threshold — and an ordinary account always beats it.
    on_spent_reserve = current == reserve and reserve_spent(live_usage, reserve_min_life)
    if not live_usage.over(threshold, weekly_threshold) and not on_spent_reserve:
        return {
            "event": "codex-no-switch",
            "reason": "below-threshold",
            "active": current,
            "weeklyPct": live_usage.weekly_pct,
        }
    others = [e for e in emails if e != current]
    ordered = rank(
        usages_for(backup_root, others, current), current, threshold, weekly_threshold,
        reserve, reserve_min_life,
    )
    if not ordered:
        return {"event": "codex-no-switch", "reason": "no-candidate", "active": current}
    target = ordered[0]
    event = {
        "event": "codex-switch",
        "from": current,
        "to": target,
        "weeklyPct": live_usage.weekly_pct,
        "dryRun": dry_run,
    }
    if not dry_run:
        switch(backup_root, target)
        if after_switch:
            event["afterSwitch"] = run_after_switch(after_switch, current, target)
    return event


def run_after_switch(command: str, previous: str | None, target: str) -> str:
    """Start the host's after-switch hook, detached; never raises.

    A running Codex process — and the shared app-server daemon every TUI
    talks to — reads auth.json once, at start. Measured 2026-10-04 on
    ryzen-old-1: two days after the switch, every thread still ran on the
    old account at 100% of its week, paying ~1,470 credits an hour, while
    the new live login sat at 2%. So the switch alone moves only NEW
    processes; the hook (e.g. a daemon restart that relaunches the TUIs) is
    what moves the running ones. Detached so the loop never waits on it.
    """
    env = {**os.environ, "CSWAP_CODEX_FROM": previous or "", "CSWAP_CODEX_TO": target}
    try:
        subprocess.Popen(
            command, shell=True, env=env, start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return "started"
    except OSError as e:
        return f"failed: {type(e).__name__}: {e}"
