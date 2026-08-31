#!/usr/bin/env python3
"""Enumerate the CLI-accepted config option surface directly from OrcaSlicer's
source, instead of relying on --help (which documents only a small subset --
see TESTING_STRATEGY.md).

CLI-accepted options come from exactly 4 ConfigDef classes in
src/libslic3r/PrintConfig.cpp, merged into DynamicPrintAndCLIConfig:
PrintConfigDef (the ~890 print/printer/filament settings) plus
CLIActionsConfigDef/CLITransformConfigDef/CLIMiscConfigDef (the CLI-only
flags). Everything after CLIMiscConfigDef in that file (ReadOnlySlicing-
StatesConfigDef and friends) is G-code placeholder-parser variables like
{zhop} -- readable in custom G-code templates, NOT CLI flags -- and must be
excluded, which is why this walks class boundaries rather than grepping the
whole file.

Each option definition is a `this->add("key", coType)` call, standard C++
member-init-list style, one line, inside one of the 4 target classes. A
handful of options are added in a loop with a computed key (e.g.
"machine_max_speed_" + axis.name for x/y/z/e) -- these can't be recovered by
this line-oriented scan and are listed in MANUAL_OVERRIDES below, resolved
once by hand; anything else that looks like a this->add( call inside a
target class but doesn't match the literal-string pattern is reported as a
warning rather than silently dropped.

Usage:
    python scripts/enumerate_cli_options.py --orca-source /path/to/OrcaSlicer
        > cases/_snapshots/cli_surface_full.json
"""
import argparse
import json
import re
import sys
from pathlib import Path

TARGET_CLASSES = {"PrintConfigDef", "CLIActionsConfigDef", "CLITransformConfigDef", "CLIMiscConfigDef"}

CTOR_RE = re.compile(r"^(\w+)::\1\(\)$")
ADD_LITERAL_RE = re.compile(r'this->add\("([A-Za-z0-9_]+)",\s*(co[A-Za-z0-9]+)\)')
ADD_ANY_RE = re.compile(r"this->add\(")
NOCLI_RE = re.compile(r"^\s*def->cli\s*=\s*ConfigOptionDef::nocli\s*;")
# Per-option metadata the override sweep (test_cli_overrides.py) needs to pick a
# valid, distinctive probe value: numeric range, enum keys, default, nullability.
MIN_RE = re.compile(r"^\s*def->min\s*=\s*([-+0-9.eE]+)\s*;")
MAX_RE = re.compile(r"^\s*def->max\s*=\s*([-+0-9.eE]+)\s*;")
ENUM_RE = re.compile(r'^\s*def->enum_values\.(?:push_back|emplace_back)\((?:L\()?"([^"]*)"\)?\)')
ALIAS_ON_ADD_RE = re.compile(r'^\s*(?:auto|const ConfigOptionDef\s*\*|ConfigOptionDef\s*\*)\s*(\w+)\s*=\s*def\s*=\s*this->add\("([A-Za-z0-9_]+)"')
ENUM_LIST_RE = re.compile(r"^\s*def->enum_values\s*=\s*\{(.*)\}\s*;")            # brace-list assignment
ENUM_COPY_RE = re.compile(r"^\s*def->enum_values\s*=\s*(\w+)->enum_values\s*;")   # copied from another def
ENUM_DYNAMIC_RE = re.compile(r"^\s*def->enum_values\.(?:push_back|emplace_back)\((?!\")")
DEF_ALIAS_RE = re.compile(r'^\s*(?:auto|const ConfigOptionDef\s*\*|ConfigOptionDef\s*\*)\s*(\w+)\s*=\s*this->get\("([A-Za-z0-9_]+)"\)')
DEF_ALIAS_SELF_RE = re.compile(r"^\s*(?:auto|const ConfigOptionDef\s*\*|ConfigOptionDef\s*\*)\s*(\w+)\s*=\s*def\s*;")
DEFAULT_RE = re.compile(r"^\s*def->set_default_value\(new\s+ConfigOption\w+\s*[({](.*)[)}]\s*\)\s*;")
NULLABLE_RE = re.compile(r"^\s*def->nullable\s*=\s*true\s*;")

# Options added via a computed key, not recoverable from a single line --
# resolved once by hand from their source (re-check after any upstream edit
# near these loops; the enumerator's warning output will flag if the pattern
# changed shape).
MANUAL_OVERRIDES = [
    # PrintConfig.cpp:5011-5047, looped over X/Y/Z/E axes
    *({"key": f"machine_max_{metric}_{axis}", "type": "coFloats", "nocli": False}
      for metric in ("speed", "acceleration", "jerk")
      for axis in ("x", "y", "z", "e")),
    # PrintConfig.cpp:1093, looped over {"print","printer","filament"}_plugin_config_overrides
    *({"key": f"{preset}_plugin_config_overrides", "type": "coString", "nocli": False}
      for preset in ("print", "printer", "filament")),
]


def key_to_flag(key: str) -> str:
    return "--" + key.replace("_", "-")


VARIANT_SET_RE = re.compile(r"std::set<std::string>\s+(\w+_options_with_variant\w*)\s*=\s*\{(.*?)\};", re.S)


def variant_sets(source: Path) -> set[str]:
    """Keys in libslic3r's `*_options_with_variant` sets: options whose merged
    config is expanded per extruder variant (see settings_compare.VARIANT_KEYS)."""
    text = (source / "src" / "libslic3r" / "PrintConfig.cpp").read_text()
    text = re.sub(r"//.*$", "", text, flags=re.M)
    keys = set()
    for m in VARIANT_SET_RE.finditer(text):
        keys |= set(re.findall(r'"([A-Za-z0-9_]+)"', m.group(2)))
    return keys


def enumerate_options(source: Path):
    text = (source / "src" / "libslic3r" / "PrintConfig.cpp").read_text()
    # Several CLI flags are defined inside /* ... */ blocks (center, copy, split,
    # scale_to_fit, export_gcode, ...): they are NOT accepted by the binary and
    # must not be enumerated. Blank out block comments (keeping line count) and
    # strip // comments before scanning.
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    lines = [re.sub(r"//.*$", "", ln) for ln in text.splitlines()]

    options = []
    warnings = []
    aliases = {}  # local variable name -> option key, for `auto def_x = this->get("x")`
    current_class = None
    pending = None  # the option dict currently accumulating its nocli flag

    for lineno, line in enumerate(lines, start=1):
        ctor = CTOR_RE.match(line.strip())
        if ctor:
            current_class = ctor.group(1)
            pending = None
            continue

        if current_class not in TARGET_CLASSES:
            continue

        if (ma := DEF_ALIAS_RE.match(line)):
            aliases[ma.group(1)] = ma.group(2)
            continue
        if pending is not None and (ma := DEF_ALIAS_SELF_RE.match(line)):
            aliases[ma.group(1)] = pending["key"]  # `auto def_x = def;` right after this->add("x", ...)
            continue

        m = ADD_LITERAL_RE.search(line)
        if m:
            if (ma := ALIAS_ON_ADD_RE.match(line)):
                aliases[ma.group(1)] = ma.group(2)  # `auto def_x = def = this->add("x", ...)`
            pending = {"key": m.group(1), "type": m.group(2), "nocli": False, "class": current_class,
                       "min": None, "max": None, "enum_values": [], "enum_dynamic": False,
                       "default": None, "nullable": False}
            options.append(pending)
            continue

        if ADD_ANY_RE.search(line):
            warnings.append(f"{lineno}: this->add( with a non-literal key, not auto-resolved: {line.strip()}")
            pending = None
            continue

        if pending is None:
            continue
        if NOCLI_RE.match(line):
            pending["nocli"] = True
        elif (mm := MIN_RE.match(line)):
            pending["min"] = float(mm.group(1))
        elif (mm := MAX_RE.match(line)):
            pending["max"] = float(mm.group(1))
        elif (mm := ENUM_RE.match(line)):
            pending["enum_values"].append(mm.group(1))
        elif (mm := ENUM_LIST_RE.match(line)):
            pending["enum_values"].extend(re.findall(r'"([^"]*)"', mm.group(1)))
        elif (mm := ENUM_COPY_RE.match(line)):
            # `def->enum_values = def_top_fill_pattern->enum_values;` -- resolve the alias
            src_key = aliases.get(mm.group(1))
            src_opt = next((o for o in options if o["key"] == src_key), None) if src_key else None
            if src_opt:
                pending["enum_values"] = list(src_opt["enum_values"])
            else:
                pending["enum_dynamic"] = True
        elif ENUM_DYNAMIC_RE.match(line):
            pending["enum_dynamic"] = True
        elif (mm := DEFAULT_RE.match(line)):
            pending["default"] = mm.group(1).strip()
        elif NULLABLE_RE.match(line):
            pending["nullable"] = True

    for override in MANUAL_OVERRIDES:
        options.append({**override, "class": "PrintConfigDef (manual override)"})

    # Per-filament overrides of printer options ("filament_retraction_length" etc.)
    # are registered in a loop over `filament_extruder_override_keys` with
    # `this->add_nullable(key, base.type)`, copying type/min/max/enum values from
    # the base printer option (key minus the "filament_" prefix).
    m = re.search(r"filament_extruder_override_keys\s*=\s*\{(.*?)\};", text, re.S)
    if m:
        by_key = {o["key"]: o for o in options}
        for key in re.findall(r'"([A-Za-z0-9_]+)"', m.group(1)):
            base = by_key.get(key[len("filament_"):]) if key.startswith("filament_") else None
            if base is None:
                warnings.append(f"filament override key {key!r}: base option not found, skipped")
                continue
            if key in by_key:
                continue
            options.append({"key": key, "type": base["type"], "nocli": False, "class": "PrintConfigDef (filament override)",
                            "min": base.get("min"), "max": base.get("max"), "enum_values": list(base.get("enum_values", [])),
                            "enum_dynamic": base.get("enum_dynamic", False), "default": None, "nullable": True})

    return options, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--orca-source", required=True, type=Path, help="path to an OrcaSlicer checkout")
    args = parser.parse_args()

    if not (args.orca_source / "src" / "libslic3r" / "PrintConfig.cpp").exists():
        print(f"ERROR: {args.orca_source} doesn't look like an OrcaSlicer checkout "
              f"(missing src/libslic3r/PrintConfig.cpp)", file=sys.stderr)
        return 1

    options, warnings = enumerate_options(args.orca_source)

    seen = set()
    deduped = []
    for opt in options:
        if opt["key"] in seen:
            continue
        seen.add(opt["key"])
        deduped.append({
            "key": opt["key"],
            "flag": key_to_flag(opt["key"]),
            "type": opt["type"],
            "nocli": opt["nocli"],
            "class": opt["class"].split(" ")[0],
            "min": opt.get("min"),
            "max": opt.get("max"),
            "enum_values": opt.get("enum_values", []),
            "enum_dynamic": opt.get("enum_dynamic", False),
            "default": opt.get("default"),
            "nullable": opt.get("nullable", False),
        })
    deduped.sort(key=lambda o: o["key"])

    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    print(f"enumerated {len(deduped)} options ({sum(1 for o in deduped if o['nocli'])} nocli, "
          f"{len(warnings)} unresolved dynamic-key lines)", file=sys.stderr)

    print(json.dumps({"options": deduped, "variant_keys": sorted(variant_sets(args.orca_source))}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
