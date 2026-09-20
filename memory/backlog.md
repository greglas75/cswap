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
