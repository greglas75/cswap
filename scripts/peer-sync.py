#!/usr/bin/env python3
"""Tell two machines sharing one account pool which account the other is on.

Runs on the Mac (it can reach ryzen-dev; ryzen-dev cannot reach the Mac):
every INTERVAL seconds one ssh call writes the Mac's live account into the
host's peer file and brings the host's live account back into the Mac's.
Each side's cswap auto reads its file (autoswitch.peerActiveFile) and never
lands on the other's account; a stale file (sync stopped) means nothing.

Usage: peer-sync.py [host] [--once]     (default host: ryzen-dev)
"""
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time

INTERVAL = 30
LOCAL_ROOT = os.path.expanduser("~/.claude-swap-backup")
REMOTE_ROOT = "~/.local/share/claude-swap"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
       "-o", "ControlMaster=auto", "-o", "ControlPath=~/.ssh/cm-peer-%r@%h:%p",
       "-o", "ControlPersist=5m"]

# Runs on the host: store the Mac's record, print the host's live account.
REMOTE = r'''
import json, os, sys, tempfile
import time
root = os.path.expanduser(sys.argv[1])
fd, tmp = tempfile.mkstemp(dir=root, prefix=".peer-active.")
with os.fdopen(fd, "w") as fh:
    # Stamped with THIS host's clock: the reader compares against its own.
    json.dump({"email": sys.argv[2], "at": time.time()}, fh)
os.replace(tmp, os.path.join(root, "peer-active.json"))
seq = json.load(open(os.path.join(root, "sequence.json")))
n = str(seq.get("activeAccountNumber"))
print((seq.get("accounts", {}).get(n) or {}).get("email") or "")
'''


def live_email(root):
    try:
        seq = json.load(open(os.path.join(root, "sequence.json")))
    except (OSError, ValueError):
        return None
    n = str(seq.get("activeAccountNumber"))
    return (seq.get("accounts", {}).get(n) or {}).get("email")


def write_peer(root, email):
    fd, tmp = tempfile.mkstemp(dir=root, prefix=".peer-active.")
    with os.fdopen(fd, "w") as fh:
        json.dump({"email": email, "at": time.time()}, fh)
    os.replace(tmp, os.path.join(root, "peer-active.json"))


def once(host):
    mine = live_email(LOCAL_ROOT)
    if not mine:
        # Unreadable mid-rewrite: send nothing rather than "no account" over a good record.
        print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          "skipped": "local sequence.json unreadable"}), flush=True)
        return
    record = mine
    # The code goes over stdin and the arguments quoted: ssh joins argv into
    # one string for the remote shell, which split the JSON and the script.
    out = subprocess.run(SSH + [host, "python3 - %s %s" % (shlex.quote(REMOTE_ROOT), shlex.quote(record))],
                         input=REMOTE, capture_output=True, text=True, timeout=60)
    # The last line only (a login banner may come first), and only an address.
    lines = out.stdout.strip().splitlines()
    theirs = lines[-1].strip() if lines else ""
    if out.returncode == 0 and re.fullmatch(r"[^@\s]+@[^@\s]+", theirs):
        write_peer(LOCAL_ROOT, theirs)
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "mac": mine,
                      host: theirs or None, "rc": out.returncode}), flush=True)


def main():
    args = [a for a in sys.argv[1:] if a != "--once"]
    host = args[0] if args else "ryzen-dev"
    while True:
        try:
            once(host)
        except Exception as e:  # noqa: BLE001 — one bad round never ends the loop
            print(json.dumps({"error": f"{type(e).__name__}: {e}"}), flush=True)
        if "--once" in sys.argv:
            return
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
