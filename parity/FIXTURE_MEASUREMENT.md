# Fixture set: measured unlock count

Measured 2026-09-03/04 against `RelWithDebInfo` at OrcaSlicer b81c0e30c1.
~1000 slices across eleven passes, 3 workers (a single slice already saturates
~12 of 32 cores through TBB, so more processes buy sublinear throughput).
Every option is re-probed against its own fixture's merged baseline -- the
current sweep probes against the Bunny 3mf but slices the cube, which silently
no-ops 28 options without ever testing them.

**327 of the 450 options the cube-based effect stage calls inert change the
G-code under these fixtures (72%). 76 remain inert; 47 cannot move a
comment-free stream at all.**

| fixture | model | unlocks |
|---|---|---|
| torture plate, 12 baselines | `fixtures/torture.stl` | 172 |
| multi-filament (3 printers) | `test_projects/*multicolor*.3mf` | 88 |
| Klipper / Marlin / toolchanger | torture + flattened Custom presets | 19 flavor + 12 jerk |
| bed types (6 plates) | torture, one `curr_bed_type` each | 10 |
| classic wall generator | torture, `wall_generator=classic` | 4 (gap fill) |
| by-object / draft shield | `fixtures/cube_{a,b}.stl` 80 mm apart | 2 |
| spiral vase | `fixtures/vase.stl` | 3 |

## Per family

| family | effective | still inert | of |
|---|---|---|---|
| multi-filament | 83 | 30 | 113 |
| supports | 44 | 7 | 51 |
| dead/unobservable | 0 | 47 | 47 |
| fine-feature geometry | 29 | 5 | 34 |
| false-inert | 23 | 5 | 28 |
| overhang+bridge geometry | 19 | 4 | 23 |
| printer/flavor | 19 | 2 | 21 |
| UNASSIGNED | 18 | 2 | 20 |
| switch: infill variant | 18 | 1 | 19 |
| switch: fuzzy skin | 11 | 1 | 12 |
| switch: scarf seam | 8 | 3 | 11 |
| switch: bed type / plate temps | 10 | 0 | 10 |
| switch: retraction/wipe variant | 4 | 5 | 9 |
| switch: cooling / aux fan | 4 | 4 | 8 |
| switch: ironing | 8 | 0 | 8 |
| switch: chamber/air filtration | 7 | 1 | 8 |
| switch: brim | 8 | 0 | 8 |
| multi-object plate | 4 | 3 | 7 |
| switch: spiral vase | 3 | 1 | 4 |
| switch: zaa | 4 | 0 | 4 |
| project: custom gcode markers | 2 | 0 | 2 |
| switch: adaptive layer height | 0 | 2 | 2 |
| switch: pressure advance | 1 | 0 | 1 |

## Companion switches that gate whole families

Each of these is a single flag that has to be on in the *baseline* before an
entire group of options can do anything. Finding them was worth more than any
mesh:

| switch | unlocks |
|---|---|
| `set_other_flow_ratios=1` | every `*_flow_ratio` |
| `default_jerk>0` (X1C ships 0) | all 7 `*_jerk` |
| `wall_generator=classic` | gap fill -- Arachne widens extrusions instead of leaving slivers, so the plate produces none by default |
| `interlocking_beam=1` | the 4 interlocking-beam options |
| `wipe_tower_wall_type=rib` | `wipe_tower_rib_width`, `wipe_tower_fillet_wall` |
| `draft_shield=enabled` | `single_loop_draft_shield` |
| `input_shaping_emit=1` + Klipper | `adaptive_bed_mesh_margin` |
| `overhang_reverse=1` | `overhang_reverse_threshold`, `..._internal_only` |

Note `adaptive_pressure_advance` must NOT be on: it overrides the static
`pressure_advance` per extrusion, so a kitchen-sink baseline hides it.

## Probe bugs, not fixture gaps

The standard `choose_value` produced unusable probes for a whole class of
options. These changed nothing until the probe was fixed, with no change to
any model:

* **Out of the option's own range** -- `infill_overhang_angle` has min 15 and
  the probe asked for 5.
* **Wrong type** -- `extra_solid_infills` is a layer spec (`"5"`, `"1,7,9"`),
  not the `probe_<key>` token the sweep sends; `center_of_surface_pattern` is
  an enum (`each_surface`/`each_model`/`each_assembly`).
* **Defaults where 0 means "inherit"** -- the six `*_filament_id` options
  default to 0, so the 0->1 probe asks for the filament the object already
  uses. Probing 2 turned all six effective at once.
* **Speeds above the volumetric clamp** -- `inner_wall_speed` 300->450 clamps
  to the same number; the probe has to go down, not up.

## The multi-filament fixture

Do not synthesise this plate. `--export-3mf` writes `filament_settings_id`
with every loaded filament but leaves `filament_colour`, `filament_type` and
`filament_map` at length 1, so a CLI-authored project counts as
single-filament on reload: one filament in the print, no tool changes, and the
prime tower forced off (`OrcaSlicer.cpp:3699`, "disable prime tower for only
one filament"). Per-object `extruder` metadata, `--sparse-infill-filament-id`,
`--support-filament` and `--load-filament-ids` all fail to rescue it.

The committed projects carry consistent per-filament vectors and slice with
real tool changes, with or without preset overrides:

| project | printer | filaments in use |
|---|---|---|
| `test_projects/p1s_multicolor.3mf` | Bambu P1S | 1,2,3,4 (308 tool changes) |
| `test_projects/klipper_multicolor.3mf` | MyKlipper | 2,3,4,1 |
| `test_projects/toolchanger_4_color.3mf` | MyToolChanger | 2,3,4,5,1 |

`--filament-colour` with two entries is the one flag that reaches the filament
mapping directly, and it segfaults (below).

## Crashes found while building the fixtures

Both are SIGSEGV (exit 139), reproducible, on RelWithDebInfo b81c0e30c1.

**1. `--filament-colour` with two colours and two filaments.** No supports, no
prime tower, single STL:

    orca-slicer --datadir <seed> --outputdir out --allow-newer-file \
      --load-settings "<X1C machine>;<0.20mm Standard @BBL X1C>" \
      --load-filaments "<Bambu PLA Basic>;<Bambu PLA Matte>" \
      --filament-colour="#FF0000,#0000FF" --slice 0 torture.stl

`filament_colour` is in the override sweep's SKIP_KEYS, so the sweep never
probes it and never saw this.

**2. Klipper profile + two filaments + prime tower**, with no `filament_colour`
override and without `single_extruder_multi_material`:

    Thread 1 "orcaslicer_main" received signal SIGSEGV
    #0 Slic3r::WipeTower2::prime (...) at src/libslic3r/GCode/WipeTower2.cpp:1256
    #1 Slic3r::Print::_make_wipe_tower (...) at src/libslic3r/Print.cpp:4359
    #2 Slic3r::Print::process (...) at src/libslic3r/Print.cpp:2584

Line 1256 is `toolchange_Wipe(writer, cleaning_box,
wipe_volumes[tools[idx_tool-1]][tool], false, true)` inside the
`idx_tool + 1 == tools.size()` branch: with `tools.size() == 1` that indexes
`tools[SIZE_MAX]`. `Print.cpp:4329` builds `wipe_volumes` by slicing
`flush_volumes_matrix` into `number_of_extruders` rows without checking the
matrix is that big, which is the other candidate for the out-of-bounds read.
The Bambu wipe-tower path is unaffected (`WipeTower2` is the non-BBL variant).

## Other harness fixes this needed

* **Custom-vendor presets must be flattened.** The CLI reads one preset file
  and never walks `inherits`, and takes `compatible_printers` from the leaf
  alone, so `0.20mm Standard @MyMarlin` (whose list lives in
  `fdm_process_marlin_common`) fails the compat gate. `make_custom_presets.py`
  produces usable copies; before it, every Klipper/Marlin option was scored
  inert against a baseline that never loaded.
* **`flatten_preset.py` cannot resolve `OrcaFilamentLibrary/filament/base/`**
  presets: anything inheriting `fdm_filament_pla_silk` exits "cannot resolve
  filament preset".
* **Object XY offsets do not survive the 3mf round-trip.** Two STLs authored
  95 mm apart come back centred on the same spot (plate bbox collapses to one
  footprint) and their G-code paths collide; `--arrange 1` does not separate
  them either; the `<item transform>` in `3D/3dmodel.model` is what actually
  moves an object. Direct STL inputs keep their file coordinates, which is why
  `cube_a.stl`/`cube_b.stl` are authored 80 mm apart rather than arranged.
* **A kitchen-sink baseline can hide the option under test**:
  `adaptive_pressure_advance` overrides the static `pressure_advance` value, so
  PA read as inert until it moved to its own variant.
* **`default_jerk > 0` gates every `*_jerk` option** (`GCode.cpp:7913`) and the
  X1C profile ships 0.


## Measured: shipped models cannot replace the torture plate

`tests/data/*.obj` loads fine as CLI input and covers most of what the
synthesised plate does, so it looked like the better-provenance choice. It was
measured rather than assumed, by re-running all 179 options the torture plate
proves against a composite plate (`gaps.stl` carrying only the five features
nothing ships -- counterbore, enclosed cavity, sub-nozzle pins, stepped tower,
non-multiple height -- plus `overhang`, `bridge`, `cube_with_hole`,
`sloping_hole`, `2x20x10`, `two_hollow_squares`):

| | torture.stl | composite | + tall parts (`V`, `pyramid`) |
|---|---|---|---|
| wall / CPU per slice | **6.5 s / 77 s** | 16.5 s / 129 s | higher still |
| options covered (of 179) | **179** | 150 | 153 |
| objects on the plate | 1 | 7 | 9 |

The composite is 2.5x the cost *and* 26-29 options worse. Two reasons: nine
separate objects cost far more than one object with twelve fused parts (more
islands per layer, plus arrange and conflict checking), and the shipped models
short enough to keep the cost down (<=10 mm) generate almost no support
structure, which is where most of the loss is -- 12 support/tree/raft options,
4 bridge options. Adding the two tall shipped parts recovered only 3.

**Conclusion: keep the synthesised plate.** The shipped models remain the right
choice anywhere they are enough on their own; they are not a drop-in for this
fixture. `gaps.stl` and the composite recipe were removed rather than kept as
dead weight -- `make_torture.py` regenerates the plate, and this table records
why the alternative was rejected so it is not re-tried blind.

## Cost routing and sharding

`effect_routing.json` maps each of the 483 measured options to the cheapest
fixture that can show its effect, derived from the measurements rather than
guessed: 162 stay on the plain cube (0.6 s), 136 need the plate, 99 a different
printer or fuzzy skin, 86 a multi-filament project (55 s).

Routing is not a saving against "everything on the torture plate" -- it is more,
because the multi-filament tier is intrinsically expensive and cannot run
anywhere cheaper. It is a 2.5x saving against the only coverage-equivalent
alternative, running every option on the most capable fixture: 360 min vs
886 min serial on a 4-vCPU runner.

Sharding is what makes it fit CI. `--effect-shard I/N` selects one shard;
`effect_routing.json` carries an 8-way split balanced by measured slice cost,
keeping variants whole where possible and splitting only the oversized ones
(a shard that re-slices a variant pays its baseline again, which is the ~4%
gap between 360 and 376 min serial). Longest shard 52.9 min against the
60-minute timeout. The shards are disjoint and cover every landed option
exactly once; options with no recorded fixture are spread round-robin so a
newly added option is never silently dropped from every shard.
