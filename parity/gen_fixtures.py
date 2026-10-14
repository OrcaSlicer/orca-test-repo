#!/usr/bin/env python3
"""(Re)generate the programmatic parity fixtures under parity/fixtures/.

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
                          (GUI disables slicing, CLI aborts)
  torture_rot30.3mf       the torture plate as a project whose instance is
                          rotated 30 deg about Z -- the only way the CLI keeps
                          an instance rotation (--rotate bakes it into the
                          mesh), which align_infill_direction_to_model reads
  presets/0.20mm Deviant @parity.json  user process preset with `inherits` and no
                          compatible_printers (exercises the CLI compat gate)

Stdlib only.
"""

import argparse
import json
import math
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


def make_base_project(binpath, workdir, inputs, name="base_project.3mf",
                      machine="Bambu Lab P1S 0.4 nozzle",
                      filament="Bambu PLA Basic @BBL P1S 0.4 nozzle"):
    """Export an unsliced project 3mf of the given models via the CLI."""
    out = os.path.join(workdir, name)
    profiles = os.path.join(SLICER_ROOT, "resources", "profiles", "BBL")
    datadir = os.path.join(workdir, "datadir")
    os.makedirs(datadir, exist_ok=True)
    subprocess.run(
        [binpath, "--datadir", datadir,
         "--load-settings", "%s;%s" % (
             os.path.join(profiles, "machine", machine + ".json"),
             os.path.join(profiles, "process", "0.20mm Standard @BBL X1C.json")),
         "--load-filaments",
         os.path.join(profiles, "filament", filament + ".json"),
         "--arrange", "1", "--export-3mf", out] + inputs,
        check=True, cwd=workdir, capture_output=True,
    )
    return out


def patch_project_model(src_3mf, dst_3mf, patch):
    """Copy a 3mf with its build file (3D/3dmodel.model) passed through patch."""
    with zipfile.ZipFile(src_3mf) as zin, zipfile.ZipFile(
        dst_3mf, "w", zipfile.ZIP_DEFLATED
    ) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "3D/3dmodel.model":
                data = patch(data.decode()).encode()
            zout.writestr(info, data)


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

    patch_project_model(src_3mf, dst_3mf, patch)


def _mat3(v):
    return [v[0:3], v[3:6], v[6:9]]


def _mul3(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _row_mul(v, m):
    return [sum(v[k] * m[k][j] for k in range(3)) for j in range(3)]


def rotate_project_item(src_3mf, dst_3mf, degrees):
    """Copy a 3mf, rotating its single build item about Z while leaving the
    object where arrange put it. 3mf transforms are row-major with the
    translation last and apply as p * M + t, so the mesh origin sits at
    component_t * item_M + item_t; the new item translation keeps that point
    fixed under the rotated item matrix."""

    def patch(xml):
        items = re.findall(r'<item [^>]*transform="([^"]+)"', xml)
        comps = re.findall(r'<component [^>]*transform="([^"]+)"', xml)
        if len(items) != 1 or len(comps) > 1:
            sys.exit("%s: expected one build item with at most one component" % src_3mf)
        iv = [float(x) for x in items[0].split()]
        comp_t = [float(x) for x in comps[0].split()][9:12] if comps else [0.0, 0.0, 0.0]
        a = math.radians(degrees)
        rot = [[math.cos(a), math.sin(a), 0.0], [-math.sin(a), math.cos(a), 0.0], [0.0, 0.0, 1.0]]
        item_m = _mat3(iv)
        pivot = [p + t for p, t in zip(_row_mul(comp_t, item_m), iv[9:12])]
        new_m = _mul3(item_m, rot)
        new_t = [p - q for p, q in zip(pivot, _row_mul(comp_t, new_m))]
        transform = " ".join("%.6f" % x for x in sum(new_m, []) + new_t)
        return re.sub(r'(<item [^>]*transform=")[^"]+(")',
                      lambda m: m.group(1) + transform + m.group(2), xml)

    patch_project_model(src_3mf, dst_3mf, patch)


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
        # the effect stage slices this one with the X1C presets, so export it
        # with them too
        plate = make_base_project(args.bin, wd, [os.path.join(OUT, "torture.stl")],
                                  name="torture.3mf", machine="Bambu Lab X1 Carbon 0.4 nozzle",
                                  filament="Bambu PLA Basic @BBL X1C")
        rotate_project_item(plate, os.path.join(OUT, "torture_rot30.3mf"), 30.0)
    print("wrote project fixtures to %s" % OUT)


if __name__ == "__main__":
    main()
