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
  stage "effect" -- a landed value should normally also CHANGE the sliced
                    G-code. One slice per option, compared against a
                    baseline slice: `effective` (instruction stream changed,
                    with the list of slicing metrics that moved as its
                    signature) or `inert` (stream identical -- the mining
                    output: an option that lands in the config but is
                    ignored by the slicing pipeline, unless it is
                    legitimately inert for this model, e.g. support options
                    with supports off). Reported, not asserted. Because it
                    cannot batch, routine runs take a daily-rotating sample
                    (--effect-sample, default 15); --effect-full sweeps
                    every landed option.

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
import collections
import datetime
import json
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

import settings_compare as sc

sys.path.insert(0, str(Path(__file__).resolve().parent / "parity"))
import gcode_metrics  # noqa: E402
from compare_gcode3mf import normalized_stream  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent
SURFACE = REPO_ROOT / "cases" / "_snapshots" / "cli_surface_full.json"
ROUTING = REPO_ROOT / "parity" / "effect_routing.json"
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

# Options that cannot move the normalized G-code stream no matter what is
# sliced: the comparison drops comments and M73, and these only ever reach a
# comment, a time/cost estimate, the GUI, or printer metadata. Reporting them
# as "inert" every run buries the real findings, so the effect stage scores
# them `unobservable` instead. This is about the *comparison*, not the option:
# each one is still checked by the merge and G-code stages.
UNOBSERVABLE = {
    "bbl_calib_mark_logo", "bed_custom_model", "bed_custom_texture",
    "best_object_pos", "default_filament_colour", "disable_m73",
    "enable_filament_dynamic_map", "extruder_max_nozzle_count", "filament_adhesiveness_category",
    "filament_cost", "filament_density", "filament_dev_ams_drying_ams_limitations",
    "filament_dev_ams_drying_heat_distortion_temperature", "filament_dev_ams_drying_temperature", "filament_dev_ams_drying_time",
    "filament_dev_chamber_drying_bed_temperature", "filament_dev_chamber_drying_time", "filament_dev_drying_cooling_temperature",
    "filament_dev_drying_softening_temperature", "filament_extruder_compatibility", "filament_notes",
    "filename_format", "gcode_comments", "machine_bed_mass_Y",
    "machine_hotend_change_time", "machine_load_filament_time", "machine_max_force_Y",
    "machine_max_printed_mass", "machine_min_extruding_rate", "machine_min_travel_rate",
    "machine_prepare_compensation_time", "machine_tool_change_time", "machine_unload_filament_time",
    "notes", "nozzle_flush_dataset", "nozzle_height",
    "nozzle_hrc", "nozzle_temperature_range_low", "nozzle_type",
    "nozzle_volume", "preferred_orientation", "print_order",
    "printer_notes", "required_nozzle_HRC", "temperature_vitrification",
    "time_cost",
}

# Reads its value (Print.cpp:4628 applies filament_shrink to the model) yet
# produces byte-identical G-code -- kept out of UNOBSERVABLE so it stays in
# the inert list as a finding rather than being quietly exempted.
SUSPECTED_IGNORED = {"filament_shrink"}

# 0 means "use the object's own filament", so the default 0 -> 1 probe asks
# for the filament the region already prints in and can never change anything.
INHERIT_ZERO_KEYS = {
    "top_surface_filament_id", "bottom_surface_filament_id", "inner_wall_filament_id",
    "outer_wall_filament_id", "internal_solid_filament_id", "sparse_infill_filament_id",
}

# coString options whose value has a grammar; a `probe_<key>` token is not a
# valid value and is discarded, so the option reads as inert.
STRUCTURED_STRINGS = {
    "extra_solid_infills": "5",   # layer spec: N, N#K, or an explicit list
}

NUMERIC_VECTOR_TYPES = {"coFloats", "coInts", "coPercents", "coFloatsOrPercents"}


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:.4g}"


def _numeric_probe(cur: str, lo, hi, integer: bool, down_first: bool = False) -> str | None:
    try:
        c = float(cur)
    except ValueError:
        return None
    lo = -1e9 if lo is None else lo
    hi = 1e9 if hi is None else hi
    cands = [c * 1.5 + (1 if c == 0 else 0), c + 1, c / 2, c - 1, lo + (hi - lo) / 3]
    if down_first:
        # A speed raised past the volumetric-flow limit clamps to the same
        # number as the baseline (inner_wall_speed 300 -> 450 is byte-identical
        # on a 0.4 nozzle with PLA), so halving is the only probe that moves.
        cands = [c / 2, c / 4, c - 1] + cands
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
    key = meta["key"]
    cur_list = current if isinstance(current, list) else None
    cur = current[0] if cur_list else current
    down_first = "speed" in key
    if key in STRUCTURED_STRINGS:
        return STRUCTURED_STRINGS[key], STRUCTURED_STRINGS[key]
    if key in INHERIT_ZERO_KEYS:
        # anything but 0 and the region's own filament
        v = "2" if str(cur) in ("0", "1") else "1"
        return v, v

    def scalar(cur_s):
        if t in ("coFloat", "coInt"):
            return _numeric_probe(cur_s, lo, hi, t == "coInt", down_first)
        if t in ("coFloats", "coInts"):
            return _numeric_probe(cur_s, lo, hi, t == "coInts", down_first)
        if t in ("coPercent", "coPercents"):
            # percent options can legitimately exceed 100% (e.g. wipe_tower_extra_spacing 100-300%)
            v = _numeric_probe(cur_s.rstrip("%"), lo, hi, False, down_first)
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
                v = _numeric_probe(cur_s.rstrip("%"), lo, hi, False, down_first)
                return None if v is None else v + "%"
            return _numeric_probe(cur_s, lo, hi, False, down_first)
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

    def variant_args(self, outdir, spec):
        """base_args for a fixture: its own presets, overlay and models."""
        return (["--datadir", self.datadir, "--outputdir", outdir, "--allow-newer-file",
                 "--load-settings", spec["settings"], "--load-filaments", spec["filaments"]]
                + [f"--{k.replace('_', '-')}={v}" for k, v in spec["overlay"].items()])


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
def override_results(orca_bin, seeded_data_dir, tmp_path_factory, request):
    work = tmp_path_factory.mktemp("overrides")
    sweep = Sweep(orca_bin, seeded_data_dir, work)
    import surface as surface_mod

    surface = [o for o in surface_mod.options()
               if not o["nocli"] and o["class"] == "PrintConfigDef" and o["key"] not in SKIP_KEYS]
    meta_by_key = {o["key"]: o for o in surface}
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

    # stage "effect": one slice per option against a baseline slice. Sampled
    # by default (mirrors the fuzz suite's daily-rotating reproducible sample)
    # because it cannot batch.
    effect_keys = sorted(k for k, b in gcode.items() if b == "landed")
    if not request.config.getoption("--effect-full"):
        sample = request.config.getoption("--effect-sample")
        rng = random.Random(datetime.date.today().isoformat())
        effect_keys = sorted(rng.sample(effect_keys, min(sample, len(effect_keys)))) if sample > 0 else []
    shard = request.config.getoption("--effect-shard")
    if shard:
        # parity/effect_routing.json groups options by the cheapest fixture that
        # can show their effect and balances those groups by measured slice cost,
        # so each shard is a similar wall-clock slice of one --effect-full run.
        i, n = (int(x) for x in shard.split("/"))
        routing = json.loads((ROUTING).read_text())
        mine = {k for keys in routing["shards"].get(str(i), {}).values() for k in keys}
        unrouted = [k for k in effect_keys if k not in routing["routing"]]
        # options with no recorded fixture are spread round-robin so a new option
        # is never silently dropped from every shard
        mine |= {k for j, k in enumerate(sorted(unrouted)) if j % n == i}
        effect_keys = [k for k in effect_keys if k in mine]
        print(f"\n[override sweep] shard {i}/{n}: {len(effect_keys)} of the landed options")
    effect, effect_probes, effect_variant = {}, dict(probes), {}
    if effect_keys:
        import effect_variants

        # The Custom-vendor Klipper/Marlin/toolchanger presets have to be
        # flattened before the CLI will load them: it reads one file, never
        # walks `inherits`, and takes compatible_printers from the leaf alone.
        # If that fails, those variants are dropped and their options fall back
        # to the cube rather than failing the run.
        flat = work / "flat_presets"
        try:
            subprocess.run([sys.executable, str(REPO_ROOT / "parity" / "make_custom_presets.py"),
                            "--seed", str(sweep.datadir), "--out", str(flat)],
                           check=True, capture_output=True, timeout=120)
        except Exception as exc:
            print(f"\n[override sweep / effect] no flattened Custom presets ({exc}); "
                  "Klipper/Marlin/toolchanger options fall back to the cube")
            flat = None
        specs = effect_variants.specs(sweep.datadir, model_stl, flat)

        wanted = {}
        for k in effect_keys:
            if k in UNOBSERVABLE:
                # comment, estimate, GUI or metadata only -- the normalized
                # stream cannot show it, so slicing it proves nothing
                effect[k] = {"bucket": "unobservable"}
                continue
            wanted.setdefault(effect_variants.route(k, specs), []).append(k)

        for vname, keys in sorted(wanted.items()):
            spec = specs[vname]
            models = [str(m) for m in spec["models"]]
            # Probe against this fixture's own merged config. Probing against a
            # different baseline than the one sliced lands the probe on the value
            # being sliced and scores the option without testing it (28 options
            # on a stock X1C profile).
            (pdir := work / f"probe_{vname}").mkdir(exist_ok=True)
            rc, _ = sweep.run(sweep.variant_args(pdir, spec) +
                              ["--export-settings", pdir / "base.json"] + models, timeout=300)
            if rc == 0 and (pdir / "base.json").exists():
                vbase = sc.parse_settings_json((pdir / "base.json").read_text())
                for k in keys:
                    if k in effect_variants.PROBE_OVERRIDES:
                        effect_probes[k] = effect_variants.PROBE_OVERRIDES[k]
                    elif k in vbase:
                        val, _why = choose_value(meta_by_key[k], vbase[k])
                        if val is not None:
                            effect_probes[k] = val

            (bdir := work / f"base_{vname}").mkdir(exist_ok=True)
            rc, cwd = sweep.run(sweep.variant_args(bdir, spec) + ["--slice", "0"] + models,
                                timeout=600)
            gbase = bdir / "plate_1.gcode"
            if rc != 0 or not gbase.exists():
                # a fixture that will not slice is a finding, not a reason to
                # abort the other variants
                for k in keys:
                    effect[k] = {"bucket": "no_baseline", "variant": vname}
                print(f"\n[override sweep / effect] fixture {vname!r} did not slice "
                      f"(rc={rc}, cwd={cwd}); {len(keys)} options unmeasured")
                continue
            base_text = gbase.read_text(errors="replace")
            base_stream = normalized_stream(base_text)
            base_metrics = gcode_metrics.parse(base_text)

            for k in keys:
                effect_variant[k] = vname
                (edir := work / f"effect_{k}").mkdir(exist_ok=True)
                rc, _ = sweep.run(sweep.variant_args(edir, spec) +
                                  [f"--{k.replace('_', '-')}={effect_probes[k]}", "--slice", "0"]
                                  + models, timeout=600)
                g = edir / "plate_1.gcode"
                if rc != 0 or not g.exists():
                    effect[k] = {"bucket": failure_kind(rc), "variant": vname,
                                 "probe": effect_probes[k]}
                    continue
                text = g.read_text(errors="replace")
                if normalized_stream(text) == base_stream:
                    effect[k] = {"bucket": "inert", "variant": vname, "probe": effect_probes[k]}
                else:
                    m = gcode_metrics.parse(text)
                    moved = sorted(f for f in base_metrics
                                   if not str(f).startswith("_") and m.get(f) != base_metrics[f])
                    effect[k] = {"bucket": "effective", "variant": vname,
                                 "probe": effect_probes[k], "moved": moved}

    report = {"probes": probes, "effect_probes": effect_probes,
              "effect_variant": effect_variant, "skipped": skipped,
              "merge": merge, "gcode": gcode, "effect": effect,
              "invocations": sweep.n,
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
def test_override_sweep_effect_stage(override_results):
    r = override_results
    eff = r.get("effect", {})
    if not eff:
        pytest.skip("effect stage disabled (--effect-sample 0 and no --effect-full)")
    effective = sorted(k for k, v in eff.items() if v["bucket"] == "effective")
    inert = sorted(k for k, v in eff.items() if v["bucket"] == "inert")
    unobservable = sorted(k for k, v in eff.items() if v["bucket"] == "unobservable")
    broken = sorted(k for k, v in eff.items() if v["bucket"] in ("crash", "hang"))
    no_base = sorted(k for k, v in eff.items() if v["bucket"] == "no_baseline")
    print(f"\n[override sweep / effect] {len(eff) - len(unobservable) - len(no_base)} options "
          f"sliced individually: {len(effective)} effective, {len(inert)} inert, "
          f"{len(broken)} crashed/hung; {len(unobservable)} not observable in a comment-free "
          f"stream (not sliced); {len(no_base)} whose fixture would not slice")
    per = collections.Counter(v.get("variant", "cube") for v in eff.values()
                              if v["bucket"] in ("effective", "inert"))
    hit = collections.Counter(v.get("variant", "cube") for v in eff.values()
                              if v["bucket"] == "effective")
    print("  by fixture: " + ", ".join(f"{v} {hit[v]}/{n}" for v, n in sorted(per.items())))
    if no_base:
        print("  fixtures that would not slice:",
              sorted({eff[k]["variant"] for k in no_base}))
    # inert is the mining output, not a failure: an option can be legitimately
    # inert for this model/config (support options with supports off, ...) --
    # but a key that SHOULD matter appearing here is a silently-ignored-flag bug
    print("  inert:", inert)
    assert not broken, (
        "options whose individual override slice crashed or hung: "
        + ", ".join(f"{k}={r['probes'][k]}" for k in broken))


@pytest.mark.cli_overrides
def test_override_sweep_gcode_stage(override_results):
    r = override_results
    dropped = _bucket(r, "gcode", "silent_drop")
    print(f"\n[override sweep / gcode] {len(r['gcode'])} sliced, {len(_bucket(r, 'gcode', 'landed'))} landed, "
          f"{len(_bucket(r, 'gcode', 'rejected'))} rejected at slice time, {len(dropped)} silently dropped")
    print("  rejected at slice:", _bucket(r, "gcode", "rejected"))
    assert not dropped, ("options that reached the merged config but NOT the G-code config block "
                         "of the slice: " + ", ".join(f"{k}={r['probes'][k]}" for k in dropped))
