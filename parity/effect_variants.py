"""Baselines for the override sweep's effect stage.

The effect stage slices one option at a time against a baseline. On a 20 mm
cube with stock presets, 455 of the 617 landed options change nothing -- not
because the slicer ignores them, but because the baseline has their master
switch off and the cube has no geometry to act on. This module supplies the
baselines that make them observable: `torture.stl` (overhangs, a bridge,
holes incl. a counterbore, thin walls, sub-nozzle pins, a stepped tower, a
sloped top and a closed cavity) plus one overlay per feature family.

Families that are mutually exclusive here (supports normal vs tree, the infill
patterns) would be per-object config on a single 3mf plate; as separate
variants they measure the same thing and slice faster.

Measured on 2026-09-03/04 against RelWithDebInfo b81c0e30c1: 228 of the 450
options the cube reports as inert change the G-code under these baselines.
See FIXTURE_MEASUREMENT.md for the per-family numbers and what is left.

The Custom-vendor variants need flattened presets -- run
parity/make_custom_presets.py first and point PRESETS_DIR at its output.
"""

MODEL = "parity/fixtures/torture.stl"
VASE = "parity/fixtures/vase.stl"                  # spiral vase: one object, alone
TWO_CUBES = ["parity/fixtures/cube_a.stl",        # 80 mm apart: by-object sequencing
             "parity/fixtures/cube_b.stl"]        # enforces a 40 mm clearance radius

# Applied to every variant. Each entry turns on a master switch that gates a
# family of options; without it every option in that family is a no-op.
COMMON = {
    "brim_type": "brim_ears", "brim_width": "5", "elefant_foot_compensation": "0.2",
    "ironing_type": "solid",
    "seam_slope_type": "external", "has_scarf_joint_seam": "1",
    "enable_pressure_advance": "1", "pressure_advance": "0.02",
    "activate_chamber_temp_control": "1", "chamber_temperature": "40",
    "activate_air_filtration": "1",
    "set_other_flow_ratios": "1",        # gates every *_flow_ratio option
    "default_jerk": "9",                 # gates every *_jerk option (X1C ships 0)
    "zaa_enabled": "1", "auxiliary_fan": "1", "enable_long_retraction_when_cut": "1",
    "z_hop": "0.4", "wipe": "1", "infill_combination": "1",
    "slow_down_for_layer_cooling": "1",
    "hole_to_polyhole": "1", "make_overhang_printable": "1", "top_surface_expansion": "1",
    # deliberately NOT here: adaptive_pressure_advance. With it on, the static
    # pressure_advance value is overridden per extrusion and the option reads
    # as inert -- an enabled feature can mask the option under test.
}

_SUP = {"enable_support": "1", "support_ironing": "1", "support_air_filtration": "1"}

VARIANTS = {
    "switches":  {},
    "support":   {**_SUP, "support_type": "normal(auto)", "support_style": "grid",
                  "support_interface_top_layers": "3", "support_interface_bottom_layers": "3",
                  "raft_layers": "3"},
    "tree":      {**_SUP, "support_type": "tree(auto)", "support_style": "tree_slim"},
    "organic":   {**_SUP, "support_type": "tree(auto)", "support_style": "organic"},
    "gyroid":    {"sparse_infill_pattern": "gyroid"},
    "lightning": {"sparse_infill_pattern": "lightning"},
    "lockedzag": {"sparse_infill_pattern": "lockedzag", "infill_lock_depth": "2",
                  "skin_infill_depth": "2"},
    "lattice":   {"sparse_infill_pattern": "lateral-lattice"},
    "zigzag":    {"sparse_infill_pattern": "zigzag"},
    "concentric":{"top_surface_pattern": "concentric", "bottom_surface_pattern": "concentric"},
    # ~90 s/slice against ~7 s for the rest, so it is not in COMMON
    "fuzzy":     {"fuzzy_skin": "external", "fuzzy_skin_noise_type": "perlin",
                  "fuzzy_skin_mode": "combined"},
    "adaptive_pa": {"adaptive_pressure_advance": "1"},
}

# Fixtures that need their own model, printer profile or plate. Each entry is
# (settings preset(s), filament preset(s), model, overlay); "flat:" names come
# from make_custom_presets.py, "sys:" from the datadir seed.
PLATES = {
    "spiral":     ("sys:BBL/X1C", "sys:BBL/PLA", VASE,
                   {"spiral_mode": "1", "wall_loops": "1", "top_shell_layers": "0",
                    "sparse_infill_density": "0%", "enable_support": "0"}),
    "byobject":   ("sys:BBL/X1C", "sys:BBL/PLA", TWO_CUBES,
                   {"print_sequence": "by object", "skirt_loops": "2"}),
    "draftshield":("sys:BBL/X1C", "sys:BBL/PLA", MODEL,
                   {"draft_shield": "enabled", "skirt_loops": "2"}),
    "klipper":    ("flat:MyKlipper 0.4 nozzle;flat:0.20mm Standard @MyKlipper",
                   "flat:Generic PLA @System", MODEL,
                   {"default_jerk": "9", "enable_support": "1", "enable_pressure_advance": "1",
                    "auxiliary_fan": "1", "activate_chamber_temp_control": "1",
                    "chamber_temperature": "40"}),
    "marlin":     ("flat:MyMarlin 0.4 nozzle;flat:0.20mm Standard @MyMarlin",
                   "flat:Generic PLA @System", MODEL,
                   {"default_jerk": "9", "enable_support": "1", "enable_pressure_advance": "1"}),
    "toolchanger":("flat:MyToolChanger 0.4 nozzle;flat:0.20mm Standard @MyToolChanger",
                   "flat:Generic PLA @MyToolChanger", MODEL,
                   {"default_jerk": "9", "enable_support": "1"}),
}
# One plate per bed type: only the current bed's temperature pair is emitted.
BED_TYPES = {"cool": "Cool Plate", "eng": "Engineering Plate", "hot": "High Temp Plate",
             "textured": "Textured PEI Plate", "textured_cool": "Textured Cool Plate",
             "supertack": "Supertack Plate"}

# Multi-filament: use the committed multicolor projects, never a CLI-authored
# one -- --export-3mf leaves filament_colour/filament_type/filament_map at
# length 1 and the plate reloads as single-filament (FIXTURE_MEASUREMENT.md).
MULTI_FILAMENT = {
    "mf_bbl":         ("sys:BBL/P1S;sys:BBL/0.20mm Standard @BBL X1C",
                       "sys:BBL/PLA Basic;sys:BBL/PLA Matte (x2)",
                       "test_projects/p1s_multicolor.3mf",
                       {"enable_support": "1", "support_ironing": "1"}),
    "mf_klipper":     ("flat:MyKlipper 0.4 nozzle;flat:0.20mm Standard @MyKlipper",
                       "flat:Generic PLA @System;flat:Generic PLA-CF @System (x2)",
                       "test_projects/klipper_multicolor.3mf",
                       {"enable_support": "1", "single_extruder_multi_material": "1"}),
    "mf_toolchanger": ("flat:MyToolChanger 0.4 nozzle;flat:0.20mm Standard @MyToolChanger",
                       "flat:Generic PLA/PETG @MyToolChanger (x5)",
                       "test_projects/toolchanger_4_color.3mf",
                       {"enable_support": "1"}),
}
# Options are tried against these in order and stop at the first that moves the
# stream; 74 of 125 land, most of them on mf_bbl.


def plate_for(key):
    """Fixture for the options the torture plate cannot reach, or None."""
    if key.startswith("spiral_"):                                   return "spiral"
    if key in ("first_layer_print_sequence", "other_layers_print_sequence",
               "printing_by_object_gcode", "reduce_crossing_wall", "combine_brims",
               "max_travel_detour_distance", "skirt_type", "min_skirt_length"):
        return "byobject"
    if key == "single_loop_draft_shield":                           return "draftshield"
    if key.endswith("_plate_temp") or key.endswith("_plate_temp_initial_layer"):
        return "bed:" + key.replace("_initial_layer", "").replace("_plate_temp", "")
    if key.startswith(("input_shaping_", "bed_mesh_")) or key in (
            "adaptive_bed_mesh_margin", "exclude_object", "silent_mode",
            "machine_max_acceleration_travel", "gcode_add_line_number",
            "machine_pause_gcode", "template_custom_gcode", "accel_to_decel_enable",
            "accel_to_decel_factor", "resonance_avoidance", "bed_temperature_formula"):
        return "klipper"                     # fall back to "marlin" if inert
    return None


def variant_for(key):
    if key.startswith("tree_support_"):
        return "organic" if key.endswith("_organic") else "tree"
    if key.startswith(("support_", "raft_", "enforce_support_layers",
                       "independent_support_layer_height", "bridge_no_support")):
        return "support"
    if key == "gyroid_optimized":                   return "gyroid"
    if key.startswith("lightning_"):                return "lightning"
    if key.startswith(("skeleton_infill", "skin_infill", "infill_lock_depth")):
        return "lockedzag"
    if key.startswith("lateral_lattice_"):          return "lattice"
    if key.startswith(("symmetric_infill", "infill_shift_step")):
        return "zigzag"
    if key.startswith(("center_of_surface_pattern", "top_surface_fill_order",
                       "bottom_surface_fill_order")):
        return "concentric"
    if key.startswith("fuzzy_skin"):                return "fuzzy"
    if key.startswith("adaptive_pressure_advance"): return "adaptive_pa"
    return "switches"


# Options whose standard probe (c+1 / c*1.5 / next enum) cannot move the
# slicer: a threshold that no geometry crosses, or a speed raised past the
# volumetric-flow clamp so both values clamp to the same number. Probe these
# explicitly instead.
PROBE_OVERRIDES = {
    "hole_to_polyhole_threshold": "0.05", "hole_to_polyhole_max_edges": "4",
    "make_overhang_printable_angle": "20",
    "top_surface_expansion_margin": "3",
    "support_interface_top_layers": "0", "support_threshold_overlap": "90%",
    "enforce_support_layers": "10", "support_object_first_layer_gap": "1",
    "support_bottom_interface_spacing": "1.5",
    "tree_support_branch_angle": "10", "tree_support_branch_diameter": "8",
    "tree_support_branch_distance": "10", "tree_support_brim_width": "10",
    "scarf_angle_threshold": "10", "scarf_joint_speed": "20%",
    "max_bridge_length": "1", "infill_overhang_angle": "5",
    "gap_infill_speed": "10", "sparse_infill_speed": "20", "gap_fill_flow_ratio": "2",
    "brim_ears_max_angle": "30", "wall_transition_filter_deviation": "0.9",
    "full_fan_speed_layer": "30", "slow_down_layers": "40", "slow_down_min_speed": "40",
    "infill_combination_max_layer_height": "400%",
}
