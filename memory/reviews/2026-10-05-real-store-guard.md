# Review — real-store guard (cswap)

Verified-against: fed5b70
Range: ff54aa2..fed5b70
Tier: 2 | SELF-REVIEW | Adversarial: 5 passes --multi (no truncation)
Verdict: APPROVE — findings fixed in-run; 2399 tests pass (farm)

## R1 — [RECOMMENDED] Blocking x.com in the product would lock out a real user
File: src/claude_swap/real_store_guard.py:53
Fix: decide on provenance — in a test context nothing is written under the real home; outside tests only reserved names (src/claude_swap/real_store_guard.py:31)

## R2 — [RECOMMENDED] Only sequence writes with accounts were guarded
File: src/claude_swap/switcher.py:433
Fix: every _write_json path checked in a test context

## R3 — [RECOMMENDED] State and settings writes bypassed the guard
File: src/claude_swap/settings.py:731
Fix: atomic_write_json guarded

## Accepted limits
- a standalone script that copies fixtures without importing tests: caught by EngineHarness's temp-dir check and reserved domains, not by provenance
- no passwd entry (Windows): guard is a no-op
