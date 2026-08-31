#!/usr/bin/env bash
# Local entry point: sets up an isolated venv (once) and runs the suite.
#
#   ./run_tests.sh /path/to/orca-slicer               # everything
#   ./run_tests.sh /path/to/orca-slicer -k crash       # just one marker/keyword
#   ORCA_BIN=/path/to/orca-slicer ./run_tests.sh       # binary via env instead
#
# Any extra arguments are passed straight through to pytest.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

if [ -n "${ORCA_BIN:-}" ]; then
  BIN="$ORCA_BIN"
else
  BIN="${1:-}"
  if [ -n "$BIN" ]; then shift; fi
fi

if [ -z "${BIN:-}" ]; then
  echo "Usage: $0 /path/to/orca-slicer [pytest-args...]" >&2
  echo "   or: ORCA_BIN=/path/to/orca-slicer $0 [pytest-args...]" >&2
  exit 2
fi
if [ ! -x "$BIN" ]; then
  echo "ERROR: not an executable file: $BIN" >&2
  exit 2
fi

VENV="$REPO_ROOT/.venv"
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -r requirements.txt
fi

# The leading "." is required, not cosmetic: pytest's rootdir/conftest
# discovery inspects raw argv for path-like values before it knows which
# options a not-yet-loaded conftest.py will register. --orca-bin's value is
# itself a real filesystem path, so without an explicit test-path argument
# pytest can anchor rootdir discovery on THAT path instead of the repo,
# conftest.py never loads, and --orca-bin comes back "unrecognized". Always
# invoke with an explicit path (here, ".") ahead of any custom path-valued
# option -- see TESTING_STRATEGY.md.
exec "$VENV/bin/python" -m pytest . -c pytest.ini --orca-bin "$BIN" "$@"
