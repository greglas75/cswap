# Scoped test quality audit

Date: 2026-09-17 (Europe/Paris)
Checkout: `/Users/greglas/DEV/claude-account-switcher`
Branch: `codex/prefer-primary-account`
Base: `c14ab714adf23a7e273a1bdb2a571d65c09fcfb5`
Mode: nested `test-audit --deep --read-only --commit=off`.
Independence: **degraded:same-model**. The requested gpt-5.4 role failed to dispatch according to the parent. This reviewer shares the parent's model and previously reviewed the design. This report is not an independent-provider certification.

## Result

| File | Passed | N/A | Out of scope | Applicable | AP deduction | Adjusted | Tier |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `tests/test_preferred_home.py` | 19 | 3 | 0 | 22 | 0 | 19/22 = 86.36% | A |
| `tests/test_preferred_reset.py` | 19 | 3 | 0 | 22 | 0 | 19/22 = 86.36% | A |

Critical gates Q7/Q11/Q13/Q15/Q17 are satisfied for the **changed behavior scope** in both files, by the branch mapping below. The initial reset-file AP2/AP15 findings were repaired by the parent and the changed tests were re-read. Neither remains in this snapshot.

`[GATE: test-quality] WARN tiers=A,A below-A=none independence=degraded:same-model final-verification=verified`

Runtime verification is complete, reusing the parent’s farm receipts:

- Full latest production snapshot: `rt` run `1789597951-96904-15433`, running `verification/check.sh` (Ruff, compileall, baseline-comparison mypy, then `python -m pytest -q`). Ruff and compileall passed; mypy reported upstream 122, current 122, introduced 0; pytest reported **2258 passed, 3 skipped in 89.93s**. Existing mypy diagnostics were not represented as a clean upstream baseline.
- After the final test-only split/public-collector cleanup: `rt` targeted run `1789598117-45014-27840`, scoped to `tests/test_preferred_home.py tests/test_preferred_reset.py`, reported **46 passed in 3.33s**.
- Parent confirmed the exact final source/test hashes in `zuvo/proofs/verified-snapshot.json`; they match this report. The full run covers the latest production, and the targeted rerun covers the later test-only cleanup. No redundant full rerun is inferred.

These are parent-supplied completed receipts, not commands executed by this reviewer. No test, lint, build, package installation, credential operation, or additional suite was run here. There is no matching completed mutation artifact, randomized-order result, or server-enforced patch coverage evidence in this review. Independence remains `degraded:same-model`; completion of execution does not upgrade that claim.

## Evidence identity and scope

The seven hashes below were measured directly after the parent’s final test cleanup and match the final parent artifact `zuvo/proofs/verified-snapshot.json`. Any subsequent source/test change requires relevant reassessment.

| Input | SHA-256 |
| --- | --- |
| `tests/test_preferred_home.py` | `980e3e46f17e13611ac49619cc3c039a51b09402faec05d8dc39acf81dc88073` |
| `tests/test_preferred_reset.py` | `d4678184d26b6cc1e639538922ce0cedb8da05f58b438c8333e1df83ab82dc97` |
| `src/claude_swap/settings.py` | `12461200972eb967696e75bdd8fa4b751a6a2c3aa515e7d33b4816bf8550d3a5` |
| `src/claude_swap/autoswitch.py` | `da6e31cf432d5c9b7e536e269385294ec74dfc8e416f700e4db0fb897106b183` |
| `src/claude_swap/usage_store.py` | `e08b6f569305e17905c2a26d0958caa63cb23f1bdabd727c89fde5dffe56eb8b` |
| `src/claude_swap/claude_locks.py` | `7d2174f276c468b04aed92b0c710e4641ea7e47319cd402a20ceae16a16054a5` |
| `src/claude_swap/switcher.py` | `ee04a0ebf0b9bc3fcb407a399c4a38c2a71e5c6088732a9492a40ebe15c35020` |

Production pairing: preferred routing in `AutoSwitchEngine._tick_inner`, `_rank_candidates`, `_collect_scheduled_usage`, `_gated_triggers`, `_return_home`; `AutoSwitchSettings.home_mode` and its SettingSpec; `UsageStore.request_reset_poll`; `ClaudeAccountSwitcher.request_usage_after_reset`, storage-lock additions to switching/restoration, and latest-shared-credential merge during active refresh; `claude_storage_lock`.

Complexity: COMPLEX integration behavior distributed over existing modules. This audit does **not** certify every legacy method or every line of those modules. The new public methods `request_reset_poll`, `request_usage_after_reset` and `claude_storage_lock` have direct or real-caller tests. The real collector test executes `request_usage_after_reset` through `engine.tick`; it is not inferred from a helper-only test.

Supplementary suite-aware evidence was read from `tests/test_home_account.py`, `tests/test_home_model_window.py`, `tests/test_claude_locks.py`, `tests/test_switcher.py`, and shared fixtures in `tests/test_autoswitch.py` / `tests/conftest.py`. These are branch references, not separately scored legacy files.

The parent's checkout/index decision was reused: the unrelated `local/tgm-survey-platform` CodeSift index cannot certify this checkout. Native `rg`, Python AST, and source reads supplied fallback dead-data, duplicate-body/name, reference, anti-pattern, and secret-pattern checks. AST parsing succeeded; every test function contains assertions or an asserted exception; no duplicate test names/bodies, unused imports, focus/skip markers, explicit sleep/network calls, or realistic credential patterns were found in either new file. Only clearly synthetic credentials occur. No downloaded code was executed.

## Branch and negative-behavior evidence

### Preferred routing

- `tests/test_preferred_home.py:33`: second account beats a third account with more quota, proving configured fallback order rather than incidental maximum headroom.
- `tests/test_preferred_home.py:55`: third account is selected when both earlier accounts are exhausted.
- `tests/test_preferred_home.py:63`, `:71`, `:83`: below/equal/above release threshold, healthy retention, and rejected exhausted return.
- `tests/test_preferred_home.py:40`, `:48`: return hysteresis rejects a marginal recovery and permits the exact configured margin.
- `tests/test_preferred_home.py:90`, `:104`: a single real engine instance leaves and returns despite cooldown/traffic, while the disabled under-load setting retains the fallback.
- `tests/test_preferred_home.py:114`, `:122`, `:129`, `:145`, `:154`: unknown/sentinel usage, weekly limit, missing weekly window, disabled home, and model-specific exhaustion.
- `tests/test_preferred_home.py:137`, `:165`, `:168`, `:172`: legacy pin hold, default, persisted opt-in configuration, and invalid-mode exception type/message.
- Unchanged home guards: `tests/test_home_account.py:151`, `:251`, `:328`, `:622`, `:639`, `:715`, `:750`, `:773`, `:797` cover dead home, bounded unknown hold, auth failure, unknown home, quarantine, live profile ownership, transient freshening, identity conflict and invalid grant. `tests/test_home_model_window.py:92` and `:212` characterize the retained pin-mode model/account-wall behavior.

### Reset scheduling, locking and refresh

- `tests/test_preferred_reset.py:33`, `:43`, `:51`: absent poll plan, never-fetched account, malformed reset. None of these invents quota.
- `tests/test_preferred_reset.py:59`: real collector runs through the public engine before and after reset; no pre-reset request, exact selected account request after reset, and actual return to primary.
- `tests/test_preferred_reset.py:90`: reset-minus-one / reset / reset-plus-one cases, retaining the old quota measurement while only changing eligibility.
- `tests/test_preferred_reset.py:100`, `:109`, `:122`: provider backoff, retry after a failed first observation, and an existing collector claim remain effective.
- `tests/test_preferred_reset.py:130`, `:144`, `:152`: repeated same-window timestamps do not spin; missing/empty/non-exhausted data stays unchanged; a later blocking weekly window prevents premature nomination.
- `tests/test_preferred_reset.py:189`, `:208`: public-collector tests simulate MCP change during the refresh request; success combines the latest MCP credentials with the new Claude token; read failure preserves the current live store, returns the expired-token sentinel, avoids a usage request, and retains the successor refresh token in backup.
- `tests/test_preferred_reset.py:232`, `:238`, `:246`, `:269`: native lock path and release, contended lock refusal, actual switch under lock with retained MCP state, and unchanged login when lock acquisition fails.
- Existing compensation paths: `tests/test_switcher.py:3289`, `:7584` (direct activation/identityless-config rollback) and `tests/test_claude_locks.py:28`, `:58`, `:146` (normal release/stolen-lock handling/partial acquisition cleanup) supplement unchanged infrastructure behavior. They do not prove a native macOS process interleaving.

## Per-file Q1–Q25

Each score is for the stated changed-symbol scope, not full-module coverage.

| Gate | Home file | Reset file |
| --- | --- | --- |
| Q1 | 1 — behavior names, e.g. `:33`, `:90` | 1 — explicit reset, backoff, persistence outcomes, e.g. `:59`, `:109`, `:189`, `:208` |
| Q2 | 1 — routing and configuration classes, `:32`, `:164` | 1 — reset and secure-store classes, `:32`, `:188` |
| Q3 | 0 — inherited `EngineHarness.tick_with_entries` mocks the collector without argument assertions (`tests/test_autoswitch.py:161`) | 0 — boundary fetch has exact positive/negative calls, but write spy at `:246` checks count/state rather than the full positive/negative argument contract for every mock |
| Q4 | 1 — exact account IDs, outcomes, events, exceptions | 1 — exact timestamps, claims, sentinel, stored credential fields |
| Q5 | 1 — Python mocks introduce no unsafe typing escape; real `UsageEntry` fixtures | 1 — real `UsageOutcome` / `RefreshOutcome` value objects and no unsafe casts |
| Q6 | 1 — fresh harness and context-managed patches | 1 — per-test store, monkeypatch fixtures and context-managed patches |
| Q7 | 1 — enumerated rejected return/configuration paths above | 1 — enumerated unknown/backoff/claim/read-error/lock-error paths above |
| Q8 | 1 — missing quota, unavailable sentinel, absent weekly window | 1 — no record/plan, None/empty data, invalid/missing date, exact reset boundary |
| Q9 | 1 — `preferred` / existing harness factories, `:14` | 1 — `exhausted_store`, `:23`, plus shared preferred harness |
| Q10 | 1 — named `used`, `live`, threshold and quota fixtures state the scenario | 1 — `expired`, `successor`, reset-relative FakeClock values identify the scenario |
| Q11 | 1 — new policy branches mapped above; unchanged branches explicitly supplemented by siblings | 1 — new nomination, selection, lock and refresh branches mapped above; runtime coverage percentage not claimed |
| Q12 | 1 — retain/release, busy permitted/blocked, ready/exhausted, pin/prefer | 1 — before/at/after reset, empty/valid data, success/failure, unlocked/contended |
| Q13 | 1 — actual engine via imported existing harness; no local reimplementation | 1 — actual engine/store/lock/switcher methods, imports `:11–18` |
| Q14 | 1 — persisted active account and event behavior | 1 — persisted credential composition, unchanged failed writes, real selected account |
| Q15 | 1 — exact selected account and event target | 1 — exact MCP token, active token and successor backup fields |
| Q16 | 1 — default pin and disabled-home behavior preserved, `:137`, `:145` | 1 — unrelated account untouched and MCP state retained, `:90`, `:189`, `:208`, `:246` |
| Q17 | 1 — selection and refusal derived from distinct quota inputs | 1 — latest MCP + refreshed Claude fields form a computed merge; old echo-only usage assertion was removed |
| Q18 | 1 — injected FakeClock; no network/timing assertions in scoped file | 1 — FakeClock controls reset logic; lock tests use immediate refusal and no timing sleeps |
| Q19 | 1 — unique temporary home and isolated keychain fixtures (`tests/conftest.py:37`, `:90`) | 1 — same isolation plus per-test cache and patch lifetime |
| Q20 | 1 — medium tests declared at `:1`; filesystem used consistently by harness | 1 — medium tests declared at `:1`; no real services invoked |
| Q21 | N/A — no completed attributable mutation artifact | N/A — no completed attributable mutation artifact |
| Q22 | N/A — scope is medium routing/configuration persistence, not a pure-validator unit | N/A — scope is medium store/credential coordination, not a pure-validator unit |
| Q23 | N/A — no cross-service schema contract claimed or changed | N/A — OAuth boundary is substituted to test local policy; this does not claim provider-schema validation |
| Q24 | 0 — randomized-order result and seed unavailable | 0 — randomized-order result and seed unavailable |
| Q25 | 0 — no evidence of server-enforced patch coverage | 0 — no evidence of server-enforced patch coverage |

No automatic Tier-D flags. No phantom mocked module found. Routing-only collector substitution is deliberate dependency isolation and is complemented by the real-collector test; it does not replace the production subject. Q3 remains honestly failed rather than counting that substitution as full mock-contract evidence.

## Reassessment and remaining limitations

The initial AP2 finding at the former conditional refresh test and AP15 finding at its private call were resolved. The replacement success/failure tests at `tests/test_preferred_reset.py:189` and `:208` drive the public collector, assert exact boundary calls, and retain the latest-MCP/active-token/successor-backup oracles. Assertions no longer branch on a parameter. The shared setup is a per-test fixture at `:163`.

No remaining AP deduction in the scoped files. Top residual gaps for both files are complete mock interaction evidence (Q3), randomized-order execution (Q24), and patch-coverage enforcement (Q25). No mutation score is available (Q21 exception). These are reported rather than fabricated; the per-file scores remain tier A because every scoped critical gate has explicit behavioral evidence.

Operational limits remain outside this test proof: native Keychain hot reload timing, credential-triggered quota wakeup, and a real user session crossing actual provider exhaustion/reset were not exercised. Synthetic quota changes establish routing decisions, not end-to-end autonomy at a provider wall.

Metrics: TIER_DIST and ECHO_COUNT use `/Users/greglas/.codex/shared/includes/test-metrics.md`. D: TIER_DIST A=100%, B=0%, C=0%, D=0% for this explicit two-file scope. D: ECHO_COUNT=0 in the final inspected snapshot. No mutation kill rate, elapsed test runtime, or full-suite test count is inferred.

Nested-stage completion: only this report was written; no duplicate parent retrospective/runlog, no production/test edits, and no additional agent dispatch.
