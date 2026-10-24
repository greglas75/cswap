<!-- zuvo-review -->
range: b02b8fd42f7080f711c811801c9c2efa27a3aff4..77c9727fbcd2c08e864a634f52f4476e3020d40b
files: src/claude_swap/kimi.py, src/claude_swap/cli.py, scripts/deploy-hosts.sh, tests/test_kimi.py
adversarial: zuvo/proofs/cswap-kimi-38a90335a5-adversarial.txt
Verified-against: 77c9727fbcd2c08e864a634f52f4476e3020d40b

# Review — cswap kimi (Kimi Code CLI login rotation), 2026-10-24

SELF-REVIEW. TIER 3 by size (839 lines, new production file). Mode FIX-AUTO.
Adversarial: 3 passes --multi (pass 1 over b02b8fd..5a84e73 in 3 chunks, 4 providers
each; pass 2 on the fix delta 5a84e73..d68bf0b; pass 3 on d68bf0b..77c9727).
Sub-agent auditors were NOT dispatched (lead-inline audit + cross-model passes) — degraded
against the TIER 3 mandate; stated here rather than claimed.

## Fixed (d68bf0b, 77c9727)
- R-1 [MUST-FIX, CROSS CRITICAL] src/claude_swap/kimi.py:257 — `_switch` stored the live
  token outside kimi's lock; a kimi refresh in between rotated a refresh token cswap never
  kept. Now sync + overwrite happen under one `kimi_lock`.
- R-2 [MUST-FIX] src/claude_swap/kimi.py:510 — `login()` stored the previous account before
  the minutes-long sign-in and overwrote the live token afterwards; now goes through `_switch`
  (re-stores the live token first). Test: test_login_keeps_the_token_kimi_rotated_during_the_sign_in.
- R-3 [MUST-FIX] src/claude_swap/kimi.py:410 — `_fresh_stored` refreshed an account that had
  meanwhile become kimi's live login (spends kimi's refresh token). Test:
  test_the_live_login_is_never_refreshed_from_its_stored_copy.
- R-4 [RECOMMENDED] src/claude_swap/kimi.py:345 — any reply without `usages` counted as no plan
  (100% used). Now only `{}` / `{"usages": {}}` (measured Free reply: `{}`).
- R-5 [RECOMMENDED] src/claude_swap/kimi.py:442 — email label write crashed on a corrupt stored file.
- R-6 [RECOMMENDED] src/claude_swap/kimi.py:304 — malformed refresh reply escaped as non-KimiError.
- R-7 [RECOMMENDED] src/claude_swap/cli.py:1603 — `auto` read the threshold once; try covered only the tick.
- R-8 [MUST-FIX, found by pass 2] src/claude_swap/kimi.py:513 — signing in again as the live
  account threw the new token away. Test: test_signing_in_again_as_the_live_account_replaces_its_token.

## Rejected / deferred
- rows duplicate current (cli.py) — rejected: `rank()` skips `current` (src/claude_swap/kimi.py:448).
- stale-lock rmdir TOCTOU src/claude_swap/kimi.py:142 — deferred: same protocol as kimi's
  proper-lockfile; a race needs two waiters on a lock stale for >5 s.
- `{}` from a proxy counted as no plan (src/claude_swap/kimi.py:345) — deferred: costs one
  switch to an account with room; no 403 probe added.
- missing live file in login (pass 3) — rejected: `read_json` returns None (src/claude_swap/filestore.py:15).

## Verification
Pre-fix: the 4 new regression tests fail on the old code (rt, hz4/stras). Post-fix: full
suite 2612 passed, 3 skipped (rt --light, farm). comment-pass PASS.

Verdict: APPROVE (degraded: no sub-agent auditors).
