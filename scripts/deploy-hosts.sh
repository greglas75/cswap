#!/usr/bin/env bash
# deploy-hosts.sh — put THIS checkout's HEAD on every machine cswap runs on, in one go:
#
#   mac        LaunchAgents com.greglas.claude-account-switcher + com.greglas.cswap-codex run
#              this checkout's .venv (editable install) — they only need a restart.
#   ryzen-dev  user greglas, systemd --user units cswap-auto + cswap-codex (cc-remote).
#   ryzen      ryzen-tf, as root: the CI user gha, system units cswap-auto + cswap-codex.
#   waw        waw-tf, same as ryzen.
#
# The hosts install from a `git archive` of HEAD (~/.local/src/cswap, HEAD in its COMMIT
# file), not from GitHub: the fork's commits are not all pushed, and a host updated on its
# own drifted behind the Mac (2026-10-01: ryzen ran a cswap without the soonest-reset
# ranking). A deploy counts only when every daemon runs the NEW code — each must have
# started after its install (the half-rollout deploy.sh exists for, CON-954).
#
# Usage: scripts/deploy-hosts.sh [target ...]   (default: mac ryzen-dev ryzen waw)
# Exit 0 = every target verified; 1 = at least one failed (named in the summary).
set -u
cd "$(dirname "$0")/.." || exit 1
TARGETS=("$@"); [ ${#TARGETS[@]} -eq 0 ] && TARGETS=(mac ryzen-dev ryzen waw)
HEAD=$(git rev-parse --short HEAD) || exit 1
if ! git diff --quiet HEAD -- src pyproject.toml; then
  echo "deploy-hosts: WARNING — uncommitted changes under src/ reach only the Mac; the hosts get HEAD $HEAD" >&2
fi
TAR=$(mktemp); trap 'rm -f "$TAR"' EXIT
git archive --format=tar --prefix=cswap/ HEAD > "$TAR" || exit 1

# Runs on a Linux host as `bash -c "$REMOTE" _ <user|gha> <commit>`; the tarball is stdin.
REMOTE='
set -u
mode=$1; commit=$2
if [ "$mode" = gha ]; then
  run() { sudo -u gha -H bash -lc "$1"; }; H=/home/gha; ctl() { systemctl "$@"; }
else
  run() { bash -lc "$1"; }; H=$HOME; ctl() { systemctl --user "$@"; }
fi
run "rm -rf ~/.local/src/cswap.new && mkdir -p ~/.local/src/cswap.new" || { echo "FAIL mkdir"; exit 1; }
run "tar -x -C ~/.local/src/cswap.new" || { echo "FAIL unpack"; exit 1; }
run "rm -rf ~/.local/src/cswap && mv ~/.local/src/cswap.new/cswap ~/.local/src/cswap && rmdir ~/.local/src/cswap.new && echo $commit > ~/.local/src/cswap/COMMIT" || { echo "FAIL move"; exit 1; }
run "uv tool install --force ~/.local/src/cswap >/dev/null 2>&1" || { echo "FAIL uv tool install"; exit 1; }
installed=$(stat -c %Y "$H/.local/share/uv/tools/claude-swap/uv-receipt.toml")
units=""
for u in cswap-auto cswap-codex; do ctl cat $u.service >/dev/null 2>&1 && units="$units $u"; done
[ -n "$units" ] || { echo "FAIL no cswap units here"; exit 1; }
ctl restart $units
sleep 8
bad=""
for u in $units; do
  pid=$(ctl show -p MainPID --value $u.service)
  if [ "$(ctl is-active $u.service)" != active ] || [ -z "$pid" ] || [ "$pid" = 0 ]; then bad="$bad $u(down)"; continue; fi
  started=$(( $(date +%s) - $(ps -o etimes= -p "$pid" | tr -d " ") ))
  [ "$started" -ge "$installed" ] || bad="$bad $u(stale)"
done
[ -z "$bad" ] && echo "OK$units at $commit" || echo "FAIL$bad"
'

deploy_mac() {
  local labels=(com.greglas.claude-account-switcher com.greglas.cswap-codex) l old new bad=""
  for l in "${labels[@]}"; do
    old=$(launchctl list "$l" 2>/dev/null | awk -F'= ' '/"PID"/{gsub(/;/,"",$2); print $2}')
    launchctl kickstart -k "gui/$(id -u)/$l" >/dev/null 2>&1 || { bad="$bad $l(kickstart)"; continue; }
    sleep 3
    new=$(launchctl list "$l" 2>/dev/null | awk -F'= ' '/"PID"/{gsub(/;/,"",$2); print $2}')
    # A restarted job has a new PID; the same or none means it still runs old code, or nothing.
    { [ -n "$new" ] && [ "$new" != "$old" ]; } || bad="$bad $l(pid ${old:-?}->${new:-none})"
  done
  [ -z "$bad" ] && echo "OK editable checkout ($HEAD$(git diff --quiet HEAD -- src || echo ' + local changes'))" || echo "FAIL$bad"
}

deploy_host() {   # $1 ssh alias, $2 user|gha
  ssh -o ConnectTimeout=15 -o BatchMode=yes "$1" "bash -c $(printf %q "$REMOTE") _ $2 $HEAD" < "$TAR" 2>&1 | tail -1
}

rc=0
for t in "${TARGETS[@]}"; do
  case "$t" in
    mac)       r=$(deploy_mac) ;;
    ryzen-dev) r=$(deploy_host ryzen-dev user) ;;
    ryzen|waw) r=$(deploy_host "$t" gha) ;;
    *)         r="FAIL unknown target (mac ryzen-dev ryzen waw)" ;;
  esac
  [ -n "$r" ] || r="FAIL no answer"
  printf '%-10s %s\n' "$t" "$r"
  case "$r" in OK*) ;; *) rc=1 ;; esac
done
[ $rc -eq 0 ] && echo "deploy-hosts: DEPLOY-OK $HEAD" || echo "deploy-hosts: DEPLOY-INCOMPLETE — see FAIL lines" >&2
exit $rc
