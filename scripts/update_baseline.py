#!/usr/bin/env python3
"""Regenerate baseline.json by slicing every test_projects/*.3mf and
recording the slicing metrics (see gcode_metrics.py).

Run after a deliberate, reviewed change to slicing output (or when adding a
fixture model) -- never to silence a test_golden_slices.py failure you have
not actually looked at.

    python scripts/update_baseline.py /path/to/orca-slicer [--only Model.3mf]
"""
import argparse
import datetime
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
import gcode_metrics as gm  # noqa: E402

TEST_PROJECTS = REPO_ROOT / "test_projects"
DATA_DIR_SEED = REPO_ROOT / "data_dir"
BASELINE_FILE = REPO_ROOT / "baseline.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("orca_bin", type=Path)
    parser.add_argument("--only", help="only regenerate this one .3mf filename")
    args = parser.parse_args()

    orca_bin = args.orca_bin.expanduser()
    if not orca_bin.is_file():
        print(f"ERROR: orca-slicer binary not found: {orca_bin}", file=sys.stderr)
        return 1
    version = subprocess.run([str(orca_bin), "--help"], capture_output=True, text=True).stdout.splitlines()[0].rstrip(":")

    baseline = json.loads(BASELINE_FILE.read_text()) if BASELINE_FILE.exists() else {}
    models = baseline.get("models") if isinstance(baseline.get("models"), dict) else {}
    # migrate a legacy size-only baseline {name: int}
    if not models and all(isinstance(v, (int, float)) for v in baseline.values()):
        models = {k: {"metrics": {"size_bytes": int(v)}} for k, v in baseline.items()}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        datadir = tmp_path / "datadir"
        outputdir = tmp_path / "result"
        shutil.copytree(DATA_DIR_SEED, datadir)
        outputdir.mkdir()

        inputs = sorted(TEST_PROJECTS.glob("*.3mf"))
        if args.only:
            inputs = [p for p in inputs if p.name == args.only]
            if not inputs:
                print(f"ERROR: no match for --only {args.only!r}", file=sys.stderr)
                return 1

        for threemf in inputs:
            key = threemf.name.replace(".", "_") + ".gcode"
            gcode = outputdir / "plate_1.gcode"
            gcode.unlink(missing_ok=True)
            proc = subprocess.run(
                [str(orca_bin), "--slice", "0", "--allow-newer-file", "--datadir", str(datadir),
                 "--outputdir", str(outputdir), str(threemf)],
                capture_output=True, text=True,
            )
            if proc.returncode != 0 or not gcode.exists():
                print(f"FAIL {threemf.name}: exit {proc.returncode}\n{proc.stderr[-1000:]}", file=sys.stderr)
                continue
            metrics = gm.parse_file(gcode)
            old = (models.get(key) or {}).get("metrics", {})
            models[key] = {"metrics": metrics, "captured_with": version,
                           "captured_on": datetime.date.today().isoformat()}
            changed = {k: (old.get(k), v) for k, v in metrics.items() if old.get(k) != v}
            print(f"{key}: {json.dumps(metrics)}" + (f"\n    changed: {changed}" if old and changed else ""))

    BASELINE_FILE.write_text(json.dumps({"_comment": "slicing metrics per model, see gcode_metrics.py; "
                                         "regenerate with scripts/update_baseline.py",
                                         "models": dict(sorted(models.items()))}, indent=2) + "\n")
    print(f"wrote {BASELINE_FILE.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
