"""Small helpers shared by the per-tool rotations that keep logins in JSON files
(``cswap mimo``, ``cswap alibaba``): private atomic writes, a strict reader for
files that also hold the user's settings, and the store lock."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from pathlib import Path


def read_json(path: Path) -> dict | None:
    """A JSON object, or None when the file is missing or not one."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_settings(path: Path, error: type[Exception]) -> dict:
    """A file that holds the user's settings too: {} only when absent, ``error``
    when unreadable — so it is never replaced by a file with one key in it."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise error(f"{path} is unreadable ({e})")
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise error(f"{path} does not parse — fix or remove it, then retry")
    return data


def write_private(path: Path, data: dict) -> None:
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


@contextlib.contextmanager
def lock(directory: Path):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    with open(directory / ".lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def safe_name(name: str, error: type[Exception]) -> str:
    if not name or "/" in name or "\\" in name or ".." in name or name.startswith("."):
        raise error(f"not a usable account name: {name!r}")
    return name
