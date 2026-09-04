#!/usr/bin/env python3
"""Flatten the Custom-vendor Klipper/Marlin/toolchanger presets for CLI use.

The CLI reads one preset file and never walks `inherits`, and it takes
`compatible_printers` from the leaf alone. `0.20mm Standard @MyMarlin` keeps
that list in its parent (fdm_process_marlin_common), so handed to the CLI raw
it fails the compat gate with "The selected printer is not compatible with the
process preset in the 3mf" (rc 239); the Klipper leaf loses most of its parent
keys and dies later with "incorrect slicing parameters" (rc 205). Flattened,
both slice clean.

  python3 parity/make_custom_presets.py --seed <datadir seed> --out <dir>
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from flatten_preset import flatten  # noqa: E402

WANTED = [
    ("machine", "MyKlipper 0.4 nozzle"), ("process", "0.20mm Standard @MyKlipper"),
    ("machine", "MyMarlin 0.4 nozzle"),  ("process", "0.20mm Standard @MyMarlin"),
    ("machine", "MyToolChanger 0.4 nozzle"), ("process", "0.20mm Standard @MyToolChanger"),
    ("filament", "Generic PLA @System"), ("filament", "Generic PLA-CF @System"),
    ("filament", "Generic PLA @MyToolChanger"), ("filament", "Generic PETG @MyToolChanger"),
]
# NOTE: filaments whose chain reaches OrcaFilamentLibrary/filament/base/ (e.g.
# "Generic PLA Silk @System" -> fdm_filament_pla_silk) cannot be flattened:
# flatten_preset.find() only looks in <vendor>/<kind>/, not in base/.

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", required=True, help="datadir seed from parity/make_seed.py")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    profiles = Path(a.seed) / "system"
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    for kind, name in WANTED:
        merged, chain = flatten(str(profiles), ["Custom", "OrcaFilamentLibrary", "BBL"], kind, name)
        (out / f"{name}.json").write_text(json.dumps(merged, indent=1))
        print(f"{kind}: {name} <- {' <- '.join(chain[1:]) or '(leaf only)'}")

if __name__ == "__main__":
    main()
