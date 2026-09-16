#!/usr/bin/env bash
set -euo pipefail
python -m pip install --no-deps .
python -m pip install ruff==0.15.20 mypy==2.1.0
python -m ruff check --select E9,F63,F7,F82 src tests/test_preferred_home.py tests/test_preferred_reset.py
python -m compileall -q src
python verification/typecheck.py
python -m pytest -q
