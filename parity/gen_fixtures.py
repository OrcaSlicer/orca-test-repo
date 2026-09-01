#!/usr/bin/env python3
"""(Re)generate the programmatic parity fixtures under tests/data/parity/.

The generated files are committed so harness runs (and CI) don't depend on
this script; run it again only when a fixture needs to change. Fixtures that
require a slicer binary (project-3mf edits) take --bin.

Fixtures produced:
  20mmbox_offorigin.stl   cube translated far off the plate origin (tests GUI
                          auto-centering vs CLI keeping file coordinates)
  20mmbox_inch.stl        cube authored in inch units (tests unit-conversion
                          paths: GUI dialog vs CLI --convert-unit)
  cube_sunken.3mf         project with the cube half below the bed
  cube_partly_outside.3mf project with the cube half off the bed edge in XY
                          (GUI warns and slices, CLI aborts)
  presets/process_deviant.json  user process preset with `inherits` and no
                          compatible_printers (exercises the CLI compat gate)

Stdlib only.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.abspath(os.path.dirname(__file__))
OUT = os.path.join(HERE, "fixtures")
BASE_STL = os.path.join(OUT, "20mmbox-LF.stl")
SLICER_ROOT = os.environ.get("ORCA_SLICER_ROOT", "")

VERTEX = re.compile(r"^(\s*vertex\s+)([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s*$")


def transform_stl(src, dst, fn):
    with open(src) as f, open(dst, "w") as g:
        for line in f:
            m = VERTEX.match(line)
            if m:
                x, y, z = fn(float(m.group(2)), float(m.group(3)), float(m.group(4)))
                g.write("%s%.6f %.6f %.6f\n" % (m.group(1), x, y, z))
            else:
                g.write(line)


def make_base_project(binpath, workdir, inputs, name="base_project.3mf"):
    """Export an unsliced project 3mf of the given models via the CLI."""
    out = os.path.join(workdir, name)
    profiles = os.path.join(SLICER_ROOT, "resources", "profiles", "BBL")
    datadir = os.path.join(workdir, "datadir")
    os.makedirs(datadir, exist_ok=True)
    subprocess.run(
        [binpath, "--datadir", datadir,
         "--load-settings", "%s;%s" % (
             os.path.join(profiles, "machine", "Bambu Lab P1S 0.4 nozzle.json"),
             os.path.join(profiles, "process", "0.20mm Standard @BBL X1C.json")),
         "--load-filaments",
         os.path.join(profiles, "filament", "Bambu PLA Basic @BBL X1C.json"),
         "--arrange", "1", "--export-3mf", out] + inputs,
        check=True, cwd=workdir, capture_output=True,
    )
    return out


def retransform_project(src_3mf, dst_3mf, dx=0.0, dy=0.0, dz=0.0, only_item=None):
    """Copy a 3mf, offsetting build item translations (all items, or just the
    only_item-th, 0-based)."""

    def patch(xml):
        counter = [0]

        def sub(m):
            idx = counter[0]
            counter[0] += 1
            if only_item is not None and idx != only_item:
                return m.group(0)
            v = m.group(2).split()
            v[9] = "%g" % (float(v[9]) + dx)
            v[10] = "%g" % (float(v[10]) + dy)
            v[11] = "%g" % (float(v[11]) + dz)
            return m.group(1) + " ".join(v) + m.group(3)

        return re.sub(
            r'(<item [^>]*transform=")([^"]+)("[^>]*/>)', sub, xml
        )

    with zipfile.ZipFile(src_3mf) as zin, zipfile.ZipFile(
        dst_3mf, "w", zipfile.ZIP_DEFLATED
    ) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "3D/3dmodel.model":
                data = patch(data.decode()).encode()
            zout.writestr(info, data)


def write_user_presets():
    pdir = os.path.join(OUT, "presets")
    os.makedirs(pdir, exist_ok=True)
    # A user process preset the GUI accepts (inherits resolved via bundle)
    # but whose leaf carries no compatible_printers - the CLI compat gate
    # rejects it (BUG-32 class). Also a BUG-34 probe: its custom values must
    # survive a CLI export -> GUI reopen.
    with open(os.path.join(pdir, "0.20mm Deviant @parity.json"), "w") as f:
        json.dump({
            "type": "process",
            "name": "0.20mm Deviant @parity",
            "from": "User",
            "inherits": "0.20mm Standard @BBL X1C",
            "version": "2.1.0.0",
            "layer_height": "0.24",
            "wall_loops": "3",
            "print_settings_id": "0.20mm Deviant @parity",
        }, f, indent=2)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bin", default=os.environ.get("ORCA_BIN"),
                    help="orca-slicer binary (needed for the project fixtures)")
    args = ap.parse_args()
    if args.bin and not os.path.isdir(os.path.join(SLICER_ROOT, "resources")):
        sys.exit("set ORCA_SLICER_ROOT to an OrcaSlicer checkout for project fixtures")
    os.makedirs(OUT, exist_ok=True)

    transform_stl(BASE_STL, os.path.join(OUT, "20mmbox_offorigin.stl"),
                  lambda x, y, z: (x + 150.0, y + 80.0, z))
    transform_stl(BASE_STL, os.path.join(OUT, "20mmbox_inch.stl"),
                  lambda x, y, z: (x / 25.4, y / 25.4, z / 25.4))
    write_user_presets()
    print("wrote STL + preset fixtures to %s" % OUT)

    if not args.bin or not os.access(args.bin, os.X_OK):
        print("no --bin: skipping project fixtures (cube_sunken, cube_partly_outside)")
        return
    with tempfile.TemporaryDirectory(prefix="parity-gen-") as wd:
        base = make_base_project(args.bin, wd, [BASE_STL])
        retransform_project(base, os.path.join(OUT, "cube_sunken.3mf"), dz=-10.0)
        shutil.copy2(base, os.path.join(OUT, "cube_project.3mf"))
        # two objects; push only the second across the right bed edge so the
        # GUI still has an in-bounds object to slice while the CLI aborts
        base2 = make_base_project(args.bin, wd, [BASE_STL, BASE_STL],
                                  name="base_two.3mf")
        retransform_project(
            base2, os.path.join(OUT, "cube_partly_outside.3mf"),
            dx=130.0, only_item=1,
        )
    print("wrote project fixtures to %s" % OUT)


if __name__ == "__main__":
    main()
