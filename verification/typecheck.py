"""Compare mypy diagnostics with the pinned upstream baseline on the test farm."""

import collections
import io
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

FILES = [f"src/claude_swap/{name}.py" for name in (
    "settings", "autoswitch", "claude_locks", "switcher", "usage_store",
)]


def check(root: Path) -> collections.Counter:
    command = ["python", "-m", "mypy", "--follow-imports=silent",
               "--ignore-missing-imports", "--no-incremental", *FILES]
    try:
        result = subprocess.run(
            command, cwd=root, capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired as exc:
        # A hung mypy is a verification FAILURE with a name, not a traceback
        # that reads like a bug in this script.
        raise RuntimeError(f"mypy timed out after {exc.timeout}s in {root}") from exc
    if result.returncode not in (0, 1):
        raise RuntimeError(f"mypy did not run: {result.stdout}{result.stderr}")
    # Exit 1 is BOTH "mypy found errors" and "python -m mypy: No module named
    # mypy". Without this check the second case reads as a clean run: stdout is
    # empty, the diagnostics Counter is empty, baseline and current both come
    # back 0, and the gate prints `introduced=0` having type-checked nothing.
    # mypy is not in verification/requirements.txt, so a fresh environment hits
    # exactly that path. Demand the summary line mypy always emits.
    if not re.search(r"^(Success:|Found \d+ error)", result.stdout, re.M):
        raise RuntimeError(
            "mypy produced no summary line — it did not actually run "
            f"(exit {result.returncode}).\nstderr: {result.stderr.strip()}\n"
            "Install it in the verification environment (uv add --group dev mypy)."
        )
    diagnostics = collections.Counter(
        re.sub(r":\d+(?::\d+)?:", ":", line, count=1)
        for line in result.stdout.splitlines() if ": error:" in line
    )
    return diagnostics


with tempfile.TemporaryDirectory(prefix="cswap-types-") as directory:
    baseline_archive = Path("verification/upstream-src.tar")
    # The fallback archives HEAD — the very commit under test — so baseline and
    # current are the same source and `introduced` is 0 by construction. That is
    # a vacuous pass, and a silent one, so say it out loud in the output the
    # reader actually sees rather than letting the zero speak for itself.
    pinned = baseline_archive.is_file()
    if pinned:
        archived = baseline_archive.read_bytes()
    else:
        print(
            f"WARNING: {baseline_archive} is missing — falling back to "
            "`git archive HEAD`, which compares this commit against itself. "
            "`introduced=0` below proves NOTHING; restore the pinned baseline."
        )
        archived = subprocess.run(
            ["git", "archive", "HEAD", "src"], check=True, capture_output=True
        ).stdout
    try:
        with tarfile.open(fileobj=io.BytesIO(archived)) as archive:
            # filter="data" (3.12+) refuses absolute paths, traversal and
            # symlinks escaping the destination.
            archive.extractall(directory, filter="data")
    except tarfile.TarError as exc:
        # A corrupt or truncated baseline is a verification failure with a
        # name, not a traceback that reads like a bug in this script.
        raise RuntimeError(
            f"baseline archive {baseline_archive} is unreadable: {exc}"
        ) from exc
    baseline = check(directory)
    current = check(Path.cwd())
    introduced = current - baseline
    print(f"mypy: upstream={baseline.total()} current={current.total()} introduced={introduced.total()}")
    for error, count in introduced.items():
        print(f"NEW ({count}): {error}")
    raise SystemExit(1 if introduced else 0)
