# Review 2026-10-06 — several reserves, live-reserve hand-over

Verified-against: 6be0cfb6e21b6d7c51877ccba1870654e0aa5c30
Range: 07dbbce..6be0cfb
Adversarial: zuvo/proofs/multi-reserve-212f2ece9b-adversarial.txt — diff-only (16 KB), no truncation; cursor-agent, muse, kimi.
Verdict: APPROVE after fixes. Suite: 2463 passed, 3 skipped (rt, farm).

## R1 — [RECOMMENDED] A non-numeric floor suffix silently un-reserved the account (fixed 6be0cfb)
src/claude_swap/settings.py:38 — the identifier is now always what precedes the last ':'; a bad floor keeps the default.

## R2 — [RECOMMENDED] The bad-floor warning repeated on every parse (fixed 6be0cfb)
src/claude_swap/settings.py:35 — warn once per entry.

## R3 — [NIT] Comment pointed at a reserves() that does not exist; sort comment did not cover the reserves-only key (fixed 6be0cfb)
src/claude_swap/autoswitch.py:2967

## Dropped (verified false or accepted)
- self.settings vs tick settings in pre-freshen (src/claude_swap/autoswitch.py:1461): the same pattern as before the change.
- order.index ValueError: reserve keys and row ids are both slot strings from resolve_account.
- reserve-active NoSwitchEvent each tick: the same cadence as below-threshold.
