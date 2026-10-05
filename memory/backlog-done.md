
## Archived from backlog.md on 2026-10-05 (1 completed items moved out)
- [x] **B-upstream-204** [investigation — closed 2026-10-05, not ported]
  Upstream #204 (`560dc8e`) bounds a bug in upstream #202 ("all over the
  threshold: go where quota returns first"), which this fork never had (no
  recovery axis in `autoswitch.py`). Our soonest-reset ordering already keeps
  its lesson: it ranks only accounts with at least `MIN_USEFUL_LIFE_PCT` (10)
  left, so it never trades real headroom for a reset days away; when nothing
  qualifies the engine stays put and the all-exhausted wake switches at the
  first reset. Re-check only if #202 is ever ported.

## Archived from backlog.md on 2026-10-05 (1 ticked WITHOUT a recorded resolution — the reason was never written down; the tick is the only evidence)
- [x] **B-import-usage** [feature — ported 1984bd4, sharing b61c571] Port `cswap import-usage
  <path|-> [--hold SECONDS]` from realiti4 `45fdcfc` + `ff1f1b2` (usage_store,
  transfer, cli, json_output, switcher + tests). Then distribute readings in
  `scripts/hub-status-push.py`: Mac → hosts with `--hold`, hosts → Mac
  without. It needs the raw JSON with `organizationUuid`, which the hub docs
  scrub. Why: the same accounts sit on 4 machines and each polls the usage API
  itself; ryzen already logged `http-429 … per-token usage budget reached`.
