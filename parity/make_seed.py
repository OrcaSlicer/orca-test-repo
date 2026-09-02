#!/usr/bin/env python3
"""Generate a self-contained OrcaSlicer datadir seed for headless parity runs.

The GUI needs a datadir containing (the CLI does not resolve `inherits` from
it — see flatten_preset.py):
  OrcaSlicer.conf   - app config: first-run wizard marked done, printer model
                      installed, machine/filament/process presets selected
  system/<Vendor>/  - vendor profile bundle (copy of resources/profiles/<Vendor>)
  user/default/     - user preset folders (may be empty)

Two modes:
  --mode resources (default): build everything from the repo's
      resources/profiles tree - fully portable, no personal data, CI-safe.
  --mode home: clone an existing ~/.config/OrcaSlicer (minus plugins/log/cache)
      - reproduces a real user environment, e.g. for user-preset fixtures.

Usage:
  make_seed.py --out /path/to/seed \
      --machine "Bambu Lab P1S 0.4 nozzle" \
      --process "0.20mm Standard @BBL X1C" \
      --filament "Bambu PLA Basic @BBL X1C"

Stdlib only.
"""

import argparse
import json
import os
import shutil
import sys


def repo_root():
    root = os.environ.get("ORCA_SLICER_ROOT", "")
    return root  # validated in main(); the runner always passes --repo


def system_cert_store():
    for p in (
        "/etc/ssl/certs/ca-certificates.crt",   # Debian/Ubuntu
        "/etc/pki/tls/certs/ca-bundle.crt",     # Fedora/RHEL
        "/etc/ssl/ca-bundle.pem",               # openSUSE
        "/etc/ssl/cert.pem",                    # Alpine/macOS-ish
    ):
        if os.path.isfile(p):
            return p
    return ""


def machine_to_model(machine_name):
    """'Bambu Lab P1S 0.4 nozzle' -> ('Bambu Lab P1S', '0.4')."""
    parts = machine_name.rsplit(" nozzle", 1)[0].rsplit(" ", 1)
    if len(parts) == 2 and parts[1].replace(".", "").isdigit():
        return parts[0], parts[1]
    return machine_name, "0.4"


def copy_vendor(profiles_dir, vendor, system_dir):
    src_json = os.path.join(profiles_dir, vendor + ".json")
    src_dir = os.path.join(profiles_dir, vendor)
    if not os.path.isfile(src_json) or not os.path.isdir(src_dir):
        sys.exit("vendor %r not found under %s" % (vendor, profiles_dir))
    shutil.copy2(src_json, os.path.join(system_dir, vendor + ".json"))
    shutil.copytree(
        src_dir,
        os.path.join(system_dir, vendor),
        ignore=shutil.ignore_patterns("*.png", "*.jpg", "*.svg", "*.stl"),
        dirs_exist_ok=True,
    )


def build_from_resources(args):
    profiles = os.path.join(args.repo, "resources", "profiles")
    system_dir = os.path.join(args.out, "system")
    os.makedirs(system_dir, exist_ok=True)
    vendors = list(dict.fromkeys(args.vendor))
    # filament profiles commonly inherit from the shared library vendor
    if "OrcaFilamentLibrary" not in vendors and os.path.isdir(
        os.path.join(profiles, "OrcaFilamentLibrary")
    ):
        vendors.append("OrcaFilamentLibrary")
    for v in vendors:
        copy_vendor(profiles, v, system_dir)

    # printer model metadata (model jsons, bed accessories); harmless if absent
    printers_src = os.path.join(args.repo, "resources", "printers")
    if os.path.isdir(printers_src):
        shutil.copytree(printers_src, os.path.join(args.out, "printers"),
                        dirs_exist_ok=True)

    for sub in ("machine", "process", "filament"):
        os.makedirs(os.path.join(args.out, "user", "default", sub), exist_ok=True)

    model, nozzle = machine_to_model(args.machine)
    conf = {
        "firstguide": {"finish": True},
        "models": [
            {"model": model, "nozzle_diameter": nozzle, "vendor": args.vendor[0]}
        ],
        "presets": {"machine": args.machine},
        "orca_presets": [
            {
                # key names from Preset.hpp: PRESET_PRINT_NAME is "process"
                "machine": args.machine,
                "process": args.process,
                "filament": args.filament,
                "curr_bed_type": args.bed_type,
            }
        ],
        "app": {
            "check_stable_update_only": "true",
            "dark_color_mode": "0",
            "stealth_mode": "true",
            # pre-accept the system TLS cert store, otherwise the GUI blocks on
            # a first-run confirmation dialog (GUI_App.cpp: tls_cert_store_accepted)
            "tls_cert_store_accepted": "yes",
            "tls_accepted_cert_store_location": system_cert_store(),
        },
    }
    with open(os.path.join(args.out, "OrcaSlicer.conf"), "w") as f:
        json.dump(conf, f, indent=1)


def build_from_home(args):
    src = os.path.expanduser(args.home)
    if not os.path.isdir(src):
        sys.exit("no datadir at %s" % src)
    shutil.copytree(
        src,
        args.out,
        ignore=shutil.ignore_patterns("plugins", "log", "cache"),
        dirs_exist_ok=True,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="seed directory to create")
    ap.add_argument("--mode", choices=("resources", "home"), default="resources")
    ap.add_argument("--repo", default=repo_root(),
                    help="OrcaSlicer checkout providing resources/profiles "
                         "(env ORCA_SLICER_ROOT)")
    ap.add_argument("--home", default="~/.config/OrcaSlicer", help="source for --mode home")
    ap.add_argument("--vendor", action="append", default=None,
                    help="vendor bundle(s) to install (default: BBL)")
    ap.add_argument("--machine", default="Bambu Lab P1S 0.4 nozzle")
    ap.add_argument("--process", default="0.20mm Standard @BBL X1C")
    ap.add_argument("--filament", default="Bambu PLA Basic @BBL X1C")
    ap.add_argument("--bed-type", default="1", help="curr_bed_type index")
    ap.add_argument("--force", action="store_true", help="replace existing seed dir")
    args = ap.parse_args()
    if args.vendor is None:
        args.vendor = ["BBL"]
    if args.mode == "resources" and not os.path.isdir(
        os.path.join(args.repo or "", "resources", "profiles")
    ):
        sys.exit("--repo (or ORCA_SLICER_ROOT) must contain resources/profiles")

    if os.path.exists(args.out):
        if not args.force:
            sys.exit("%s exists (use --force to replace)" % args.out)
        shutil.rmtree(args.out)
    os.makedirs(args.out)

    if args.mode == "resources":
        build_from_resources(args)
    else:
        build_from_home(args)
    print("seed ready: %s (%s mode)" % (args.out, args.mode))


if __name__ == "__main__":
    main()
