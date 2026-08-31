"""Single source of truth for "which CLI options exist" (the *surface*).

Resolution order:
  1. a live enumeration from an OrcaSlicer source checkout, when one is given
     (`--orca-source PATH`, `$ORCA_SOURCE`, or auto-detected -- see
     run_test.py), so a build under test is always matched with the option
     definitions it was compiled from;
  2. otherwise the committed snapshot cases/_snapshots/cli_surface_full.json.

Everything that needs the surface (type map for the G-code parser, the fuzz
and override sweeps, the GUI-guard's action-flag list) goes through
`options()` so they can never disagree. The committed snapshot's drift from a
live enumeration is caught by this repo's scheduled workflow, which
regenerates it from upstream main and opens a PR with the diff.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SNAPSHOT = REPO_ROOT / "cases" / "_snapshots" / "cli_surface_full.json"
_CACHE: dict = {}


def source_dir() -> Path | None:
    p = os.environ.get("ORCA_SOURCE")
    if p and (Path(p) / "src" / "libslic3r" / "PrintConfig.cpp").exists():
        return Path(p)
    return None


def set_source_dir(path: str | None):
    if path:
        os.environ["ORCA_SOURCE"] = str(path)
    _CACHE.clear()


def enumerate_live(src: Path) -> dict:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from enumerate_cli_options import enumerate_options, key_to_flag, variant_sets  # noqa: E402

    opts, warnings = enumerate_options(src)
    defaults = {"min": None, "max": None, "enum_values": [], "enum_dynamic": False, "default": None, "nullable": False}
    seen, out = set(), []
    for o in opts:
        if o["key"] in seen:
            continue
        seen.add(o["key"])
        out.append({**defaults, **{k: v for k, v in o.items() if k != "class"}, "flag": key_to_flag(o["key"]),
                    "class": o["class"].split(" ")[0]})
    out.sort(key=lambda o: o["key"])
    return {"options": out, "variant_keys": sorted(variant_sets(src)), "warnings": warnings, "origin": str(src)}


def load() -> dict:
    """{'options': [...], 'variant_keys': [...], 'origin': str}"""
    if "surface" in _CACHE:
        return _CACHE["surface"]
    src = source_dir()
    if src is not None:
        data = enumerate_live(src)
    else:
        raw = json.loads(SNAPSHOT.read_text()) if SNAPSHOT.exists() else {"options": [], "variant_keys": []}
        if isinstance(raw, list):  # older snapshot layout: a bare list of options
            raw = {"options": raw, "variant_keys": []}
        data = {"options": raw.get("options", []), "variant_keys": raw.get("variant_keys", []),
                "warnings": [], "origin": str(SNAPSHOT)}
    _CACHE["surface"] = data
    return data


def options() -> list[dict]:
    return load()["options"]


def variant_keys() -> set[str]:
    return set(load()["variant_keys"])


def type_map() -> dict[str, str]:
    return {o["key"]: o["type"] for o in options()}


def action_flags() -> set[str]:
    return {o["flag"] for o in options() if o.get("class") == "CLIActionsConfigDef"} | {"--downward-check"}
