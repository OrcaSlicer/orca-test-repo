#!/usr/bin/env python3
"""Slice every .3mf in test_projects/ via OrcaSlicer and check output size."""

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
TEST_DIR = REPO_ROOT / "test_projects"
RESULT_DIR = REPO_ROOT / "result"
DATA_DIR = REPO_ROOT / "data_dir"
BASELINE_FILE = REPO_ROOT / "baseline.json"
RAW_OUTPUT = RESULT_DIR / "plate_1.gcode"
VALIDATOR_BIN = REPO_ROOT / "bin" / "orca_gcode_validator"

TOLERANCE = 0.20


def tail(text: str, n: int = 50) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def slice_one(threemf: Path, baseline: dict[str, int], orca_bin: Path) -> tuple[bool, str]:
    target_name = threemf.name.replace(".", "_") + ".gcode"
    target = RESULT_DIR / target_name

    if target_name not in baseline:
        return False, f"no baseline entry for {target_name} in {BASELINE_FILE.name}"
    expected = baseline[target_name]
    min_size = int(expected * (1 - TOLERANCE))
    max_size = int(expected * (1 + TOLERANCE))

    RAW_OUTPUT.unlink(missing_ok=True)

    tokens: list[str | Path] = [
        orca_bin,
        "--slice", "0",
        "--allow-newer-file",
        "--datadir", DATA_DIR,
        "--outputdir", RESULT_DIR,
        threemf,
    ]
    command = [str(t) for t in tokens]
    display_command = [
        t if isinstance(t, str)
        else t.name if t is orca_bin
        else os.path.relpath(t, REPO_ROOT)
        for t in tokens
    ]

    print(f">>> {shlex.join(display_command)}", flush=True)

    proc = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    if proc.returncode != 0:
        signal_note = (
            f" (killed by signal {-proc.returncode})" if proc.returncode < 0 else ""
        )
        return False, f"slicer exited {proc.returncode}{signal_note}\nstderr:\n{tail(proc.stderr)}"

    if not RAW_OUTPUT.exists():
        return False, "no plate_1.gcode produced"

    RAW_OUTPUT.replace(target)
    size = target.stat().st_size

    size_ok = min_size <= size <= max_size
    if not size_ok:
        size_msg = (
            f"size {size} bytes outside baseline {expected} +/-{int(TOLERANCE * 100)}% "
            f"[{min_size}, {max_size}]"
        )

    # validate gcode (only for Stanford_Bunny)
    if threemf.name == "Stanford_Bunny.3mf":
        vproc = subprocess.run(
            [str(VALIDATOR_BIN), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        vout = vproc.stdout.strip()
        verrors = vproc.returncode != 0

        if verrors:
            vmsg = f"gcode validation errors:\n{vout}"
        elif size_ok:
            vmsg = "gcode validation: clean"

        if not size_ok:
            return False, f"{size_msg}\n{vmsg}" if verrors else f"{size_msg}\n{vmsg}"

        if verrors:
            return False, f"{size / 1_000_000:.2f} MB (baseline {expected / 1_000_000:.2f} MB) -> {target.name}\n{vmsg}"

        return True, f"{size / 1_000_000:.2f} MB (baseline {expected / 1_000_000:.2f} MB) -> {target.name}"

    if not size_ok:
        return False, size_msg

    return True, f"{size / 1_000_000:.2f} MB (baseline {expected / 1_000_000:.2f} MB) -> {target.name}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Slice every .3mf in test_projects/ via OrcaSlicer and check output size against baseline.json.",
    )
    parser.add_argument(
        "orca_bin",
        type=Path,
        help="Path to the OrcaSlicer executable (e.g. /Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer on macOS).",
    )
    args = parser.parse_args()

    orca_bin: Path = args.orca_bin.expanduser()
    if not orca_bin.exists():
        print(f"ERROR: OrcaSlicer binary not found: {orca_bin}", file=sys.stderr)
        return 1
    print(f"Using OrcaSlicer: {orca_bin.name}")

    if not BASELINE_FILE.exists():
        print(f"ERROR: baseline file not found: {BASELINE_FILE}", file=sys.stderr)
        return 1
    baseline = json.loads(BASELINE_FILE.read_text())

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    for entry in RESULT_DIR.iterdir():
        if entry.is_file():
            entry.unlink()

    inputs = sorted(TEST_DIR.glob("*.3mf"))
    if not inputs:
        print(f"ERROR: no .3mf files found in {TEST_DIR}", file=sys.stderr)
        return 1

    failures: list[tuple[str, str]] = []
    for threemf in inputs:
        ok, msg = slice_one(threemf, baseline, orca_bin)
        if ok:
            print(f"OK   {threemf.name}: {msg}")
        else:
            print(f"FAIL {threemf.name}: {msg}", file=sys.stderr)
            failures.append((threemf.name, msg))

    print()
    print(f"PASSED: {len(inputs) - len(failures)}")
    print(f"FAILED: {len(failures)}")
    for name, reason in failures:
        print(f"  - {name}: {reason.splitlines()[0]}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
