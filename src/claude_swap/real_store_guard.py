"""Never let a test identity into the real account store.

2026-10-05 on the owner's Mac: a test helper ran against the REAL home and
seeded its fixture accounts (a@x.com, b@x.com) into ~/.claude-swap-backup —
slots 1 and 2 (tatiana, greg.laski) were overwritten in sequence.json, the
daemon stopped recognising the live login, and rotation went wrong for two
hours. pytest's own isolation (``_isolate_real_home``, ``temp_home``) works by
patching ``$HOME`` and ``Path.home()``; a script that hands a helper the real
home, or a test exempted from isolation, sails past it.

So the product refuses, not the tests: a write of a test-domain account into a
store under the user's REAL home — resolved from the password database, which
no ``$HOME`` patch or monkeypatch changes — raises instead of landing. Real
accounts are never on these domains (RFC 2606/6761 reserved names, plus x.com,
which the suite's fixtures use).
"""

from __future__ import annotations

import os
from pathlib import Path

TEST_DOMAINS = frozenset({"example.com", "example.org", "example.net", "x.com"})
TEST_SUFFIXES = (".test", ".invalid", ".example", ".localhost")


class RealStoreGuardError(RuntimeError):
    """A test identity was about to be written into the real account store."""


def is_test_email(email: str | None) -> bool:
    domain = (email or "").strip().lower().rpartition("@")[2]
    return bool(domain) and (domain in TEST_DOMAINS or domain.endswith(TEST_SUFFIXES))


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


def refuse_test_identity(email: str | None, path: Path | str) -> None:
    """Raise if a test-domain account is about to be written under the real home."""
    if is_test_email(email) and inside_real_store(path):
        raise RealStoreGuardError(
            f"refusing to write test account {email!r} into the real store ({path}): "
            "a test or script is pointed at your real home directory"
        )
