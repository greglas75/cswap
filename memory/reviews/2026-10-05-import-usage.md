# Review 2026-10-05 — import-usage, loginExpiresAt, usage sharing

Verified-against: 3791739ea52bc37b28b2e10e0639aa554d26f971
Range: fed5b70..3791739 (code: oauth, json_output, switcher, transfer, usage_store, cli, scripts/hub-status-push.py; tests)
Adversarial: zuvo/proofs/import-usage-ab140ca957-adversarial.txt — diff-only, two chunks (19 KB + 16 KB), no truncation; cursor-agent, openrouter-3, byteplus, kimi, codex-5.3, openrouter-4.
An earlier pass that sent whole files (151 KB, truncated, exit 4) was discarded; its proof was deleted, not cited.
Verdict: APPROVE after fixes. Suite: 2455 passed, 3 skipped (rt, farm).

## R1 — [RECOMMENDED] Host readings never reached the Mac when the Mac's own list failed (fixed ab140ca)
scripts/hub-status-push.py:132 — the host->Mac jobs sat inside `if "mac" in raws`; now built from every answering host.

## R2 — [RECOMMENDED] Negative or boolean usageAgeSeconds counted as fresh and earned a hold (fixed ab140ca)
scripts/hub-status-push.py:111 — `_measured_lately` excludes bool and requires 0 <= age <= SHARE_MAX_AGE_S.

## R3 — [RECOMMENDED] Remote command quoted with json.dumps, which leaves $ and ` to the remote shell (fixed ab140ca)
scripts/hub-status-push.py:37 — shlex.quote for every ssh target.

## R4 — [RECOMMENDED] Concurrent importers into one machine's store (fixed ab140ca)
scripts/hub-status-push.py:137 — jobs aimed at one machine run one after another.

## R5 — [RECOMMENDED] A corrupt refreshTokenExpiresAt (1e300, NaN) crashed list --json (fixed 3791739)
src/claude_swap/oauth.py:79 — OverflowError/OSError/ValueError and NaN return None; tests added.

## R6 — [NIT] A reading the Mac adopted from a host can return to that host with a hold (accepted, bounded)
scripts/hub-status-push.py:52 — only rows <= 300 s old are held, so a host that alone polls an account polls at worst every ~8 min.

## R7 — [NIT] hub-status-push hides cswap's stderr (pre-existing, backlog B-hub-stderr-dropped)
scripts/hub-status-push.py:33 — `2>/dev/null` in BOTH.

## Dropped (verified false)
- `entries[num]` keyed by str vs int in the held skip (src/claude_swap/switcher.py:4834): same pattern as the lines above it; test_a_held_active_account_shows_its_reading_not_token_expired goes through it.
- bool usageAgeSeconds accepted by import_usage: transfer.py rejects bool explicitly.
- the "all" scoped-window sentinel: oauth.relevant_windows supports it (match_all).
- `--hold 0` with no matched row lifts nothing (src/claude_swap/usage_store.py:831): by design (upstream ff1f1b2) — holds are per account in the document.

## Accepted limits
- While a hold is renewed, the host does not fetch that account's usage, so the token refresh the fetch path would do waits for the hold to lapse; Claude Code and pre-freshen still refresh tokens.
- import_usage reads the whole document without a size bound (it comes from our own cswap over ssh).
