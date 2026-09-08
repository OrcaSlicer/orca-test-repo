"""Make a user preset self-contained before handing it to the CLI.

`--load-settings` / `--load-filaments` resolve a preset against the presets
already loaded in the datadir's bundle; a file that is not one of them is
refused ("Preset was not found in the loaded bundle"). A *self-contained*
preset -- every key explicit, no `inherits` -- is accepted from anywhere,
and that is the supported way to hand the CLI a preset it has never seen.

The fixtures under test_projects/settings/ are authored as thin presets that
inherit from a shipped profile, because that is what they are testing: which
of the user's keys survive import. They are flattened here, against the
system profiles in the datadir under test, so the file the CLI receives is
self-contained while the fixture stays readable and stays matched to the
profiles the binary ships.

The flattened copy is marked `from: system` -- see flatten() for why that is
required rather than cosmetic.
"""
from __future__ import annotations

import json
from pathlib import Path

# Bookkeeping that identifies the preset rather than describing the print:
# the leaf's values win and a parent's must not leak in.
LEAF_ONLY = {"name", "from", "setting_id", "filament_id", "instantiation",
             "version", "is_custom", "url", "inherits"}

KIND_DIR = {"machine": "machine", "process": "process", "filament": "filament"}


def _vendor_dirs(datadir: Path) -> list[Path]:
    system = datadir / "system"
    return sorted(p for p in system.iterdir() if p.is_dir()) if system.is_dir() else []


def _find(datadir: Path, kind: str, name: str) -> Path | None:
    for vendor in _vendor_dirs(datadir):
        p = vendor / KIND_DIR[kind] / f"{name}.json"
        if p.is_file():
            return p
    return None


def flatten(leaf_path: Path, datadir: Path) -> dict:
    """Merge a preset's `inherits` chain into one self-contained config."""
    leaf = json.loads(Path(leaf_path).read_text())
    kind = leaf.get("type")
    if kind not in KIND_DIR:
        raise ValueError(f"{leaf_path}: preset has no machine/process/filament type")
    chain, seen, name = [leaf], set(), leaf.get("inherits", "")
    while name:
        if name in seen:
            raise ValueError(f"{leaf_path}: inherits cycle at {name!r}")
        seen.add(name)
        parent = _find(datadir, kind, name)
        if parent is None:
            raise ValueError(f"{leaf_path}: cannot resolve {kind} preset {name!r} in {datadir}")
        d = json.loads(parent.read_text())
        chain.append(d)
        name = d.get("inherits", "")

    merged: dict = {}
    for d in reversed(chain):
        for k, v in d.items():
            if k in LEAF_ONLY and d is not leaf:
                continue
            merged[k] = v
    merged.pop("inherits", None)
    # A flattened preset is its own root, so it has to say so. The CLI takes a
    # preset's *system identity* from its own name when `from` is "system" and
    # from `inherits` otherwise (OrcaSlicer.cpp, where new_printer_system_name
    # is assigned); flattening removes `inherits`, so a preset left marked
    # "User" would have no system identity at all and nothing could declare
    # itself compatible with it.
    merged["from"] = "system"
    return merged


def flatten_to(leaf_path: Path, datadir: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / Path(leaf_path).name
    out.write_text(json.dumps(flatten(leaf_path, datadir), indent=2))
    return out
