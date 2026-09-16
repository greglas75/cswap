# Preferred-account verification

Base: c14ab714adf23a7e273a1bdb2a571d65c09fcfb5.
Source: /Users/greglas/DEV/claude-account-switcher.
Farm: waw-tf through rt; no test suite ran on the Mac.
Exact source/test hashes: verified-snapshot.json.

## Executed checks

- Upstream baseline, rt run1789597046-97549-2649:123 passed.
- Targeted regression battery, run1789597260-52931-20409:697 passed.
- Full battery, run1789597486-81245-27710:2252 passed,3 skipped.
- Full battery after review fixes, run1789597613-14252-13828:2255 passed,3 skipped.
- Final production snapshot, run1789597951-96904-15433:

```text
All checks passed!
mypy: upstream=122 current=122 introduced=0
2258 passed, 3 skipped in 89.93s (0:01:29)
tf: exit 0
```

The lint command is Ruff E9,F63,F7,F82; source compilation also passed.
This is a type-regression check against upstream, not a claim that inherited
code is type-clean. The122 inherited mypy diagnostics are unchanged.
Three existing platform tests are skipped on the Linux farm.
Final test-only cleanup: run1789598117-45014-27840, 46 passed in3.33s, exit0.

## Acceptance evidence

- First/second/third fallback order: real engine and filesystem tests.
- Reset at exact timestamp and reset+1s: real store and collector test, including
  a recent100% snapshot younger than the ordinary cache TTL.
- Provider backoff, active claims and failed first probe: reservation tests.
- No weekly window invented; reported weekly/model quota blocks landing: tests
  plus real native account usage.
- Traffic/cooldown do not prevent configured preferred return; hysteresis avoids
  marginal quota flapping: engine regression tests.
- Native storage lock contention preserves active login; refresh preserves latest
  MCP state and retains a consumed token's successor on live-write failure.
- No CLI process is killed, restarted or independently resumed by these changes.
- Live dry run: held greg.laski while Tatiana5h/Fable limits were exhausted.

## Reviews and dispositions

Same-model analysis found the duplicate quiet gate; fixed and covered by the
regression that initially failed. Test-audit report is ../audits/test-audit-preferred.md:
both scoped files TierA19/22. Its independence is explicitly degraded:same-model
because the prescribed gpt-5.4 role returned unsupported-model. No strict
independence pass or successful content-keyed gate artifact is claimed.

Two cross-provider security reviews ran using cursor-agent, agy and claude.
Artifacts: priority-adversarial.txt and priority-final-adversarial.txt.
Second review was chunked without omitted files; AGY refused the test-only chunk,
while two other providers returned reviews for it. Production chunk had3 reviewers.

Findings fixed: coordinator lock around reset planning; storage lock on switching,
restore and active-refresh merge; return hysteresis; regression cases for retries.

Findings rejected from complete-source evidence:

- Unknown home quota cannot enter the home-preferred early return: its enclosing
  branch requires non-None active_headroom. Existing bounded failure logic remains.
- Failed reset polling does not permanently suppress retries: nextPollAt remains
  due on failure and reservation retries after backoff; explicit regression passes.
- Active refresh already holds FileLock and the OAuth lock pair around the entire
  POST and persistence block. Reacquiring those non-reentrant locks would deadlock.
- Native config writer releases its synchronous file lock without awaiting storage;
  no config-to-storage nesting was demonstrated. Storage staleness15s matches
  installed Claude2.1.273; it is not an invented timeout.
- Model quota is already included in active_headroom. Preferred retention does
  not bypass a configured exhausted model window.
- Candidate sorting explicitly uses key=lambda pair:pair[0]; stable ties preserve
  sequence, not lexicographic account numbers.
- Merge pulls only the shared credential allowlist from latest live bytes and
  preserves the newly refreshed Claude token. Tests verify both values.
- Discarding a successor after its single-use grant was consumed would destroy
  recoverability. Preserve backup and decline unreadable live overwrite instead.
- Switching under traffic is the user-requested opt-in behavior. Tokens affect
  subsequent requests; this implementation never kills in-flight work.

Noncritical follow-up: invalid nonempty homeAccount in prefer mode still orders
fallbacks by sequence rather than best-headroom. The installed home is verified;
document this invalid-configuration case before general distribution.

## Operational limits

This is a local adaptation pinned to inspected Claude Code2.1.273 behavior.
macOS normally rereads changed Keychain login within about30s; failed Keychain
reads can delay it. Usage observations may be delayed by provider Retry-After.
A CLI turn already halted at quota may need manual continuation; proactive95%
rotation reduces that risk but does not guarantee avoiding it.
Tatiana currently has a separate Fable limit through2026-09-19T05:59:59Z, even
though its general weekly window is absent. Do not promise Fable recovery at
the earlier five-hour reset.

## Runtime activation

LaunchAgent verified running on2026-09-16T22:37:05Z. First actual poll: active slot2, home slot1 exhausted; no-switch below-threshold. Third account remains pending login.
