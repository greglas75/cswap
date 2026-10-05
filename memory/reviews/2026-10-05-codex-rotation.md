# Review — codex rotation, reserve floor, fleet scripts (cswap)

Verified-against: ff54aa2
Range: a21eed1..ff54aa2 (24 reviewed commits + 7 fix commits)
Tier: 3 (DEEP) | SELF-REVIEW | Auditors: behavior, structure, CQ (sonnet, dispatched, all returned) + confidence re-scorer
Adversarial: 4 passes + a clean final pass (no truncation, new proof) and its validation append, --multi (pass 1 in 3 chunks — chunk 1 re-covered in pass 2 after a truncation; pass 2 in 3 parts with autoswitch split by hunks; pass 3 on post-pass-2 fixes; validation pass on the last fixes)
Verdict: APPROVE — 1 MUST-FIX and ~30 RECOMMENDED fixed in-run; 2378 tests pass (farm)
Deployment risk: MEDIUM (5) — new prod files, multi-host scripts; deploy with scripts/deploy-hosts.sh

## MUST-FIX (fixed)

## R1 — [MUST-FIX] Codex rank switched onto unreadable/revoked accounts instead of credits
File: src/claude_swap/codex.py:414
Fix: unreadable accounts are never targets except an access token that really expired (codex.py:379, JWT exp); a revoked LIVE login is left at once (codex.py:516)

## RECOMMENDED (fixed)

## R2 — [RECOMMENDED] Session scan early exit could read as quiet while sessions write
File: src/claude_swap/autoswitch.py:4803 (verdict pinned to scan end; cache keyed on scan end)
## R3 — [RECOMMENDED] Ungated switch's label scan ran under the state lock before lastSwitchAt
File: src/claude_swap/autoswitch.py:3286 (label after state + marker; write failure logged)
## R4 — [RECOMMENDED] Blind home trusted arbitrarily old readings
File: src/claude_swap/autoswitch.py:1937 (TRUST_MAX_AGE_S cap)
## R5 — [RECOMMENDED] Pre-freshen rotated disabled slots' tokens
File: src/claude_swap/autoswitch.py:1472
## R6 — [RECOMMENDED] cswap list order disagreed with the engine without a prefer home
File: src/claude_swap/switcher.py:5414
## R7 — [RECOMMENDED] Credits pin survived a week reset
File: src/claude_swap/codex.py:520
## R8 — [RECOMMENDED] Double usage fetch per tick; no lock over sync/switch read-modify-write
File: src/claude_swap/codex.py:133
## R9 — [RECOMMENDED] Re-login of the live account reverted by sync; API-key login could be replaced
File: src/claude_swap/codex.py:229, src/claude_swap/codex.py:452
## R10 — [RECOMMENDED] Switch could destroy an unstored live login or accept another account's file
File: src/claude_swap/codex.py:448
## R11 — [RECOMMENDED] Account name could leave the store (path traversal)
File: src/claude_swap/codex.py:127
## R12 — [RECOMMENDED] Unlimited credits sorted last
File: src/claude_swap/codex.py:553
## R13 — [RECOMMENDED] Hook processes never reaped (zombies)
File: src/claude_swap/codex.py:619
## R14 — [RECOMMENDED] list --json called a held reserve / unreadable account 'exhausted'; weekly < 5h threshold handled differently from Claude
File: src/claude_swap/cli.py:1061, src/claude_swap/cli.py:993
## R15 — [RECOMMENDED] hub-status-push overwrote a good half with null; stale age reset each round
File: scripts/hub-status-push.py:131, scripts/hub-status-push.py:142
## R16 — [RECOMMENDED] cswap-ci shifted positional args per host; empty array under set -u (bash 3.2)
File: scripts/cswap-ci:33

## R17 — [RECOMMENDED] 'cswap codex list --json' printed text when no account was stored
File: src/claude_swap/cli.py:1046

## Dropped (verified false)
- weekly_life ignores unverified model windows; TypeError in latest_session_activity_ts; return-home dropped a test; pre_timing undefined; launchd overlap; tar -x without -f; undefined 'tokens'; revoked() case-sensitivity — each checked against the code, none reproduces.

## Backlog (structural / pre-existing)
- STRUCT-1 reserve/ranking policy in three copies → one leaf module (recipe in structure report)
- STRUCT-3/4 split cli._codex_command and codex._auto_tick
- STRUCT-9 one deploy door (deploy.sh vs deploy-hosts.sh)
- pre-existing: no-candidates long wait on a Keychain outage (autoswitch, from c14ab71); codex auto reads settings once
- tests: CLI layer beyond --json; Q24/Q25 (random order, coverage gate) repo-wide

## Mutation check
Not a full zuvo:mutation-test run. Two fixes (R-1 rank, R-2 scan pin) were reverted by hand and their new tests failed (2 failed), then restored.
