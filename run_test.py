#!/usr/bin/env python3
"""Compatibility entry point: `run_test.py <orca_bin>`.

OrcaSlicer's own CI (build_orca.yml, "Run external slicer regression tests")
clones this repo and calls exactly this command against its freshly built
binary, with no dependency-install step of its own. The actual suite is now
pytest-based (see TESTING_STRATEGY.md); this wrapper exists purely so that
existing call site keeps working unchanged -- it sets up an isolated venv
(once), installs requirements.txt into it, and delegates. Local/manual use
should prefer `./run_tests.sh` or `pytest` directly; this file's only job is
backward compatibility with the fixed single-argument CLI shape callers
already depend on.
"""
import subprocess
import sys
import venv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
VENV_DIR = REPO_ROOT / ".venv"


def venv_python(venv_dir: Path) -> Path:
    candidate = venv_dir / "bin" / "python"  # POSIX
    if candidate.exists():
        return candidate
    return venv_dir / "Scripts" / "python.exe"  # Windows


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} /path/to/orca-slicer", file=sys.stderr)
        return 2

    orca_bin = Path(sys.argv[1]).expanduser()
    if not orca_bin.exists():
        print(f"ERROR: orca-slicer binary not found: {orca_bin}", file=sys.stderr)
        return 1

    python = venv_python(VENV_DIR)
    if not python.exists():
        print("Setting up test environment (first run only)...", file=sys.stderr)
        venv.EnvBuilder(with_pip=True).create(VENV_DIR)
        python = venv_python(VENV_DIR)
        subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet",
             "-r", str(REPO_ROOT / "requirements.txt")],
            check=True,
        )

    # If the binary was built from a source tree we can see (OrcaSlicer's own CI
    # runs us from $GITHUB_WORKSPACE, and a local build lives under the checkout),
    # enumerate the CLI option surface from that tree so the suite is always
    # matched with the definitions the binary was compiled from.
    extra = []
    source = find_source_tree(orca_bin)
    if source:
        print(f"Using OrcaSlicer source tree for the option surface: {source}", file=sys.stderr)
        extra = ["--orca-source", str(source)]

    # The leading "." is required -- see TESTING_STRATEGY.md's pytest gotcha note.
    proc = subprocess.run(
        [str(python), "-m", "pytest", ".", "-c", "pytest.ini", "--orca-bin", str(orca_bin), "-v", *extra],
        cwd=str(REPO_ROOT),
    )
    return proc.returncode


def find_source_tree(orca_bin: Path):
    import os

    candidates = [os.environ.get("ORCA_SOURCE"), os.environ.get("GITHUB_WORKSPACE")]
    candidates += [str(p) for p in orca_bin.resolve().parents]
    for c in candidates:
        if c and (Path(c) / "src" / "libslic3r" / "PrintConfig.cpp").exists():
            return Path(c)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
