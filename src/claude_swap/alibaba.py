"""Alibaba Cloud Model Studio Token Plan rotation for Qwen Code (``cswap alibaba …``).

Two pieces make one account:
- the Token Plan API key Qwen Code sends — ``env.BAILIAN_TOKEN_PLAN_API_KEY`` in
  ``~/.qwen/settings.json`` (the rest of that file is the user's settings and stays);
- the Bailian CLI login (``~/.bailian/config.json``: console token, workspace),
  which is how the plan's usage is read: ``bl usage token-plan`` gives the month's
  used fraction and the reset time. Its own api_key is a different key and does
  not work on the Token Plan endpoint (measured 2026-10-18).

cswap keeps both per workspace under ``<backup root>/alibaba/<workspace>.json``.
An idle account is measured by running ``bl`` with a temporary HOME holding its
stored login. Past the threshold the next account is the one with room whose
month resets soonest.
"""

from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from claude_swap import filestore
from claude_swap.real_store_guard import refuse_test_identity

ENV_KEY = "BAILIAN_TOKEN_PLAN_API_KEY"
TOKEN_PLAN_URL = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
MIN_USEFUL_LIFE_PCT = 5.0


class AlibabaError(Exception):
    """A user-facing failure of a ``cswap alibaba`` command."""


def _home() -> Path:
    return Path(os.environ.get("CSWAP_ALIBABA_HOME") or "~").expanduser()


def qwen_settings() -> Path:
    return _home() / ".qwen" / "settings.json"


def bailian_config(home: Path | None = None) -> Path:
    return (home or _home()) / ".bailian" / "config.json"


def store_dir(backup_root: Path) -> Path:
    return Path(backup_root) / "alibaba"


def workspace_of(account: dict | None) -> str | None:
    if not isinstance(account, dict) or not account.get("qwenKey"):
        return None
    ws = (account.get("bailian") or {}).get("workspace_id")
    return ws if isinstance(ws, str) and ws else None


def read_live() -> dict | None:
    key = ((filestore.read_json(qwen_settings()) or {}).get("env") or {}).get(ENV_KEY)
    bailian = filestore.read_json(bailian_config())
    if not key or bailian is None:
        return None
    return {"qwenKey": key, "bailian": bailian}


def _write_pieces(account: dict) -> None:
    settings = filestore.read_settings(qwen_settings(), AlibabaError)
    settings.setdefault("env", {})[ENV_KEY] = account["qwenKey"]
    filestore.write_private(bailian_config(), account["bailian"])
    filestore.write_private(qwen_settings(), settings)


def write_live(account: dict, previous: dict | None = None) -> None:
    """Both pieces of one account; a failure part-way puts ``previous`` back."""
    ws = workspace_of(account)
    if ws is None:
        raise AlibabaError("refusing to write an account without a workspace and a Token Plan key")
    refuse_test_identity(ws, qwen_settings())
    filestore.read_settings(qwen_settings(), AlibabaError)   # fail before touching anything
    try:
        _write_pieces(account)
    except BaseException as e:
        if previous is not None:
            try:
                _write_pieces(previous)
            except Exception as rollback:  # noqa: BLE001
                raise AlibabaError(f"writing {ws} failed ({e}) and restoring the previous account "
                                   f"failed too ({rollback}) — check ~/.qwen/settings.json") from e
        raise


def stored_path(backup_root: Path, ws: str) -> Path:
    return store_dir(backup_root) / f"{filestore.safe_name(ws, AlibabaError)}.json"


def stored_accounts(backup_root: Path) -> list[str]:
    root = store_dir(backup_root)
    if not root.is_dir():
        return []
    return sorted(p.name[: -len(".json")] for p in root.iterdir()
                  if p.name.endswith(".json") and not p.name.startswith(".")
                  and workspace_of(filestore.read_json(p)) == p.name[: -len(".json")])


def _sync_live(backup_root: Path) -> str | None:
    live = read_live()
    ws = workspace_of(live)
    if ws is not None and stored_path(backup_root, ws).exists():
        filestore.write_private(stored_path(backup_root, ws), live)
    return ws


def sync_live(backup_root: Path) -> str | None:
    with filestore.lock(store_dir(backup_root)):
        return _sync_live(backup_root)


def add(backup_root: Path) -> str:
    live = read_live()
    ws = workspace_of(live)
    if ws is None:
        raise AlibabaError(f"no live account: Qwen Code needs {ENV_KEY} in ~/.qwen/settings.json "
                           "and the Bailian CLI a login (bl auth login)")
    with filestore.lock(store_dir(backup_root)):
        filestore.write_private(stored_path(backup_root, ws), live)
    return ws


def _bl() -> str:
    found = shutil.which("bl") or "/opt/homebrew/bin/bl"
    if not Path(found).exists():
        raise AlibabaError("the Bailian CLI is not installed (npm i -g bailian-cli)")
    return found


def _bl_env(home: Path | None) -> dict:
    env = dict(os.environ)
    # launchd's PATH has no node; bl's shebang needs it next to bl.
    env["PATH"] = os.pathsep.join([str(Path(_bl()).parent), "/opt/homebrew/bin", env.get("PATH", "")])
    if home is not None:
        env["HOME"] = str(home)
    return env


def key_works(key: str, timeout: float = 20.0) -> bool:
    req = urllib.request.Request(TOKEN_PLAN_URL + "/models", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def login(backup_root: Path) -> str:
    """Store the current account, run `bl auth login` for another one (console
    login: usage), then ask for that account's Token Plan key (Qwen Code).
    Without a complete new account the previous one is put back."""
    bl = _bl()
    with filestore.lock(store_dir(backup_root)):
        previous = read_live()
        raw_bailian = filestore.read_json(bailian_config())   # even one no account owns
        if workspace_of(previous) is not None:
            filestore.write_private(stored_path(backup_root, workspace_of(previous)), previous)
    fresh = None
    try:
        with filestore.lock(store_dir(backup_root)):
            filestore.write_private(bailian_config(), {})
        subprocess.call([bl, "auth", "login"], env=_bl_env(None))
        bailian = filestore.read_json(bailian_config()) or {}
        if bailian.get("workspace_id"):
            key = getpass.getpass("Token Plan API key of this account (for Qwen Code, hidden): ").strip()
            if key and key_works(key):
                fresh = {"qwenKey": key, "bailian": bailian}
            elif key:
                print("That key does not work on the Token Plan endpoint.")
    finally:
        with filestore.lock(store_dir(backup_root)):
            if workspace_of(fresh) is not None:
                write_live(fresh)
            elif previous is not None:
                _write_pieces(previous)
            elif raw_bailian is not None:
                filestore.write_private(bailian_config(), raw_bailian)
    ws = workspace_of(fresh)
    if ws is None:
        raise AlibabaError("no new account — the previous one is back in place" if previous else "no new account")
    with filestore.lock(store_dir(backup_root)):
        filestore.write_private(stored_path(backup_root, ws), fresh)
    return ws


def remove(backup_root: Path, ws: str) -> None:
    with filestore.lock(store_dir(backup_root)):
        path = stored_path(backup_root, ws)
        if not path.exists():
            raise AlibabaError(f"{ws} is not stored")
        if workspace_of(read_live()) == ws:
            raise AlibabaError(f"{ws} is the live account — switch to another one first")
        path.unlink()


def _switch(backup_root: Path, ws: str) -> str | None:
    target = filestore.read_json(stored_path(backup_root, ws))
    if workspace_of(target) != ws:
        raise AlibabaError(f"{ws} is not stored — log in with it and run: cswap alibaba add")
    live = read_live()
    live_ws = workspace_of(live)
    if live is not None and (live_ws is None or not stored_path(backup_root, live_ws).exists()):
        raise AlibabaError(f"the live account {live_ws or '(no workspace)'} is not stored — "
                           "run `cswap alibaba add` first")
    previous = _sync_live(backup_root)
    if previous != ws:
        write_live(target, previous=live)
    return previous


def switch(backup_root: Path, ws: str) -> str | None:
    with filestore.lock(store_dir(backup_root)):
        return _switch(backup_root, ws)


@dataclass(frozen=True)
class Usage:
    pct: float | None
    reset_at: float | None
    error: str | None = None

    def life(self, threshold: float) -> float | None:
        if self.error is not None or self.pct is None:
            return None
        return max(0.0, threshold - self.pct)


def parse_usage(payload: dict) -> Usage:
    frac = payload.get("per1MonthPercentage")
    reset = payload.get("per1MonthResetTime")
    try:
        reset_at = float(reset) / 1000.0 if reset is not None else None
    except (TypeError, ValueError):
        reset_at = None
    if not isinstance(frac, (int, float)) or isinstance(frac, bool) or not 0 <= frac <= 10:
        return Usage(None, reset_at, error="no monthly usage in the reply")
    return Usage(float(frac) * 100.0, reset_at)


def fetch_usage(bailian: dict | None, *, live: bool, timeout: float = 60.0) -> tuple[Usage, dict | None]:
    """The month's usage via `bl usage token-plan`; for an idle account under a
    temporary HOME. Returns the login bl left behind (it may refresh it)."""
    if not bailian:
        return Usage(None, None, error="missing"), None
    with tempfile.TemporaryDirectory(prefix="cswap-bl-") as tmp:
        home = None if live else Path(tmp)
        if home is not None:
            filestore.write_private(bailian_config(home), bailian)
        try:
            out = subprocess.run([_bl(), "usage", "token-plan", "--output", "json"], env=_bl_env(home),
                                 capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError, AlibabaError) as e:
            return Usage(None, None, error=type(e).__name__), None
        after = filestore.read_json(bailian_config(home))
    if out.returncode != 0:
        return Usage(None, None, error=f"bl rc={out.returncode}: {(out.stderr or out.stdout).strip()[:160]}"), after
    try:
        payload = json.loads(out.stdout[out.stdout.index("{"):])
    except ValueError:
        return Usage(None, None, error="bl printed no JSON"), after
    return parse_usage(payload if isinstance(payload, dict) else {}), after


def usages_for(backup_root: Path, workspaces: list[str], current: str | None) -> dict[str, Usage]:
    out = {}
    for ws in workspaces:
        account = filestore.read_json(stored_path(backup_root, ws)) or {}
        usage, after = fetch_usage(account.get("bailian"), live=ws == current)
        if ws != current and after and after != account.get("bailian") and after.get("workspace_id") == ws:
            with filestore.lock(store_dir(backup_root)):
                filestore.write_private(stored_path(backup_root, ws), {**account, "bailian": after})
        out[ws] = usage
    return out


def rank(usages: dict[str, Usage], current: str | None, threshold: float) -> list[str]:
    """Accounts with room, soonest monthly reset first, then most room."""
    keyed = []
    for ws, usage in usages.items():
        life = usage.life(threshold)
        if ws == current or life is None or life <= 0:
            continue
        keyed.append(((life < MIN_USEFUL_LIFE_PCT,
                       usage.reset_at if usage.reset_at is not None else float("inf"), -life), ws))
    keyed.sort()
    return [ws for _, ws in keyed]


def auto_tick(backup_root: Path, threshold: float, *, dry_run: bool = False, usages_fn=usages_for) -> dict:
    with filestore.lock(store_dir(backup_root)):
        current = _sync_live(backup_root)
        workspaces = stored_accounts(backup_root)
    if current is None or current not in workspaces:
        return {"event": "alibaba-no-switch", "reason": "live-account-not-stored", "live": current}
    usages = usages_fn(backup_root, workspaces, current)   # bl runs outside the lock
    with filestore.lock(store_dir(backup_root)):
        if workspace_of(read_live()) != current:
            return {"event": "alibaba-no-switch", "reason": "live-account-changed", "account": current}
        live = usages[current]
        if live.error is not None:
            return {"event": "alibaba-no-switch", "reason": "active-usage-unknown", "detail": live.error}
        if live.pct < threshold:
            return {"event": "alibaba-no-switch", "reason": "below-threshold", "account": current,
                    "usedPct": live.pct}
        order = rank(usages, current, threshold)
        if not order:
            return {"event": "alibaba-no-switch", "reason": "no-account-with-room", "account": current,
                    "usedPct": live.pct}
        if not dry_run:
            _switch(backup_root, order[0])
        return {"event": "alibaba-switch", "from": current, "to": order[0], "usedPct": live.pct,
                "dryRun": dry_run}
