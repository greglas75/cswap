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


def check(root):
    command = ["python", "-m", "mypy", "--follow-imports=silent",
               "--ignore-missing-imports", "--no-incremental", *FILES]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=120)
    if result.returncode not in (0, 1):
        raise RuntimeError(f"mypy did not run: {result.stdout}{result.stderr}")
    diagnostics = collections.Counter(
        re.sub(r":\d+(?::\d+)?:", ":", line, count=1)
        for line in result.stdout.splitlines() if ": error:" in line
    )
    return diagnostics


with tempfile.TemporaryDirectory(prefix="cswap-types-") as directory:
    baseline_archive = Path("verification/upstream-src.tar")
    archived = (
        baseline_archive.read_bytes() if baseline_archive.is_file()
        else subprocess.run(["git", "archive", "HEAD", "src"],
                            check=True, capture_output=True).stdout
    )
    with tarfile.open(fileobj=io.BytesIO(archived)) as archive:
        archive.extractall(directory, filter="data")
    baseline = check(directory)
    current = check(Path.cwd())
    introduced = current - baseline
    print(f"mypy: upstream={baseline.total()} current={current.total()} introduced={introduced.total()}")
    for error, count in introduced.items():
        print(f"NEW ({count}): {error}")
    raise SystemExit(1 if introduced else 0)
