"""Override sweep: every config option set on the command line must land.

For each of the ~850 print/printer/filament options the CLI accepts as
`--<option>=<value>` (enumerated from OrcaSlicer's source into
cases/_snapshots/cli_surface_full.json, with min/max/enum metadata), pick a
*valid* value that differs from the merged baseline, pass it on the command
line and verify it shows up:

  stage "merge"  -- in the merged config (`--export-settings`), on a 3mf
                    project with system machine/process/filament presets
                    loaded, so a landed value proves the precedence
                    CLI override > --load-settings preset > 3mf embedded
                    for that key; fast (no slicing).
  stage "gcode"  -- in the G-code CONFIG_BLOCK of an actual slice (STL +
                    the same presets), i.e. the config the slicer applied.

Options are sent in batches; a batch that fails is bisected down to the
single option(s) responsible. Every option ends up in exactly one bucket:

  landed        value present as given                           -> OK
  rejected      CLI exited non-zero with an error, value absent   -> allowed,
                reported (validation is a legitimate outcome for a probe
                value the slicer refuses)
  silent_drop   exit 0 but the value is not the one given         -> FAILURE
  crash         killed by a signal                                -> FAILURE

The report for a run is written to <outputdir>/override_report.json and the
rejected list is printed, so a new rejection is visible even though it does
not fail the run. Options that are structural/identity (bed shape, preset
names, ...) or whose value cannot be derived (dynamic enums, keys absent from
the baseline) are skipped and listed as such.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

import settings_compare as sc

REPO_ROOT = Path(__file__).resolve().parent
SURFACE = REPO_ROOT / "cases" / "_snapshots" / "cli_surface_full.json"
BATCH = 40

SYS_MACHINE = "system/BBL/machine/Bambu Lab X1 Carbon 0.4 nozzle.json"
SYS_PROCESS = "system/BBL/process/0.20mm Standard @BBL X1C.json"
SYS_FILAMENT = "system/BBL/filament/Bambu PLA Basic @BBL X1C.json"

# Not probed: changing these re-targets the printer/preset identity or the bed
# geometry (and would invalidate every other option's baseline), or they are
# bookkeeping rather than settings.
SKIP_KEYS = {
    "printable_area", "printable_height", "bed_exclude_area", "wrapping_exclude_area", "extruder_printable_area",
    "extruder_printable_height", "printer_technology", "printer_model", "printer_variant", "printer_settings_id",
    "print_settings_id", "filament_settings_id", "filament_ids", "inherits", "inherits_group", "name", "from",
    "version", "different_settings_to_system", "nozzle_diameter", "extruder_type", "nozzle_volume_type",
    "extruder_variant_list", "printer_extruder_variant", "print_extruder_variant", "filament_extruder_variant",
    "printer_extruder_id", "print_extruder_id", "filament_extruder_id", "filament_map", "filament_map_2",
    "filament_map_mode", "physical_extruder_map", "master_extruder_id", "print_host", "printhost_apikey",
    "printhost_cafile", "printhost_port", "printhost_user", "printhost_password", "printhost_authorization_type",
    "host_type", "bbl_use_printhost", "print_plugin_config_overrides", "printer_plugin_config_overrides",
    "filament_plugin_config_overrides", "thumbnails", "thumbnails_format", "gcode_flavor", "post_process",
    "extruder_clearance_height_to_lid", "extruder_clearance_height_to_rod", "extruder_clearance_radius",
    "printer_structure", "z_offset", "wipe_tower_x", "wipe_tower_y", "flush_volumes_matrix", "flush_volumes_vector",
    "flush_multiplier", "flush_multiplier_fast", "filament_multi_colour", "filament_colour", "filament_is_mixed",
    "extruder_ams_count", "extruder_colour", "start_end_points", "curr_bed_type", "support_multi_bed_types",
    # normalized by the CLI itself for a single-filament project (forced off), so a "1" can never land
    "enable_prime_tower",
    # flipping it removes the very CONFIG_BLOCK the gcode stage reads (covered by its own case instead)
    "gcode_skip_config_block",
    # per-filament nozzle/volume assignment maps are recomputed at apply time on newer builds
    # (see settings_compare.KNOWN_DIVERGENT); a CLI value is never what the G-code records
    "filament_nozzle_map", "filament_volume_map",
    # structured numeric model data stored as strings; a `probe_<key>` text is not a valid value and is
    # (reasonably) discarded -- needs a format-aware probe before it can be swept
    "small_area_infill_flow_compensation_model", "volumetric_speed_coefficients",
}

NUMERIC_VECTOR_TYPES = {"coFloats", "coInts", "coPercents", "coFloatsOrPercents"}


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:.4g}"


def _numeric_probe(cur: str, lo, hi, integer: bool) -> str | None:
    try:
        c = float(cur)
    except ValueError:
        return None
    lo = -1e9 if lo is None else lo
    hi = 1e9 if hi is None else hi
    cands = [c * 1.5 + (1 if c == 0 else 0), c + 1, c / 2, c - 1, lo + (hi - lo) / 3]
    for v in cands:
        if integer:
            v = round(v)
        if lo <= v <= hi and abs(v - c) > 1e-9:
            return _fmt(v)
    return None


def choose_value(meta: dict, current) -> tuple[str | None, str]:
    """Return (cli_value, expected) or (None, reason) if no valid probe exists."""
    t, lo, hi = meta["type"], meta["min"], meta["max"]
    enums = meta["enum_values"]
    cur_list = current if isinstance(current, list) else None
    cur = current[0] if cur_list else current

    def scalar(cur_s):
        if t in ("coFloat", "coInt"):
            return _numeric_probe(cur_s, lo, hi, t == "coInt")
        if t in ("coFloats", "coInts"):
            return _numeric_probe(cur_s, lo, hi, t == "coInts")
        if t in ("coPercent", "coPercents"):
            # percent options can legitimately exceed 100% (e.g. wipe_tower_extra_spacing 100-300%)
            v = _numeric_probe(cur_s.rstrip("%"), lo, hi, False)
            return None if v is None else v + "%"
        if t in ("coPoint", "coPoints"):
            # a point is "x,y" (scalar) or "XxY" (vector element) in the merged JSON; shift both by 1
            try:
                x, y = [float(p) for p in re.split(r"[x,]", cur_s)]
            except ValueError:
                return None
            return f"{_fmt(x + 1)},{_fmt(y + 1)}" if t == "coPoint" else f"{_fmt(x + 1)}x{_fmt(y + 1)}"
        if t in ("coFloatOrPercent", "coFloatsOrPercents"):
            if cur_s.endswith("%"):
                v = _numeric_probe(cur_s.rstrip("%"), lo, hi, False)
                return None if v is None else v + "%"
            return _numeric_probe(cur_s, lo, hi, False)
        if t in ("coBool", "coBools"):
            return "0" if cur_s in ("1", "true") else "1"
        if t in ("coEnum", "coEnums"):
            if not enums:
                return None
            others = [e for e in enums if e != cur_s]
            return others[0] if others else None
        if t == "coString":
            return f"probe_{meta['key']}"
        if t == "coStrings":
            return f"probe_{meta['key']}"
        return None

    if cur_list is not None:
        if not cur_list:
            return None, "empty vector in baseline"
        if any(x == "nil" for x in cur_list):
            return None, "nullable/nil element"
        vals = [scalar(x) for x in cur_list]
        if any(v is None for v in vals):
            return None, "no valid probe for an element"
        return ",".join(vals), ",".join(vals)
    v = scalar(cur)
    if v is None:
        return None, "no valid probe value"
    return v, v


class Sweep:
    def __init__(self, orca_bin: Path, datadir: Path, workdir: Path):
        self.bin, self.datadir, self.workdir = orca_bin, datadir, workdir
        self.n = 0

    def run(self, args, timeout=180):
        """Returns (returncode, cwd); returncode is None when the CLI hung past
        `timeout` seconds (a pathological override can send the slicer into an
        endless loop -- that is a finding, not a reason to abort the sweep)."""
        self.n += 1
        cwd = self.workdir / f"run{self.n:04d}"
        cwd.mkdir(parents=True)
        from conftest import assert_has_cli_action, headless_env

        assert_has_cli_action(args)
        try:
            proc = subprocess.run([str(self.bin)] + [str(a) for a in args], cwd=cwd,
                                  capture_output=True, text=True, timeout=timeout, env=headless_env())
        except subprocess.TimeoutExpired:
            return None, cwd
        return proc.returncode, cwd

    def base_args(self, outdir):
        return ["--datadir", self.datadir, "--outputdir", outdir, "--allow-newer-file",
                "--load-settings", f"{self.datadir / SYS_MACHINE};{self.datadir / SYS_PROCESS}",
                "--load-filaments", self.datadir / SYS_FILAMENT]


def _bisect(sweep, keys, probes, runner):
    """runner(keys) -> (ok: bool, failure: 'crash'|'hang'|'rejected'|None, landed: dict key->bool).
    Returns dict key -> bucket."""
    out = {}
    ok, failure, landed = runner(keys)
    if ok:
        for k in keys:
            out[k] = "landed" if landed.get(k) else "silent_drop"
        return out
    if len(keys) == 1:
        out[keys[0]] = failure
        return out
    mid = len(keys) // 2
    out.update(_bisect(sweep, keys[:mid], probes, runner))
    out.update(_bisect(sweep, keys[mid:], probes, runner))
    return out


@pytest.fixture(scope="session")
def override_results(orca_bin, seeded_data_dir, tmp_path_factory):
    work = tmp_path_factory.mktemp("overrides")
    sweep = Sweep(orca_bin, seeded_data_dir, work)
    import surface as surface_mod

    surface = [o for o in surface_mod.options()
               if not o["nocli"] and o["class"] == "PrintConfigDef" and o["key"] not in SKIP_KEYS]
    model_3mf = REPO_ROOT / "test_projects" / "Stanford_Bunny.3mf"
    model_stl = REPO_ROOT / "test_projects" / "synthetic" / "cube20.stl"

    def failure_kind(rc):
        return "hang" if rc is None else ("crash" if rc < 0 else "rejected")

    # baseline merged config (3mf + presets)
    (work / "base").mkdir(exist_ok=True)
    rc, cwd = sweep.run(sweep.base_args(work / "base") + ["--export-settings", work / "base.json", model_3mf], timeout=120)
    if rc != 0:
        detail = (work / "base" / "result.json").read_text() if (work / "base" / "result.json").exists() else "(no result.json)"
        raise AssertionError(f"baseline export failed with rc={rc}; cwd={cwd}; result.json: {detail[:400]}")
    base = sc.parse_settings_json((work / "base.json").read_text())

    probes, skipped = {}, {}
    for o in surface:
        k = o["key"]
        if k not in base:
            skipped[k] = "not in merged baseline"
            continue
        if o["enum_dynamic"] and not o["enum_values"]:
            skipped[k] = "dynamic enum"
            continue
        v, why = choose_value(o, base[k])
        if v is None:
            skipped[k] = why
        else:
            probes[k] = v

    def flag(k):
        return f"--{k.replace('_', '-')}={probes[k]}"

    def merge_runner(keys):
        (cwd_out := work / f"m{sweep.n}").mkdir(exist_ok=True)  # --export-settings needs its target dir to exist
        rc, cwd = sweep.run(sweep.base_args(cwd_out) +
                            [flag(k) for k in keys] + ["--export-settings", cwd_out / "s.json", model_3mf], timeout=120)
        if rc != 0 or not (cwd_out / "s.json").exists():
            return False, failure_kind(rc), {}
        got = sc.parse_settings_json((cwd_out / "s.json").read_text())
        return True, None, {k: k in got and sc.values_match(k, got[k], probes[k].split(",") if isinstance(base[k], list) else probes[k]) for k in keys}

    def gcode_runner(keys):
        (cwd_out := work / f"g{sweep.n}").mkdir(exist_ok=True)
        rc, cwd = sweep.run(sweep.base_args(cwd_out) +
                            [flag(k) for k in keys] + ["--slice", "0", model_stl], timeout=180)
        gpath = cwd_out / "plate_1.gcode"
        if rc != 0 or not gpath.exists():
            return False, failure_kind(rc), {}
        try:
            got = sc.parse_gcode_config(gpath.read_text(errors="replace"))
        except ValueError:
            # exit 0 but the G-code carries no config block -> nothing can be verified;
            # bisected down, the responsible option is reported as a silent drop
            return False, "silent_drop", {}
        res = {}
        for k in keys:
            if k not in got:
                res[k] = True  # not part of the G-code dump (banned keys); merge stage covers it
            else:
                res[k] = sc.values_match(k, got[k], probes[k].split(",") if isinstance(base[k], list) else probes[k])
        return True, None, res

    keys = sorted(probes)
    merge = {}
    for i in range(0, len(keys), BATCH):
        merge.update(_bisect(sweep, keys[i:i + BATCH], probes, merge_runner))
    landed = [k for k in keys if merge[k] == "landed"]
    gcode = {}
    for i in range(0, len(landed), BATCH):
        gcode.update(_bisect(sweep, landed[i:i + BATCH], probes, gcode_runner))

    report = {"probes": probes, "skipped": skipped, "merge": merge, "gcode": gcode, "invocations": sweep.n,
              "binary": str(orca_bin), "surface_origin": surface_mod.load()["origin"]}
    text = json.dumps(report, indent=2, sort_keys=True)
    (work / "override_report.json").write_text(text)
    # also at a stable, git-ignored path so scripts/override_coverage.py can find the last run
    stable = REPO_ROOT / ".pytest_cache" / "override_report.json"
    stable.parent.mkdir(exist_ok=True)
    stable.write_text(text)
    return report


def _bucket(report, stage, name):
    return sorted(k for k, b in report[stage].items() if b == name)


@pytest.mark.cli_overrides
def test_override_sweep_no_crashes(override_results):
    r = override_results
    crashes = _bucket(r, "merge", "crash") + _bucket(r, "gcode", "crash")
    hangs = _bucket(r, "merge", "hang") + _bucket(r, "gcode", "hang")
    assert not crashes and not hangs, (
        f"options whose override crashed the CLI: {[(k, r['probes'][k]) for k in crashes]}; "
        f"options whose override made the slicer hang past the timeout: {[(k, r['probes'][k]) for k in hangs]}")


@pytest.mark.cli_overrides
def test_override_sweep_merge_stage(override_results):
    r = override_results
    dropped = _bucket(r, "merge", "silent_drop")
    summary = (f"{len(r['probes'])} probed, {len(_bucket(r, 'merge', 'landed'))} landed, "
               f"{len(_bucket(r, 'merge', 'rejected'))} rejected, {len(dropped)} silently dropped, "
               f"{len(r['skipped'])} skipped, {r['invocations']} CLI invocations")
    print("\n[override sweep / merge] " + summary)
    print("  rejected:", _bucket(r, "merge", "rejected"))
    print("  skipped :", {k: v for k, v in r["skipped"].items()})
    assert not dropped, ("options given on the CLI that did NOT reach the merged config "
                         "(exit 0, value unchanged): " + ", ".join(f"{k}={r['probes'][k]}" for k in dropped))


@pytest.mark.cli_overrides
def test_override_sweep_gcode_stage(override_results):
    r = override_results
    dropped = _bucket(r, "gcode", "silent_drop")
    print(f"\n[override sweep / gcode] {len(r['gcode'])} sliced, {len(_bucket(r, 'gcode', 'landed'))} landed, "
          f"{len(_bucket(r, 'gcode', 'rejected'))} rejected at slice time, {len(dropped)} silently dropped")
    print("  rejected at slice:", _bucket(r, "gcode", "rejected"))
    assert not dropped, ("options that reached the merged config but NOT the G-code config block "
                         "of the slice: " + ", ".join(f"{k}={r['probes'][k]}" for k in dropped))
