"""Never let a test identity into the real account store.

2026-10-05 on the owner's Mac: a test helper ran against the REAL home and
seeded its fixture accounts (a@x.com, b@x.com) into ~/.claude-swap-backup —
slots 1 and 2 (tatiana, greg.laski) were overwritten in sequence.json, the
daemon stopped recognising the live login, and rotation went wrong for two
hours. pytest's own isolation (``_isolate_real_home``, ``temp_home``) works by
patching ``$HOME`` and ``Path.home()``; a script that hands a helper the real
home, or a test exempted from isolation, sails past it.

So the product refuses, not the tests. Two rules, both against the user's REAL
home — resolved from the password database, which no ``$HOME`` patch or
monkeypatch changes:

- in a TEST context (pytest running, or any ``tests`` module imported — the
  2026-10-05 script imported a test helper) nothing may be written there at
  all, whatever the account;
- anywhere, an account on an RFC 2606/6761 reserved name (example.*, .test,
  .invalid, .localhost) is refused: no real login lives there.

The decision is on provenance first, not on the address: x.com, which the
fixtures use, is a real mail domain and is blocked only in a test context.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

RESERVED_DOMAINS = frozenset({"example.com", "example.org", "example.net", "localhost",
                              "test", "invalid", "example"})
TEST_SUFFIXES = (".test", ".invalid", ".example", ".localhost")


class RealStoreGuardError(RuntimeError):
    """A test identity was about to be written into the real account store."""


def is_test_email(email: object) -> bool:
    if not isinstance(email, str):
        return False
    domain = email.strip().lower().rpartition("@")[2].strip(" .>")
    if not domain:
        return False
    return (
        domain in RESERVED_DOMAINS
        or any(domain.endswith("." + d) for d in RESERVED_DOMAINS)   # mail.example.com
        or domain.endswith(TEST_SUFFIXES)
    )


def in_test_context() -> bool:
    """pytest is running, or test code is loaded into this process."""
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules or "_pytest" in sys.modules:
        return True
    return any(name == "tests" or name.startswith("tests.") or name == "conftest" for name in list(sys.modules))


def _real_home() -> Path | None:
    try:
        import pwd  # POSIX only; Windows keeps the old behaviour

        return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    except Exception:  # noqa: BLE001 — no passwd entry: nothing to compare against
        return None


def _real_roots() -> list[Path]:
    home = _real_home()
    if home is None:
        return []
    return [
        home / ".claude-swap-backup",               # macOS / legacy cswap store
        home / ".local" / "share" / "claude-swap",  # Linux cswap store
        home / ".codex",                            # the live Codex login
        home / ".claude",                           # the live Claude login
        home / ".claude.json",                      # the live Claude identity
    ]


def inside_real_store(path: Path | str) -> bool:
    try:
        p = Path(path).resolve()
    except OSError:
        return False
    for root in _real_roots():
        try:
            root = root.resolve()
        except OSError:
            continue
        if p == root or root in p.parents:
            return True
    return False


def refuse_test_identity(email: object, path: Path | str) -> None:
    """Raise if test code, or a reserved test identity, is about to write under the real home."""
    if not inside_real_store(path):
        return
    if in_test_context():
        raise RealStoreGuardError(
            f"refusing to write {email!r} into the real store ({path}) from test code: "
            "a test or a script using test helpers is pointed at your real home directory"
        )
    if is_test_email(email):
        raise RealStoreGuardError(
            f"refusing to write test account {email!r} into the real store ({path})"
        )
