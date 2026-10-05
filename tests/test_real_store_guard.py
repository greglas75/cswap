"""Medium: a test identity can never be written into the real account store.

2026-10-05: a helper run against the owner's real home put fixture accounts
(a@x.com, b@x.com) into slots 1 and 2 of ~/.claude-swap-backup/sequence.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_swap import codex, real_store_guard
from claude_swap.real_store_guard import RealStoreGuardError, is_test_email, refuse_test_identity


@pytest.fixture
def fake_real_home(tmp_path, monkeypatch):
    home = tmp_path / "realhome"
    home.mkdir()
    monkeypatch.setattr(real_store_guard, "_real_home", lambda: home.resolve())
    return home


@pytest.mark.parametrize("email,expected", [
    ("a@x.com", True), ("b@example.com", True), ("c@example.org", True), ("d@host.test", True),
    ("e@mail.example.com", True), ("tatiana@tgmresearch.com", False), ("greg.laski@gmail.com", False),
    ("", False), (None, False), (123, False), ("someone@notx.com", False),
])
def test_test_domains(email, expected):
    assert is_test_email(email) is expected


def test_refused_under_the_real_home_allowed_elsewhere(fake_real_home, tmp_path):
    with pytest.raises(RealStoreGuardError):
        refuse_test_identity("a@x.com", fake_real_home / ".claude-swap-backup" / "sequence.json")
    with pytest.raises(RealStoreGuardError):
        refuse_test_identity("a@x.com", fake_real_home / ".local" / "share" / "claude-swap")
    refuse_test_identity("a@x.com", tmp_path / "scratch" / "sequence.json")          # scratch: fine
    refuse_test_identity("tatiana@tgmresearch.com", fake_real_home / ".claude-swap-backup")  # real: fine


def test_the_switcher_will_not_seed_a_fixture_into_the_real_store(fake_real_home, temp_home):
    from tests.test_autoswitch import EngineHarness
    h = EngineHarness(temp_home)
    h.switcher.backup_dir = fake_real_home / ".claude-swap-backup"
    h.switcher.sequence_file = h.switcher.backup_dir / "sequence.json"
    h.switcher.backup_dir.mkdir()
    with pytest.raises(RealStoreGuardError):
        h.switcher._write_json(h.switcher.sequence_file, {"accounts": {"1": {"email": "a@x.com"}}})
    assert not h.switcher.sequence_file.exists()


def test_codex_will_not_write_a_fixture_login_into_the_real_codex_home(fake_real_home):
    from tests.test_codex import _auth
    with pytest.raises(RealStoreGuardError):
        codex.write_private(fake_real_home / ".codex" / "auth.json", _auth("a@x.com"))
    assert not (fake_real_home / ".codex" / "auth.json").exists()


def test_the_engine_harness_refuses_a_non_temporary_home():
    from tests.test_autoswitch import EngineHarness
    with pytest.raises(RuntimeError, match="temporary home"):
        EngineHarness(Path("/Users/someone"))
