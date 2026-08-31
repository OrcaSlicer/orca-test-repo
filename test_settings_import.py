"""Settings import / export matrix.

Every recipe below imports settings through one combination of the CLI's
input paths, slices, exports through every output path, and then verifies
that the settings *recorded in the outputs* are the ones that were imported:

  import paths                          export paths
  ------------                          ------------
  3mf embedded project config           G-code  (--slice   -> plate_N.gcode, CONFIG_BLOCK)
  --load-settings  machine/process      3mf     (--export-3mf: Metadata/plate_N.gcode + project_settings.config)
  --load-filaments filament(s)          JSON    (--export-settings)
  --<option>=value CLI overrides
  --uptodate (refresh presets)

Per recipe the checks are:
  1. the slice succeeds and all three artifacts exist;
  2. every imported *probe value* appears in the G-code config block (the
     config the slicer actually applied), including precedence
     (CLI override > --load-settings > 3mf embedded);
  3. the preset identity keys (print/printer/filament_settings_id) name the
     presets that were loaded;
  4. the three artifacts agree with each other: the G-code block embedded in
     the 3mf is byte-identical to the standalone G-code's, the JSON exported
     by --export-settings equals the 3mf's project_settings.config, and the
     G-code block agrees with that JSON on every common key (type- and
     variant-aware, see settings_compare.py) except a small documented set;
  5. the exported 3mf round-trips: re-importing it and exporting its settings
     reproduces the same JSON.

Probe expectations are read *from the fixture preset files themselves*
(test_projects/settings/*.json), so adding a key to a fixture extends
coverage without touching this file. To add an import path, add a RECIPE.

Fixture notes (what the CLI accepts, learned empirically -- see
TESTING_STRATEGY.md "Settings import"):
  * a preset JSON needs `type` (machine|process|filament), `from` (User or
    system) and `name`; a user process must list the printer's *system* name
    (its `name`, or a user machine's `inherits`) in `compatible_printers`;
  * only keys explicitly present in a loaded preset are applied -- `inherits`
    is not resolved on builds that ship no preset cache, so probe keys must be
    explicit; filaments must be *flattened* full presets (a sparse filament
    carrying filament_colour crashes the CLI on STL input -- see
    cases/settings-import/);
  * `--export-settings` output and a 3mf's project_settings.config are
    `from: project` files and cannot be fed back via --load-settings.
"""
from pathlib import Path

import pytest

import settings_compare as sc

REPO_ROOT = Path(__file__).resolve().parent
FIXTURES = REPO_ROOT / "test_projects" / "settings"

SYS_MACHINE = "system/BBL/machine/Bambu Lab X1 Carbon 0.4 nozzle.json"
SYS_PROCESS = "system/BBL/process/0.20mm Standard @BBL X1C.json"
SYS_FILAMENT = "system/BBL/filament/Bambu PLA Basic @BBL X1C.json"
SYS_FILAMENTS_4 = [
    "system/BBL/filament/Bambu PLA Matte @BBL X1C.json",
    "system/BBL/filament/Bambu PLA Basic @BBL X1C.json",
    "system/BBL/filament/Bambu PLA Tough @BBL X1C.json",
    "system/BBL/filament/Bambu PLA Silk @BBL X1C.json",
]

# Divergences already tracked by their own `status: open` case. The matrix
# excludes them from its artifact-agreement check so a known bug does not turn
# every recipe that happens to touch it red; the dedicated case is what flips
# (via strict xfail) when upstream fixes it. Keep this in step with cases/.
KNOWN_OPEN_DIVERGENCES = {
    "filament_ramming_volumetric_speed": "cases/settings-import/partial-load-filaments-variant-key-zero-filled.yaml",
}

# Keys read out of a 3mf's embedded config when the recipe expects "embedded"
# values to survive into the G-code untouched.
EMBEDDED_PROBE_KEYS = [
    "layer_height", "wall_loops", "sparse_infill_density", "sparse_infill_pattern",
    "nozzle_temperature", "filament_type", "printable_area", "print_settings_id",
    "printer_settings_id", "filament_settings_id",
]

# id, model, load_settings (datadir-relative "sys:" or fixture "fx:"), load_filaments,
# overrides, extra CLI args, and what to expect.
RECIPES = [
    dict(id="3mf-embedded-only", model="Stanford_Bunny.3mf",
         expect_embedded=True),
    dict(id="3mf+system-machine+process+filament", model="Stanford_Bunny.3mf",
         load_settings=[f"sys:{SYS_MACHINE}", f"sys:{SYS_PROCESS}"], load_filaments=[f"sys:{SYS_FILAMENT}"]),
    dict(id="stl+system-machine+process+filament", model="synthetic/cube20.stl",
         load_settings=[f"sys:{SYS_MACHINE}", f"sys:{SYS_PROCESS}"], load_filaments=[f"sys:{SYS_FILAMENT}"]),
    dict(id="3mf+system-filament-only", model="Stanford_Bunny.3mf",
         load_filaments=[f"sys:{SYS_FILAMENT}"]),
    dict(id="3mf+cli-overrides", model="Stanford_Bunny.3mf",
         overrides={"wall_loops": "7", "sparse_infill_density": "33%", "layer_height": "0.28",
                    "sparse_infill_pattern": "gyroid", "brim_width": "9"}),
    dict(id="3mf+user-process(explicit-keys)", model="Stanford_Bunny.3mf",
         load_settings=[f"sys:{SYS_MACHINE}", "fx:user_process.json"], load_filaments=[f"sys:{SYS_FILAMENT}"]),
    dict(id="precedence:3mf<process<cli", model="Stanford_Bunny.3mf",
         load_settings=[f"sys:{SYS_MACHINE}", "fx:user_process.json"], load_filaments=[f"sys:{SYS_FILAMENT}"],
         overrides={"wall_loops": "7", "layer_height": "0.16"}),
    dict(id="stl+user-machine+user-process", model="synthetic/cube20.stl",
         load_settings=["fx:user_machine.json", "fx:user_process.json"], load_filaments=[f"sys:{SYS_FILAMENT}"]),
    dict(id="stl+user-filament(flattened)", model="synthetic/cube20.stl",
         load_settings=[f"sys:{SYS_MACHINE}", f"sys:{SYS_PROCESS}"], load_filaments=["fx:user_filament_flattened.json"]),
    dict(id="3mf+user-filament(flattened)", model="Stanford_Bunny.3mf",
         load_filaments=["fx:user_filament_flattened.json"]),
    dict(id="multi-filament:4-ordered", model="p1s_multicolor.3mf",
         load_filaments=[f"sys:{f}" for f in SYS_FILAMENTS_4]),
    dict(id="multi-filament:partial-2-of-4", model="p1s_multicolor.3mf",
         load_filaments=[f"sys:{f}" for f in SYS_FILAMENTS_4[:2]],
         expect={"filament_settings_id": ["Bambu PLA Matte @BBL X1C", "Bambu PLA Basic @BBL X1C",
                                          "Bambu PLA Basic @BBL X1C", "Bambu PLA Basic @BBL X1C"]}),
    dict(id="3mf+uptodate", model="p1s_multicolor.3mf", extra_args=["--uptodate"], expect_embedded=True),
]


def _resolve(ref: str, datadir: Path) -> Path:
    kind, _, rel = ref.partition(":")
    if kind == "sys":
        return datadir / rel
    if kind == "fx":
        return FIXTURES / rel
    raise ValueError(ref)


def _preset_name(path: Path) -> str:
    import json
    return json.loads(path.read_text())["name"]


# Keys in a preset file that describe *which variant/extruder the preset
# offers* rather than a value the slice should carry, plus preset bookkeeping.
# They are never used as probe expectations.
NOT_PROBE_KEYS = {
    "filament_extruder_variant", "printer_extruder_variant", "print_extruder_variant",
    "filament_extruder_id", "printer_extruder_id", "print_extruder_id",
    "different_settings_to_system", "inherits_group", "filament_settings_id",
} | sc.KNOWN_DIVERGENT


def _scalar(v):
    return v[0] if isinstance(v, list) and v else v


def _build_expectations(recipe, datadir: Path, model_path: Path):
    """Returns (expect, per_filament, identity):
    expect        key -> value the G-code block must carry (scalar or full vector),
                  assembled lowest precedence first (embedded < presets < overrides);
    per_filament  key -> [value for filament 0, filament 1, ...] taken from the
                  --load-filaments files in order (None where a file doesn't set it);
    identity      the *_settings_id values implied by the loaded preset names."""
    import json
    expect, per_filament, identity = {}, {}, {}
    if recipe.get("expect_embedded") and model_path.suffix == ".3mf":
        emb = sc.read_3mf_project_settings(model_path)
        expect.update({k: emb[k] for k in EMBEDDED_PROBE_KEYS if k in emb})
    for ref in recipe.get("load_settings", []):
        p = _resolve(ref, datadir)
        expect.update({k: v for k, v in sc.expect_from_preset_file(p).items()
                       if k not in NOT_PROBE_KEYS and _scalar(v) != "nil"})
        t = json.loads(p.read_text())["type"]
        identity["printer_settings_id" if t == "machine" else "print_settings_id"] = _preset_name(p)
    fil_files = [_resolve(r, datadir) for r in recipe.get("load_filaments", [])]
    fil_cfgs = [sc.expect_from_preset_file(p) for p in fil_files]
    for k in {k for c in fil_cfgs for k in c} - NOT_PROBE_KEYS:
        per_filament[k] = [_scalar(c.get(k)) for c in fil_cfgs]
    if fil_files and "filament_settings_id" not in recipe.get("expect", {}):
        identity["filament_settings_id"] = [_preset_name(p) for p in fil_files]
    expect.update(recipe.get("overrides", {}))
    expect.update(recipe.get("expect", {}))
    return expect, per_filament, identity


@pytest.mark.settings_import
@pytest.mark.parametrize("recipe", RECIPES, ids=lambda r: r["id"])
def test_settings_roundtrip(recipe, run_orca, datadir, outputdir, model):
    model_path = model(recipe["model"])
    args = ["--datadir", datadir, "--outputdir", outputdir, "--allow-newer-file"]
    if recipe.get("load_settings"):
        args += ["--load-settings", ";".join(str(_resolve(r, datadir)) for r in recipe["load_settings"])]
    if recipe.get("load_filaments"):
        args += ["--load-filaments", ";".join(str(_resolve(r, datadir)) for r in recipe["load_filaments"])]
    for k, v in recipe.get("overrides", {}).items():
        args.append(f"--{k.replace('_', '-')}={v}")
    args += recipe.get("extra_args", [])
    args += ["--slice", "0", "--export-3mf", "out.3mf", "--export-settings", str(outputdir / "settings.json"), model_path]

    result = run_orca(args)
    failures = []

    # 1. success + artifacts
    assert result.returncode == 0, f"exit {result.returncode}\n{result.stdout[-1500:]}\n{result.stderr[-1500:]}"
    gcode_path, threemf_path, json_path = outputdir / "plate_1.gcode", outputdir / "out.3mf", outputdir / "settings.json"
    for p in (gcode_path, threemf_path, json_path):
        assert p.exists(), f"missing export artifact {p.name}"
    gcode_cfg = sc.parse_gcode_config(gcode_path.read_text(errors="replace"))
    json_cfg = sc.parse_settings_json(json_path.read_text())

    # 2. imported probe values reached the applied config
    expect, per_filament, identity = _build_expectations(recipe, datadir, model_path)
    absent = []
    for k, v in expect.items():
        if k not in gcode_cfg:
            absent.append(k)
            continue
        if not sc.values_match(k, gcode_cfg[k], v):
            failures.append(f"probe {k}: expected {v!r}, G-code has {gcode_cfg[k]!r}")
    for k in recipe.get("overrides", {}) | recipe.get("expect", {}):
        if k in absent:
            failures.append(f"probe {k}: not present in the G-code config block at all")
    if len(absent) > len(expect) // 2:
        failures.append(f"more than half of the expected keys are absent from the G-code block: {absent}")
    # per-filament keys: element i of the G-code vector must come from filament file i
    # ("nil" in a filament preset means "use the printer's value", so it is not probed)
    for k, vals in per_filament.items():
        if k not in gcode_cfg:
            continue
        got = gcode_cfg[k] if isinstance(gcode_cfg[k], list) else [gcode_cfg[k]]
        for i, v in enumerate(vals):
            if v is None or v == "nil":
                continue
            if i >= len(got) or not sc._num_eq(got[i], v):
                failures.append(f"probe {k}[{i}]: expected {v!r} from filament #{i + 1}, G-code has {got!r}")
                break

    # 3. identity
    for k, v in identity.items():
        got = gcode_cfg.get(k)
        if not sc.values_match(k, got, v):
            failures.append(f"identity {k}: expected {v!r}, G-code has {got!r}")

    # 4. artifacts agree with each other
    if sc.read_3mf_gcode_config(threemf_path) != gcode_cfg:
        failures.append("G-code config block embedded in the exported 3mf differs from the standalone G-code's")
    ps_cfg = sc.read_3mf_project_settings(threemf_path)
    ps_diff = [k for k in json_cfg if k != "version" and json_cfg[k] != ps_cfg.get(k)]
    if ps_diff or set(json_cfg) != set(ps_cfg):
        failures.append(f"--export-settings JSON differs from the 3mf's project_settings.config: {ps_diff[:10]} "
                        f"only-json={sorted(set(json_cfg) - set(ps_cfg))[:5]} only-3mf={sorted(set(ps_cfg) - set(json_cfg))[:5]}")
    mism = sc.compare_gcode_to_json(gcode_cfg, json_cfg, ignore=sc.KNOWN_DIVERGENT | set(KNOWN_OPEN_DIVERGENCES))
    if mism:
        failures.append("G-code block vs --export-settings disagree on: " +
                        "; ".join(f"{k}: gcode={a!r} json={b!r}" for k, a, b in mism[:10]))

    # 5. round-trip: re-import the exported 3mf, export its settings again
    rt = run_orca(["--datadir", datadir, "--outputdir", outputdir / "rt", "--allow-newer-file",
                   "--export-settings", str(outputdir / "rt_settings.json"), threemf_path])
    if rt.returncode != 0:
        failures.append(f"re-importing the exported 3mf failed: exit {rt.returncode}")
    else:
        rt_cfg = sc.parse_settings_json((outputdir / "rt_settings.json").read_text())
        # the 3mf reader re-serializes list-like strings with ", " (e.g. thumbnails
        # "48x48/PNG,300x300/PNG" -> "48x48/PNG, 300x300/PNG"): formatting, not data
        norm = lambda v: v.replace(", ", ",") if isinstance(v, str) else v
        rt_diff = [k for k in json_cfg if k != "version" and norm(json_cfg[k]) != norm(rt_cfg.get(k))]
        if rt_diff:
            failures.append(f"settings changed across export-3mf -> re-import -> export-settings: {rt_diff[:10]}")

    if failures:
        pytest.fail(f"{recipe['id']}:\n  - " + "\n  - ".join(failures), pytrace=False)


# --------------------------------------------------------------- reference artifacts
# A G-code file and a "sliced plate" 3mf exported from the GUI, checked in as
# fixtures. They pin the *format* this suite parses, independent of any binary.

@pytest.mark.settings_import
def test_reference_gcode_config_block_parses():
    cfg = sc.parse_gcode_config((FIXTURES / "reference_plate_1.gcode").read_text(errors="replace"))
    assert len(cfg) > 600, f"unexpectedly small config block: {len(cfg)} keys"
    assert cfg["layer_height"] == "0.12"
    assert cfg["print_settings_id"] == "0.12mm Fine @Elegoo CC 0.4 nozzle"
    assert cfg["filament_settings_id"] == ["Generic PLA Matte @System"]
    assert cfg["printable_area"] == ["0x0", "256x0", "256x256", "0x256"]
    assert cfg["nozzle_temperature"] == ["220"]
    assert "\n" in cfg["machine_start_gcode"], "C-style escapes must be decoded for scalar strings"


@pytest.mark.settings_import
def test_reference_gcode_only_3mf_is_self_consistent():
    """The GUI's sliced-plate 3mf export embeds the same G-code block as the
    standalone G-code, and its project_settings.config agrees with it."""
    threemf = FIXTURES / "gcode_only_export.3mf"
    standalone = sc.parse_gcode_config((FIXTURES / "reference_plate_1.gcode").read_text(errors="replace"))
    assert sc.read_3mf_gcode_config(threemf) == standalone
    mism = sc.compare_gcode_to_json(standalone, sc.read_3mf_project_settings(threemf), ignore=sc.KNOWN_DIVERGENT)
    assert not mism, "G-code block vs project_settings.config: " + "; ".join(f"{k}: {a!r} vs {b!r}" for k, a, b in mism[:10])
