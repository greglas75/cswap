#!/usr/bin/env python3
"""hub-status-push — send every machine's `cswap list` / `cswap codex list` to the mockup hub.

The cswap guide on https://tgm-mockups.pages.dev (claude-account-switcher/cswap-guide) shows a
live tab per machine. This collects the JSON of both lists from the Mac (local), ryzen-dev
(user greglas) and the CI hosts ryzen-tf / waw-tf (user gha, over root ssh), and writes one
document per machine into the project's shared data (/api/db, col "status", id = machine).

Runs on the Mac every minute (LaunchAgent com.greglas.cswap-hub-status): the hub owner password
stays in the Mac's keychain and never goes to a host. A machine that does not answer keeps
its last document; the page shows how old each one is.

    scripts/hub-status-push.py            # one round
    scripts/hub-status-push.py --print    # collect and print, send nothing
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
    # id: (label, argv producing "<claude json>@@CODEX@@<codex json>")
    "mac": ("Mac", ["bash", "-lc", BOTH]),
    "ryzen-dev": ("ryzen-dev", SSH + ["ryzen-dev", "bash -lc " + json.dumps(BOTH)]),
    "ryzen-tf": ("ryzen-tf (CI)", SSH + ["ryzen", "sudo -u gha -H bash -lc " + json.dumps(BOTH)]),
    "waw-tf": ("waw-tf (CI)", SSH + ["waw", "sudo -u gha -H bash -lc " + json.dumps(BOTH)]),
}
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
    label, argv = TARGETS[tid]
    doc = {"host": tid, "label": label, "at": time.time(), "claude": None, "codex": None, "errors": {}}
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        doc["errors"]["host"] = "timed out after 90 s"
        return doc
    claude_txt, _, codex_txt = p.stdout.partition("@@CODEX@@")
    doc["claude"] = scrub(parse(claude_txt))
    doc["codex"] = scrub(parse(codex_txt))
    if doc["claude"] is None:
        doc["errors"]["claude"] = (p.stderr.strip().splitlines() or ["no output from cswap list --json"])[-1][:300]
    if doc["codex"] is None:
        doc["errors"]["codex"] = "no output from cswap codex list --json (no Codex accounts, or an older cswap)"
    return doc


def owner_password():
    return subprocess.run(["security", "find-generic-password", "-s", "tgm-mockups-hub", "-a", "tgm", "-w"],
                          capture_output=True, text=True, check=True).stdout.strip()


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
    if dry:
        print(json.dumps(docs, indent=1)[:4000])
        return 0
    token = owner_password()
    rc = 0
    for d in docs:
        if d["claude"] is None and d["codex"] is None:
            print(time.strftime("%F %T ") + "%s: nothing collected (%s) — its last document stays" % (d["host"], d["errors"]), file=sys.stderr)
            rc = 1
            continue
        try:
            put(token, d)
        except Exception as e:  # one machine's failure never stops the others
            print(time.strftime("%F %T ") + "%s: send failed: %s" % (d["host"], e), file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
