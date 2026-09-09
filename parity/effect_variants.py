"""Fixtures for the override sweep's effect stage.

The effect stage slices one option at a time and asks whether the G-code
changed. On a 20 mm cube with stock presets almost nothing is switched on, so
455 of 617 landed options changed nothing and were scored inert -- mostly not
because the slicer ignores them, but because the fixture had nothing to say
about them.

A variant here is a complete fixture: presets, models and the baseline overlay
that turns a feature family on. `effect_routing.json` maps each option to the
cheapest variant that was measured to show its effect, so a temperature still
slices a bare cube (0.6 s) and only the options that need supports, a second
filament or a different printer pay for one.

Adding a variant: give it a spec here, route keys to it in effect_routing.json,
and re-balance the shards. A variant nothing routes to is never built.

Measured 2026-09-03/04 against RelWithDebInfo b81c0e30c1: 327 of the 450
classifiable inert options change the G-code under these fixtures.
See FIXTURE_MEASUREMENT.md.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ROUTING = HERE / "effect_routing.json"

# Baseline overlay shared by every single-material geometry variant. Each entry
# is a master switch that gates a whole family; without it every option in that
# family is a no-op no matter what is sliced.
SWITCHES = {
    "brim_type": "brim_ears", "brim_width": "5", "elefant_foot_compensation": "0.2",
    "ironing_type": "solid", "seam_slope_type": "external", "has_scarf_joint_seam": "1",
    "enable_pressure_advance": "1", "pressure_advance": "0.02",
    "activate_chamber_temp_control": "1", "chamber_temperature": "40",
    "activate_air_filtration": "1",
    "set_other_flow_ratios": "1",   # gates every *_flow_ratio
    "default_jerk": "9",            # gates every *_jerk; the X1C profile ships 0
    "zaa_enabled": "1", "auxiliary_fan": "1", "enable_long_retraction_when_cut": "1",
    "z_hop": "0.4", "wipe": "1", "infill_combination": "1",
    "slow_down_for_layer_cooling": "1", "hole_to_polyhole": "1",
    "make_overhang_printable": "1", "top_surface_expansion": "1", "overhang_reverse": "1",
    # deliberately absent: adaptive_pressure_advance overrides the static
    # pressure_advance per extrusion and hides the option under test.
}
SUPPORTS = {**SWITCHES, "enable_support": "1", "support_ironing": "1",
            "support_air_filtration": "1"}

BED_TYPES = {"cool": "Cool Plate", "eng": "Engineering Plate", "hot": "High Temp Plate",
             "textured": "Textured PEI Plate", "textured_cool": "Textured Cool Plate",
             "supertack": "Supertack Plate"}

# Options whose standard probe cannot move the slicer: a threshold no geometry
# crosses, or a value the option's own range rejects.
PROBE_OVERRIDES = {
    "hole_to_polyhole_threshold": "0.05", "hole_to_polyhole_max_edges": "4",
    "make_overhang_printable_angle": "20", "top_surface_expansion_margin": "3",
    "support_interface_top_layers": "0", "support_threshold_overlap": "90%",
    "enforce_support_layers": "10", "support_object_first_layer_gap": "1",
    "support_bottom_interface_spacing": "1.5", "support_interface_pattern": "concentric",
    "tree_support_branch_angle": "10", "tree_support_branch_diameter": "8",
    "tree_support_branch_distance": "10", "tree_support_brim_width": "10",
    "scarf_joint_speed": "20%", "brim_ears_max_angle": "30", "brim_ears_outer_only": "1",
    "wall_transition_filter_deviation": "0.9", "full_fan_speed_layer": "30",
    "slow_down_layers": "40", "slow_down_min_speed": "40",
    "infill_combination_max_layer_height": "400%", "filter_out_gap_fill": "10",
    "gap_fill_flow_ratio": "2", "gap_infill_speed": "10", "extra_solid_infills": "5",
    "overhang_reverse_threshold": "0", "overhang_reverse_internal_only": "1",
    "infill_overhang_angle": "70", "center_of_surface_pattern": "each_model",
    "input_shaping_freq_x": "40", "input_shaping_type": "ZV",
    # jerk emits against default_jerk=9; a step up to 13.5 is too small to move
    # the stream on the Marlin profile, a drop to 3 always does
    "initial_layer_jerk": "3", "inner_wall_jerk": "3", "outer_wall_jerk": "3",
    "top_surface_jerk": "3", "travel_jerk": "3", "infill_jerk": "3",
    "initial_layer_travel_jerk": "300%",
}


def _bbl(datadir, machine, process):
    return f"{datadir}/system/BBL/machine/{machine}.json;{datadir}/system/BBL/process/{process}.json"


def specs(datadir, model_stl, flat_dir=None):
    """name -> {settings, filaments, models, overlay}.

    `flat_dir` holds the Custom-vendor presets flattened by make_custom_presets.py;
    the CLI never walks `inherits` and takes compatible_printers from the leaf
    alone, so the raw vendor files fail its compat gate. Variants needing them
    are dropped when it is absent rather than failing the run.
    """
    d, f = Path(datadir), Path(flat_dir) if flat_dir else None
    bf = d / "system/BBL/filament"
    x1c = _bbl(d, "Bambu Lab X1 Carbon 0.4 nozzle", "0.20mm Standard @BBL X1C")
    p1s = _bbl(d, "Bambu Lab P1S 0.4 nozzle", "0.20mm Standard @BBL X1C")
    pla = str(bf / "Bambu PLA Basic @BBL X1C.json")
    pla4 = ";".join([pla, str(bf / "Bambu PLA Matte @BBL X1C.json")] * 2)
    fix = REPO / "parity" / "fixtures"
    plate, vase = [fix / "torture.stl"], [fix / "vase.stl"]
    cubes = [fix / "cube_a.stl", fix / "cube_b.stl"]
    proj = REPO / "test_projects"

    v = {
        # the default tier: exactly what the stage sliced before, for every
        # option that never needed more than a bare cube
        "cube":       dict(settings=x1c, filaments=pla, models=[model_stl], overlay={}),
        "switches":   dict(settings=x1c, filaments=pla, models=plate, overlay=SWITCHES),
        "classic":    dict(settings=x1c, filaments=pla, models=plate,
                           overlay={**SWITCHES, "wall_generator": "classic"}),
        "concentric": dict(settings=x1c, filaments=pla, models=plate,
                           overlay={**SWITCHES, "top_surface_pattern": "concentric",
                                    "bottom_surface_pattern": "concentric"}),
        "support":    dict(settings=x1c, filaments=pla, models=plate,
                           overlay={**SUPPORTS, "support_type": "normal(auto)",
                                    "support_style": "grid", "support_interface_top_layers": "3",
                                    "support_interface_bottom_layers": "3", "raft_layers": "3"}),
        "tree":       dict(settings=x1c, filaments=pla, models=plate,
                           overlay={**SUPPORTS, "support_type": "tree(auto)",
                                    "support_style": "tree_slim"}),
        "organic":    dict(settings=x1c, filaments=pla, models=plate,
                           overlay={**SUPPORTS, "support_type": "tree(auto)",
                                    "support_style": "organic"}),
        # ~90 s a slice against ~7 s for the rest, so it is never in SWITCHES
        "fuzzy":      dict(settings=x1c, filaments=pla, models=plate,
                           overlay={**SWITCHES, "fuzzy_skin": "external",
                                    "fuzzy_skin_noise_type": "perlin",
                                    "fuzzy_skin_mode": "combined"}),
        "adaptive_pa": dict(settings=x1c, filaments=pla, models=plate,
                            overlay={**SWITCHES, "adaptive_pressure_advance": "1"}),
        "spiral":     dict(settings=x1c, filaments=pla, models=vase,
                           overlay={"spiral_mode": "1", "wall_loops": "1",
                                    "top_shell_layers": "0", "sparse_infill_density": "0%",
                                    "enable_support": "0"}),
        # by-object sequencing enforces a 40 mm clearance radius, which the
        # torture plate is far too wide to satisfy
        "byobject":   dict(settings=x1c, filaments=pla, models=cubes,
                           overlay={"print_sequence": "by object", "skirt_loops": "2",
                                    "reduce_crossing_wall": "1"}),
        "draftshield": dict(settings=x1c, filaments=pla, models=plate,
                            overlay={**SWITCHES, "draft_shield": "enabled", "skirt_loops": "2"}),
    }
    for pat in ("gyroid", "lightning", "lateral-lattice", "zigzag"):
        v[{"lateral-lattice": "lattice"}.get(pat, pat)] = dict(
            settings=x1c, filaments=pla, models=plate,
            overlay={**SWITCHES, "sparse_infill_pattern": pat})
    v["lockedzag"] = dict(settings=x1c, filaments=pla, models=plate,
                          overlay={**SWITCHES, "sparse_infill_pattern": "lockedzag",
                                   "infill_lock_depth": "2", "skin_infill_depth": "2"})
    # only the current bed's temperature pair is emitted, so one plate per type
    for key, bed in BED_TYPES.items():
        v[f"bed_{key}"] = dict(settings=x1c, filaments=pla, models=plate,
                               overlay={"curr_bed_type": bed})
    # Multi-filament: the committed multicolor projects, never a CLI-authored
    # one -- --export-3mf leaves filament_colour/type/map at length 1 and the
    # plate reloads as single-filament with the prime tower forced off.
    v["mf_bbl"] = dict(settings=p1s, filaments=pla4,
                       models=[proj / "p1s_multicolor.3mf"],
                       overlay={"enable_support": "1", "support_ironing": "1"})
    if f and (f / "MyKlipper 0.4 nozzle.json").exists():
        klip = f"{f/'MyKlipper 0.4 nozzle.json'};{f/'0.20mm Standard @MyKlipper.json'}"
        marl = f"{f/'MyMarlin 0.4 nozzle.json'};{f/'0.20mm Standard @MyMarlin.json'}"
        tc = f"{f/'MyToolChanger 0.4 nozzle.json'};{f/'0.20mm Standard @MyToolChanger.json'}"
        gpla, gcf = str(f / "Generic PLA @System.json"), str(f / "Generic PLA-CF @System.json")
        v["klipper"] = dict(settings=klip, filaments=gpla, models=plate,
                            overlay={"default_jerk": "9", "enable_support": "1",
                                     "enable_pressure_advance": "1", "auxiliary_fan": "1",
                                     "activate_chamber_temp_control": "1",
                                     "chamber_temperature": "40", "input_shaping_emit": "1"})
        v["marlin"] = dict(settings=marl, filaments=gpla, models=plate,
                           overlay={"default_jerk": "9", "enable_support": "1",
                                    "enable_pressure_advance": "1"})
        v["toolchanger"] = dict(settings=tc, filaments=str(f / "Generic PLA @MyToolChanger.json"),
                                models=plate, overlay={"default_jerk": "9", "enable_support": "1"})
        v["mf_klipper"] = dict(settings=klip, filaments=";".join([gpla, gcf] * 2),
                               models=[proj / "klipper_multicolor.3mf"],
                               overlay={"enable_support": "1",
                                        "single_extruder_multi_material": "1"})
        v["mf_toolchanger"] = dict(
            settings=tc,
            filaments=";".join([str(f / "Generic PLA @MyToolChanger.json"),
                                str(f / "Generic PETG @MyToolChanger.json")] * 2
                               + [str(f / "Generic PLA @MyToolChanger.json")]),
            models=[proj / "toolchanger_4_color.3mf"], overlay={"enable_support": "1"})
    return v


def route(key, available):
    """Cheapest variant measured to show this option, or 'cube' as the floor."""
    table = json.loads(ROUTING.read_text())["routing"]
    name = table.get(key, "cube")
    if name == "bed":                       # one plate per bed type
        base = key.replace("_initial_layer", "").replace("_plate_temp", "")
        name = f"bed_{base}"
    return name if name in available else "cube"
