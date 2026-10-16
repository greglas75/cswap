<!-- zuvo-review -->
range: d721eed523bc3a20fef21c190d61b3aaa7e3bb32..7d58daffbe297a36f7b8898ed7317bd79db8e098
files: src/claude_swap/agy.py, src/claude_swap/cli.py, src/claude_swap/settings.py, tests/test_agy.py, scripts/deploy-hosts.sh
adversarial: zuvo/proofs/agy-b26b030-adversarial.txt
verdict: APPROVE after fixes
-->

# Review — cswap agy (Antigravity login rotation)

Fixed in 7d58daf, each with a regression test where it is behaviour:
- CRITICAL login interrupted in agy left the machine logged out → restore in `finally`.
- Locked keychain fell back to the stale file; switch could overwrite an unreadable live login →
  keychain errors are "unknown", `_switch`/`login` read strictly and refuse.
- Reading without a reset time held an account out forever → expires after 24 h; nanosecond and
  offset-less timestamps parse.
- Malformed quota payload crashed the tick → returns an error reading.
- `list` recorded without the store lock → locked.

Accepted: keychain-then-file write is not atomic across both (the keychain wins on read, so the
login stays consistent); the email is the unverified id_token claim, as in `cswap codex` (local file
of the owner's own login); the Mac deploy label is unconditional (the LaunchAgent is installed).

Tests: 2503 passed, 3 skipped (rt).
