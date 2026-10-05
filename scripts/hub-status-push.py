#!/usr/bin/env python3
"""hub-status-push — send every machine's `cswap list` / `cswap codex list` to the mockup hub.

The cswap guide on https://tgm-mockups.pages.dev (claude-account-switcher/cswap-guide) shows a
live tab per machine. This collects the JSON of both lists from the Mac (local), ryzen-dev
(user greglas) and the CI hosts ryzen-tf / waw-tf (user gha, over root ssh), and writes one
document per machine into the project's shared data (/api/db, col "status", id = machine).

Runs on the Mac every minute (LaunchAgent com.greglas.cswap-hub-status): the hub owner password
stays in the Mac's keychain and never goes to a host. A machine that does not answer keeps
its last document; the page shows how old each one is.

The same round also shares usage readings (`cswap import-usage`): the Mac's Claude list goes
to every host with a hold of SHARE_HOLD_S, so the hosts stop polling the accounts the Mac
already polls (four machines polling one account hit the usage endpoint's 429 budget), and
each host's list comes back to the Mac without a hold. While the Mac sleeps no hold is
renewed, and the hosts poll for themselves again once it lapses.

    scripts/hub-status-push.py               # one round
    scripts/hub-status-push.py --print       # collect and print, send nothing, share nothing
    scripts/hub-status-push.py --no-share    # send to the hub, share no readings
"""
import concurrent.futures
import json
import subprocess
import sys
import time
import urllib.request

HUB = "https://tgm-mockups.pages.dev"
APP = "claude-account-switcher--cswap-guide"
BOTH = "cswap list --json 2>/dev/null; echo '@@CODEX@@'; cswap codex list --json 2>/dev/null"
SSH = ["ssh", "-o", "ConnectTimeout=15", "-o", "BatchMode=yes"]
TARGETS = {
    # id: (label, cmd -> argv running a shell command as that machine's cswap user)
    "mac": ("Mac", lambda cmd: ["bash", "-lc", cmd]),
    "ryzen-dev": ("ryzen-dev", lambda cmd: SSH + ["ryzen-dev", "bash -lc " + json.dumps(cmd)]),
    "ryzen-tf": ("ryzen-tf (CI)", lambda cmd: SSH + ["ryzen", "sudo -u gha -H bash -lc " + json.dumps(cmd)]),
    "waw-tf": ("waw-tf (CI)", lambda cmd: SSH + ["waw", "sudo -u gha -H bash -lc " + json.dumps(cmd)]),
}
# Three rounds of the one-minute LaunchAgent: one missed round does not hand
# polling back to the hosts, a sleeping Mac does within three minutes.
SHARE_HOLD_S = 180
# A held row must be one somebody measured lately. The Mac's list also carries
# readings it adopted from a host; sent back with a hold they would stop that
# host polling while nobody else does, renewed every round for up to the
# store's one-hour trust ceiling. Past this age a row goes without a hold, so
# the hold lapses and the host polls again: at most about 8 minutes stale.
SHARE_MAX_AGE_S = 300
DROP = {"organizationUuid", "organizationName"}   # not needed on the page


def parse(text):
    # `cswap list --json` is pretty-printed over many lines; a login shell may print a banner first.
    i = text.find("{")
    if i < 0:
        return None
    try:
        return json.loads(text[i:])
    except ValueError:
        return None


def scrub(o):
    if isinstance(o, dict):
        return {k: scrub(v) for k, v in o.items() if k not in DROP}
    if isinstance(o, list):
        return [scrub(v) for v in o]
    return o


def collect(tid):
    label, run_as = TARGETS[tid]
    doc = {"host": tid, "label": label, "at": time.time(), "claude": None, "codex": None, "errors": {}}
    try:
        p = subprocess.run(run_as(BOTH), capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        doc["errors"]["host"] = "timed out after 90 s"
        return doc
    except OSError as e:          # one machine's failure never drops the round
        doc["errors"]["host"] = "could not run: %s" % e
        return doc
    claude_txt, _, codex_txt = p.stdout.partition("@@CODEX@@")
    # Unscrubbed for import-usage, which matches rows by organizationUuid;
    # main() drops it before anything goes to the hub.
    doc["raw_claude"] = parse(claude_txt)
    doc["claude"] = scrub(doc["raw_claude"])
    doc["codex"] = scrub(parse(codex_txt))
    if doc["claude"] is None:
        doc["errors"]["claude"] = (p.stderr.strip().splitlines() or ["no output from cswap list --json"])[-1][:300]
    if doc["codex"] is None:
        doc["errors"]["codex"] = "no output from cswap codex list --json (no Codex accounts, or an older cswap)"
    return doc


def import_into(tid, raw, hold_s):
    """Hand one machine's `cswap list --json` to another machine's usage store."""
    cmd = "cswap import-usage -" + (" --hold %d" % hold_s if hold_s else "")
    try:
        p = subprocess.run(TARGETS[tid][1](cmd), input=json.dumps(raw), capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as e:
        return "%s: %s" % (tid, e)
    if p.returncode != 0:
        return "%s: %s" % (tid, (p.stderr.strip().splitlines() or ["exit %d" % p.returncode])[-1][:300])
    return None


def share_usage(docs):
    """Mac readings to every host (held); each host's readings to the Mac (not held)."""
    raws = {d["host"]: d["raw_claude"] for d in docs if isinstance(d.get("raw_claude"), dict)}
    jobs = []
    if "mac" in raws:
        fresh = dict(raws["mac"], accounts=[
            a for a in raws["mac"].get("accounts") or []
            if isinstance(a, dict) and isinstance(a.get("usageAgeSeconds"), (int, float))
            and a["usageAgeSeconds"] <= SHARE_MAX_AGE_S])
        # Only machines that answered this round: an unreachable one would
        # just burn another ssh timeout.
        jobs += [(tid, fresh, SHARE_HOLD_S) for tid in raws if tid != "mac" and fresh["accounts"]]
        jobs += [("mac", raw, 0) for tid, raw in raws.items() if tid != "mac"]
    with concurrent.futures.ThreadPoolExecutor(max(1, len(jobs))) as ex:
        failures = [f for f in ex.map(lambda j: import_into(*j), jobs) if f]
    for f in failures:
        print(time.strftime("%F %T ") + "import-usage failed on " + f, file=sys.stderr)
    return not failures


def owner_password():
    return subprocess.run(["security", "find-generic-password", "-s", "tgm-mockups-hub", "-a", "tgm", "-w"],
                          capture_output=True, text=True, check=True).stdout.strip()


def previous_docs(token):
    """The hub's current documents (None if they could not be read), so a half
    that failed this round keeps its last good value instead of being
    overwritten with nothing."""
    req = urllib.request.Request("%s/api/db?app=%s&since=0" % (HUB, APP),
                                 headers={"Authorization": "Bearer " + token, "User-Agent": "cswap-hub-status/1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return {d["id"]: d["body"] for d in json.load(r).get("docs", []) if d.get("col") == "status" and d.get("body")}
    except Exception as e:  # noqa: BLE001 — best-effort; without it a failed half is sent as null
        print(time.strftime("%F %T ") + "could not read previous documents: %s" % e, file=sys.stderr)
        return None


def put(token, doc):
    body = json.dumps({"app": APP, "col": "status", "id": doc["host"], "body": doc}).encode()
    req = urllib.request.Request(HUB + "/api/db", data=body, method="PUT",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + token,
                                          # Cloudflare answers Python's default UA with 403 (error 1010).
                                          "User-Agent": "cswap-hub-status/1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


def main():
    dry = "--print" in sys.argv
    with concurrent.futures.ThreadPoolExecutor(len(TARGETS)) as ex:
        docs = list(ex.map(collect, TARGETS))
    shared = True if dry or "--no-share" in sys.argv else share_usage(docs)
    for d in docs:
        d.pop("raw_claude", None)   # carries organizationUuid: never to the hub
    if dry:
        print(json.dumps(docs, indent=1)[:4000])
        return 0
    try:
        token = owner_password()
    except (subprocess.CalledProcessError, OSError) as e:
        # A locked keychain (screen locked at login) is a reason to skip this
        # round, not a traceback every minute; the documents on the hub stay.
        print(time.strftime("%F %T ") + "hub password unavailable from the keychain (%s) — nothing sent" % e,
              file=sys.stderr)
        return 1
    prev = previous_docs(token)
    prev_known = prev is not None
    prev = prev or {}
    rc = 0
    for d in docs:
        old = prev.get(d["host"]) or {}
        if d["claude"] is None and d["codex"] is None:
            print(time.strftime("%F %T ") + "%s: nothing collected (%s) — its last document stays" % (d["host"], d["errors"]), file=sys.stderr)
            rc = 1
            continue
        if not prev_known and (d["claude"] is None or d["codex"] is None):
            # Without the previous document a failed half would be sent as
            # null over the good one on the hub: skip this host this round.
            print(time.strftime("%F %T ") + "%s: partial and previous unknown — not sent" % d["host"], file=sys.stderr)
            rc = 1
            continue
        for half in ("claude", "codex"):
            if d[half] is None and old.get(half) is not None:
                d[half] = old[half]
                # When that half was collected — carried over already, keep its
                # original time, or a stale half would look a minute old forever.
                was = old.get("stale") if isinstance(old.get("stale"), dict) else {}
                d.setdefault("stale", {})[half] = was.get(half) or old.get("at")
        try:
            put(token, d)
        except Exception as e:  # one machine's failure never stops the others
            print(time.strftime("%F %T ") + "%s: send failed: %s" % (d["host"], e), file=sys.stderr)
            rc = 1
    return rc if shared else 1


if __name__ == "__main__":
    sys.exit(main())
