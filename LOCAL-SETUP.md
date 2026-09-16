# Preferred Claude account

This checkout is a local adaptation of
`danpoyar/cswap` at `c14ab714adf23a7e273a1bdb2a571d65c09fcfb5`.

## Feature and scope

Add an opt-in `autoswitch.homeMode=prefer` policy. A preferred account is used
while it has quota, gives way when exhausted, and takes over again as soon as
fresh usage shows it has capacity. The existing `pin` policy remains the default.
Returning requires the existing headroom hysteresis margin, preventing flapping
on tiny near-limit recoveries. Eligible fallbacks follow account sequence.
With `switchUnderLoad=true`, returning to the preferred account uses the existing
credential handoff without waiting five minutes for transcript inactivity.
No CLI process or tool is terminated, and no model is changed.

Production scope: `src/claude_swap/settings.py`, `src/claude_swap/autoswitch.py`,
`src/claude_swap/usage_store.py`, `src/claude_swap/claude_locks.py`, and
`src/claude_swap/switcher.py`. Tests: `tests/test_preferred_home.py` and
`tests/test_preferred_reset.py`. Documentation: this file and README.md.
Risk tier: DEEP (account routing and concurrent running sessions). Existing large
modules are not being split or otherwise refactored. Their current tests remain.

## Reuse and blast radius

Native CLI 2.1.273 refreshes its Keychain cache and invalidates OAuth memoization
when its credential file changes. Reuse cswap's existing credential merge,
refresh locks, account identity checks, quota collection and switching engine.
Only the new optional routing policy changes. Existing pin behavior is covered
by test_home_account.py and test_home_model_window.py.

## Steps and acceptance proofs

1. Add validated `homeMode` choice (`pin`, `prefer`), default `pin`.
2. Let exhausted preferred home use ordinary failover; keep healthy home selected.
3. Return only when home has usable account/model quota; unknown usage is not ready.
4. Allow the requested return under traffic only when explicitly configured.
5. Check the reset-aware collector, run targeted and full tests through `rt`,
   review the changed behavior, commit locally, then install this exact checkout.
6. Enroll the three already-authenticated profiles using matching Keychain entries
   and identities without displaying tokens. Configure Tatiana as preferred.
7. Validate account status and an automatic dry run before starting local service.

Proof: targeted pytest suite verifies failover, exhausted-home rejection, return
after quota recovers despite cooldown and traffic, healthy-home retention,
unknown/disabled home, model-specific limit, and unchanged default pin behavior.
Full smoke: same engine instance traverses primary -> fallback -> primary on
controlled quota observations, while account identity and switch events agree.
Live quota-exhaustion cycling is not induced: real account checks and the dry run
are recorded separately from deterministic simulated quota-reset tests.

## Code contract

settings.py: validate a string choice at CLI/file boundaries with the existing
SettingSpec parser; default older/missing config to pin. Preserve unknown keys
and existing atomic, owner-only writes. No new credentials or external calls.
Public surface: AutoSwitchSettings.home_mode and autoswitch.homeMode.

autoswitch.py: consume validated settings, resolved account IDs and existing
nullable quota/headroom results. Missing quota cannot authorize a return.
The release threshold and landing threshold use the same boundary; equality
releases home and forbids landing. Existing disabled/quarantined/expired-login
checks still apply. Reuse existing locks and target freshening; do not launch,
kill, signal or replay Claude tasks. Normal cooldown remains for ordinary moves;
preferred return intentionally retains the existing cooldown bypass.

## Test contract

Medium tests use the existing real engine/switcher with temporary filesystem and
fake external credential/usage boundaries. Oracles are the requested account
order and known quota values, not implementation-derived values. Cover both
home modes, below/equal/above threshold, usable/unknown/limited/disabled home,
busy/quiet sessions, recent cooldown, and per-model/account-wide quota.
Mutation targets: invert preferred release, remove quota guard, change <= to <,
ignore switchUnderLoad, or change default to prefer. Each has a regression case.

Scope addition after source review: usage_store pulls the first post-reset poll
forward without clearing backoff or stealing claims. Tests use reset-minus-10
fresh snapshots, reset-minus-1/reset/reset-plus-1 boundaries, provider 429,
claimed work, unchanged reset timestamps and later weekly limits. The store's
existing identity-guarded locked mutation remains the only writer.

claude_locks and switcher additionally acquire native .storage-write.lock (15s
staleness) inside OAuth locks and before the config lock, throughout secure-store
read/merge/write and rollback. Tests verify lock contention prevents mutation,
the lock is held during a real switch, and unrelated MCP credentials survive.
Lock acquisition errors propagate through the existing switch error channel;
context managers release all acquired locks on both success and exception.

Known limitation: credential reload does not wake a CLI turn already halted at a
usage limit. Use a proactive threshold (95%) to reduce this case; such a paused
turn may still require a manual "continue". No second process is resumed against
the same transcript. macOS cache refresh is normally up to30s, not instantaneous.

## Execution policy

No application repository changes; no push or deployment. Native login uses
Claude Code and macOS Keychain. Tests, lint and builds use the test farm with
no local fallback. Build skill authorizes read-only analysis/review agents.
Source is a new separate checkout; do not query or reindex the unrelated
`local/tgm-survey-platform` CodeSift index for these files.

## Status

Native Tatiana and greg.laski logins were verified and enrolled using the exact
macOS Keychain service for each profile, via an in-memory import. No credential
was printed or placed in this repository. Greg profile is awaiting email login.
Use the default `claude` command in Zed for the shared auto-switch policy. Do not
run the enrollment profiles alongside it: duplicated OAuth refresh families
must not be independently refreshed in two stores.

Configuration: primary Tatiana; prefer mode; threshold95%; interval15s;
hysteresis10 percentage points; model Fable (matching the existing Claude
configuration); paid API-key account fallback disabled. Account slots1/2/3 are
Tatiana/greg.laski/greg. The third slot is enrolled after login completes.

2026-09-17 live check: Tatiana five-hour window100%, no general weekly window,
but Fable-specific window100% until2026-09-19T05:59:59Z. A five-hour reset alone
therefore does not make this account usable for Fable. User was asked whether
to preserve Fable or return using a different model; pending answer, preserve
the existing model. Dry-run correctly held greg.laski with usable quota.

## Verification

Upstream baseline:123 targeted tests passed. Changed code then passed697
targeted tests and2252 full tests (3 existing platform skips); subsequent full
run passed2255 with3 skips. Final production snapshot:2258 passed,3 skipped; final test-only cleanup:46 passed.
Ruff/compile passed and mypy introduced0 diagnostics versus122 upstream.
Final receipts are in `zuvo/proofs`.

The farm lacks uv. `verification/requirements.txt` is exported from the exact
committed uv.lock; a temporary git mirror contains byte-identical changed source
and tests, uses that requirements file instead of uv.lock for farm provisioning,
then installs this package with `pip install --no-deps .`. No suite ran on the Mac.
`verification/check.sh` runs syntax-focused Ruff, compilation, mypy comparison
against the pinned upstream sources, and the full pytest suite. Upstream has
existing mypy diagnostics: this checks introduced diagnostics explicitly and
does not claim the whole inherited repository is type-clean.
