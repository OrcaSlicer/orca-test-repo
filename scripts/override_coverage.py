#!/usr/bin/env python3
"""Turn an override-sweep report into a human-readable coverage list.

    python scripts/override_coverage.py <override_report.json> > cases/_snapshots/override_coverage.md

Every option in the CLI surface ends up in exactly one section, so the output
answers "which settings does the suite actually exercise, and why not the rest".
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import surface  # noqa: E402
from test_cli_overrides import SKIP_KEYS  # noqa: E402


def main() -> int:
    report = Path(sys.argv[1]) if len(sys.argv) == 2 else REPO_ROOT / ".pytest_cache" / "override_report.json"
    if not report.exists():
        print(f"no report at {report}; run the sweep first (pytest -m cli_overrides)", file=sys.stderr)
        return 2
    r = json.loads(report.read_text())
    opts = {o["key"]: o for o in surface.options()}

    sections = defaultdict(list)
    for k, o in sorted(opts.items()):
        if o["class"] != "PrintConfigDef":
            sections["CLI-only flags (actions/transforms/misc) -- covered by cases/cli-flags, not by the sweep"].append(k)
        elif o["nocli"]:
            sections["Rejected by the CLI itself (`nocli`) -- not settable from the command line"].append(k)
        elif k in SKIP_KEYS:
            sections["Deliberately not swept (identity/structural/network/derived keys, see SKIP_KEYS in test_cli_overrides.py)"].append(k)
        elif k in r["skipped"]:
            sections[f"Skipped: {r['skipped'][k]}"].append(k)
        elif r["merge"].get(k) == "landed" and r["gcode"].get(k) == "landed":
            sections["Tested: override landed in the merged config AND in the G-code of a slice"].append(f"{k} = {r['probes'][k]}")
        elif r["merge"].get(k) == "landed":
            sections[f"Tested at merge, {r['gcode'].get(k, 'not sliced')} at slice time"].append(f"{k} = {r['probes'][k]}")
        elif k in r["merge"]:
            sections[f"Merge stage: {r['merge'][k]}"].append(f"{k} = {r['probes'][k]}")
        else:
            sections["Not in this report (surface newer than the report?)"].append(k)

    print(f"# Override-sweep coverage\n\nBinary: `{r.get('binary', '?')}` -- {r['invocations']} CLI invocations, "
          f"{len(r['probes'])} options probed, surface origin `{r.get('surface_origin', '?')}`.\n")
    order = sorted(sections, key=lambda s: (not s.startswith("Tested"), s))
    for s in order:
        items = sections[s]
        print(f"## {s} ({len(items)})\n")
        print(", ".join(f"`{i}`" for i in items) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
