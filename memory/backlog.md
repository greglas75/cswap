# Backlog

Deferred findings from `zuvo:review c14ab71..b9fd3bd` (2026-09-20). Every entry
carries a defer-reason: only NIT and multi-file structural refactors may be
deferred — a localized fix belongs in the review's own fix loop, not here.

- [ ] **B-rank-decompose** [structural-refactor (multi-file)] `_rank_candidates`
  (`src/claude_swap/autoswitch.py`) is ~200 lines carrying five interleaved
  policies (consume-first ordering, model pool-shield, early-swap hysteresis,
  home-prefer, reserve). The reserve filter is the third policy bolted onto the
  end in a row. Recipe for `zuvo:refactor`: (1) extract the reserve filter —
  lowest risk, already delimited by its own comment block; (2) extract the
  shield-state setup into `_voluntary_shield_state(...) -> (shield,
  active_burned, active_voluntary_h)`; (3) extract the sort-key construction
  into `_candidate_sort_key(...)`; (4) turn the per-candidate body into a local
  `qualify(num, h)` closure — a method would need an unworkable parameter
  count. Target: a ~40-60 line orchestrator, each piece independently testable.

- [ ] **B-reserve-warn** [NIT] `_reserve_slot` swallows an unresolvable
  `reserveAccount` and returns None with no `ConfigWarningEvent`, unlike
  `_home_slot`'s warn-once. A typo'd key is then indistinguishable from an
  unset one — the same silent-inert class as the bare-int gap already fixed in
  4eea8d0, just one layer further out. Blocked on: the warn-once state is
  tick-local and `_rank_candidates` is replayed twice per tick, so the warning
  has to be emitted by the caller, not from inside the pure resolver.

- [ ] **B-return-home-clause** [NIT] `_return_home`'s refusal condition
  (`src/claude_swap/autoswitch.py`) has clause (a)
  `100.0 - available >= threshold` which, for every `hysteresis_pct >= 0`, is
  implied by clause (b). It never fires independently. Harmless, but reads as
  two live checks where there is one.

- [ ] **B-mypy-pin** [NIT] `mypy` is not in `verification/requirements.txt`, so
  the type-check gate cannot actually run in a fresh environment. b9fd3bd makes
  that failure LOUD rather than silent, but the dependency still needs pinning
  (`uv add --group dev mypy`) for the gate to do its job at all.

## Session 2026-10-05 — Codex rotation, real-store guard, upstream ports

Left undone in that session: on purpose, by accident, for lack of time, or
because it was out of scope. Review-deferred items carry their review ID.

- [ ] **B-import-usage** [feature, in progress] Port `cswap import-usage
  <path|-> [--hold SECONDS]` from realiti4 `45fdcfc` + `ff1f1b2` (usage_store,
  transfer, cli, json_output, switcher + tests). Then distribute readings in
  `scripts/hub-status-push.py`: Mac → hosts with `--hold`, hosts → Mac
  without. It needs the raw JSON with `organizationUuid`, which the hub docs
  scrub. Why: the same accounts sit on 4 machines and each polls the usage API
  itself; ryzen already logged `http-429 … per-token usage budget reached`.

- [ ] **B-login-expiry-ship** [follow-up] `loginExpiresAt` (bb1955e) is
  committed and tested, but not yet reviewed (push gate), pushed to `mine`, or
  deployed with `scripts/deploy-hosts.sh`. The hub page (cswap-guide live
  tabs) does not show it yet: it should say "log in again before X".

- [ ] **B-upstream-204** [investigation] Upstream #204 (`560dc8e`): past
  `RECOVERY_HORIZON_S` (4 h), rank the over-threshold escape by headroom
  instead of reset time. This has to be weighed against the owner's rule
  "prefer the account whose week ends soonest". Not yet analysed.

- [ ] **B-upstream-drift** [ops] The fork (based on 0.24.0b1) and realiti4
  (0.27.0b1) share no history (`git merge-base` is empty), so every upstream
  fix is a manual port. Nothing records which upstream commits were ported or
  rejected; keep a ledger (e.g. `docs/upstream-ports.md`).

- [ ] **B-policy-one-module** [structural-refactor (multi-file)] STRUCT-1 of
  the codex-rotation review. The reserve/ranking policy lives in three copies:
  `autoswitch._rank_candidates`, `switcher._in_switch_order` (list order) and
  `codex.rank`. They already disagreed once (R6). Move them into one leaf
  module that all three call.

- [ ] **B-split-codex-cli-tick** [structural-refactor] STRUCT-3/4: split
  `cli._codex_command` (one function for login/add/list/switch/auto) and
  `codex._auto_tick` (credits mode, revoked-live, threshold and reserve in one
  body).

- [ ] **B-one-deploy-door** [structural-refactor] STRUCT-9: there are two
  deploy paths, `scripts/deploy.sh` and `scripts/deploy-hosts.sh`. Keep one.

- [ ] **B-codex-settings-once** [behavior, pre-existing] `cswap codex auto`
  reads settings once at start. A changed `codexReserveAccount`, threshold or
  `codexAfterSwitch` takes effect only after the `cswap-codex` unit restarts,
  and nothing says so.

- [ ] **B-keychain-outage-wait** [behavior, pre-existing since c14ab71] When
  the Keychain is unavailable, the engine finds no candidates and waits the
  long no-candidates interval instead of retrying soon.

- [ ] **B-codex-switch-interrupts** [design] Every Codex switch restarts the
  `app-server` daemon (its login is cached in memory). Running Codex turns are
  cut off and resumed by the watchdog. Credits mode is sticky to limit this,
  but threshold switches still interrupt. It was not measured how often an
  in-flight turn is lost rather than resumed.

- [ ] **B-codex-multi-host-login** [investigation] A plain `codex login`
  revokes that account's previous login (401 `token_revoked`). It was not
  verified whether logging the same Codex account in on host B (each machine
  logs in on its own; copying login files between machines is forbidden)
  revokes host A's stored copy. If it does, the 4-machine setup keeps knocking
  itself out.

- [ ] **B-after-switch-hook-hosts** [ops] `codexAfterSwitch`
  (`~/.local/bin/codex-account-switched`) is configured only on ryzen-dev. The
  Mac and the CI hosts (gha) have no hook. That is harmless where no
  long-lived Codex daemon runs, but it was not checked on the Mac.

- [ ] **B-ci-holds-all-logins** [security] `cswap-ci` puts every Claude and
  Codex login of the owner into `gha` on ryzen-tf and waw-tf. Those runners
  execute PR code (private repos only), so one malicious or compromised PR
  could read every account's refresh token. Consider a separate, minimal
  account set for CI, or keep the store unreadable to job processes.

- [ ] **B-hub-push-mac-only** [ops, accepted for now] `hub-status-push.py`
  runs only on the Mac (the hub password stays in its keychain). While the Mac
  sleeps, every tab goes stale; the page shows the age but nothing alerts.

- [ ] **B-guard-limits** [accepted limit, real-store guard review] A
  standalone script that copies fixture data without importing test code is
  caught only by EngineHarness's temp-dir check and the reserved domains, not
  by provenance. Without a passwd entry (Windows) the guard does nothing.

- [ ] **B-tests-untested-scripts** [test-coverage] `scripts/hub-status-push.py`
  (merge of stale halves, skip rules), `scripts/cswap-ci` and
  `scripts/deploy-hosts.sh` have no tests. Each was checked only by running it
  live.

- [ ] **B-tests-cli-q24** [test-coverage] From the codex-rotation review: the
  CLI layer is covered only for `--json`. Q24/Q25 (random test order, a
  coverage gate) are not enforced repo-wide.

- [ ] **B-mutation-not-run** [test-coverage] Neither the codex-rotation range
  nor the guard range got a real `zuvo:mutation-test` run. Two fixes (rank,
  scan pin) were reverted by hand to prove their tests fail; nothing else was.

- [ ] **B-leak-copy-cleanup** [ops, owner's call] The Mac still holds
  `~/.claude-swap-test-leak-20261005-152116`, a copy of the store as the test
  leak left it, kept as evidence. It may contain live tokens. Delete it once
  the owner agrees. The same goes for `~/.ssh/config.bak-2026-10-05` and the
  `.bashrc` backup on ryzen-dev.
