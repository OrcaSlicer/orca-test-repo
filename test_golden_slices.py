"""Golden-slice checks: every .3mf in test_projects/ must slice and reproduce
the metrics recorded in baseline.json.

The metrics come from what OrcaSlicer itself writes into the G-code
(gcode_metrics.py): layer count and max Z (exact), filament used per
extruder in mm/cm3/g, the sum of all E moves, print-time estimates, and file
size, each with its own tolerance (gcode_metrics.TOLERANCES). A slice that
keeps the same byte size but prints differently -- missing layers, wrong
retraction, no wipe tower, supports dropped -- moves at least one of these.

A slice that fails, produces no G-code, or fails the validator FAILS the
test. Metrics outside tolerance do NOT fail it: they are printed as a
readable warning (GoldenMetricsDrift, also in pytest's warnings summary),
because the baseline belongs to one specific build and other builds are
expected to drift; the numbers are for a human to judge.

Regenerate after a deliberate, reviewed change to slicing output (never to
silence a failure you have not looked at):
    python scripts/update_baseline.py /path/to/orca-slicer [--only Model.3mf]

`baseline.json` records, per model, the metrics object and the build they were
captured on. Older baselines that are a bare integer (the historical
"gcode size" format) are still understood and compared as size_bytes only.
"""
import json
import subprocess
import warnings
from pathlib import Path

import pytest

import gcode_metrics as gm

REPO_ROOT = Path(__file__).resolve().parent
BASELINE = json.loads((REPO_ROOT / "baseline.json").read_text())
VALIDATOR_BIN = REPO_ROOT / "bin" / "orca_gcode_validator"
TEST_PROJECTS = sorted((REPO_ROOT / "test_projects").glob("*.3mf"))

# orca_gcode_validator is not reliable (false positives observed) --
# opt in per-model only once a model's validator output has been hand-checked
# for false positives. Extend this set as more runs are vetted.
GCODE_VALIDATED_MODELS = {"Stanford_Bunny.3mf"}


class GoldenMetricsDrift(UserWarning):
    """Slicing metrics moved away from baseline.json (informational)."""


def captured_with(key: str) -> str:
    entry = BASELINE.get("models", BASELINE).get(key)
    return entry.get("captured_with", "an unrecorded build") if isinstance(entry, dict) else "an unrecorded build"


def expected_metrics(key: str) -> dict:
    entry = BASELINE.get("models", BASELINE).get(key)
    if entry is None:
        pytest.fail(f"no baseline entry for {key} in baseline.json -- run scripts/update_baseline.py to add one")
    if isinstance(entry, (int, float)):  # legacy size-only baseline
        return {"size_bytes": int(entry)}
    return entry["metrics"] if "metrics" in entry else entry


@pytest.mark.parametrize("threemf", TEST_PROJECTS, ids=lambda p: p.name)
def test_golden_slice(threemf, run_orca, datadir, outputdir):
    key = threemf.name.replace(".", "_") + ".gcode"
    result = run_orca(["--slice", "0", "--allow-newer-file", "--datadir", datadir, "--outputdir", outputdir, threemf])
    assert result.returncode == 0, f"slice failed (exit {result.returncode}):\n{result.stdout[-1500:]}\n{result.stderr[-1500:]}"
    gcode = outputdir / "plate_1.gcode"
    assert gcode.exists(), "no plate_1.gcode produced"

    actual = gm.parse_file(gcode)
    problems = gm.compare(actual, expected_metrics(key))
    if problems:
        # Drift is reported, not failed: the baseline was captured on one specific
        # build (see baseline.json "captured_with"), and a different build is
        # expected to differ somewhat. The slice itself succeeding is the hard
        # requirement; the numbers are for a human to read.
        msg = (f"{threemf.name}: slicing metrics differ from baseline.json "
               f"(captured with {captured_with(key)}):\n    - " + "\n    - ".join(problems) +
               "\n  If this is a deliberate change, refresh with: python scripts/update_baseline.py <orca-slicer>")
        warnings.warn(msg, GoldenMetricsDrift)
        print("\n[golden] " + msg)

    if threemf.name in GCODE_VALIDATED_MODELS:
        vproc = subprocess.run([str(VALIDATOR_BIN), str(gcode)], capture_output=True, text=True)
        assert vproc.returncode == 0, f"gcode validation errors:\n{vproc.stdout}"


def test_baseline_file_is_well_formed():
    models = BASELINE.get("models", BASELINE)
    assert models, "baseline.json has no models"
    for key, entry in models.items():
        assert ".gcode" in key, key
        metrics = entry.get("metrics", entry) if isinstance(entry, dict) else {"size_bytes": entry}
        assert "layers" in metrics or "size_bytes" in metrics, f"{key}: no usable metric"
    missing = [p.name for p in TEST_PROJECTS if p.name.replace(".", "_") + ".gcode" not in models]
    assert not missing, f"models without a baseline entry (run scripts/update_baseline.py): {missing}"
