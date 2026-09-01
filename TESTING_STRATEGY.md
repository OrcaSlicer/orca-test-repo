# Testing Strategy

Black-box regression suite for OrcaSlicer's CLI (headless) mode. Every test
drives an already-built `orca-slicer` binary as a subprocess and asserts on
exit codes, stdout/stderr, and produced files. Nothing here links against or
imports OrcaSlicer source.

## Layers

0. **GUI-vs-CLI parity harness** (`parity/`, run by `parity/run_parity.py`)
   -- four-lane comparison (GUI headless under Xvfb, CLI, and both
   round-trips) of full slicing runs on committed fixtures, scored as
   metrics with a known-differences ledger; never gates. Needs an X-capable
   environment and an OrcaSlicer checkout/AppImage for `resources/`
   (`ORCA_SLICER_ROOT`); see `parity/README.md`.


1. **Declarative regression cases** (`cases/**/*.yaml`, run by
   `test_cases.py`) -- fast, targeted checks that a known *class* of input
   keeps behaving correctly: crash resistance under crafted arguments
   (`cases/crash/`), CLI parsing and `--help` correctness
   (`cases/cli-parsing/`).
2. **Parity and bounds cases** (same runner) -- CLI-vs-GUI config precedence
   and clamping parity (`cases/config-precedence/`) and bed-shape /
   array-length bounds correctness (`cases/geometry-bounds/`). Most of these
   need bespoke fixture `.3mf`/profile files and are tracked as
   `needs_fixture` until those exist.
3. **Golden-slice checks** (`test_golden_slices.py`) -- every `.3mf` in
   `test_projects/` must slice successfully and reproduce the slicing
   *metrics* recorded in `baseline.json` (layer count, max Z, filament used
   per extruder, sum of E moves, print-time estimate, size -- each with its
   own tolerance, see `gcode_metrics.py`). A whole-pipeline safety net that
   catches output drift no targeted case covers, including "same size,
   different print". See "Golden-slice layer" below.
8. **Per-object settings** (`cases/per-object/`) -- settings embedded in a
   3mf at object level (`model_settings.config`) and per layer range
   (`layer_config_ranges.xml`) are applied, take precedence over CLI
   overrides, and survive `--export-3mf`; asserted through slicing metrics
   since the plate config block only records global values.
4. **Config-surface fuzzing** (`test_config_fuzz.py`) -- sweeps the real CLI
   option surface (sourced from OrcaSlicer's source, not `--help` -- see
   below) with type-appropriate poison values, asserting only that the
   binary doesn't crash. Discovery, not regression-pinning: confirmed
   findings graduate into a proper case in layer 1/2.
5. **Settings import / export matrix** (`test_settings_import.py` +
   `cases/settings-import/`) -- every way of getting settings *into* a slice
   (3mf embedded config, `--load-settings` machine/process presets,
   `--load-filaments`, CLI `--option=value` overrides, `--uptodate`) crossed
   with every way they come *out* (G-code config block, exported 3mf, JSON
   from `--export-settings`), verifying the recorded settings are the imported
   ones. See "Settings import / export" below.
6. **Config-option override sweep** (`test_cli_overrides.py`) -- *every* one
   of the ~800 print/printer/filament options is set on the command line with
   a valid, distinctive value and must land in the merged config and in the
   G-code of a real slice (proving CLI > preset > 3mf precedence per key).
   See "Override sweep" below.
7. **CLI flag behaviour** (`cases/cli-flags/`) -- the CLI-only action,
   transform and misc flags (`--scale`, `--rotate-*`, `--export-stl`,
   `--min-save`, `--mtcpp`, ...) each with an observable-effect assertion.
   See "CLI flags" below.

## Case file schema

Each YAML file under `cases/<category>/` is one case:

```yaml
id: outputdir-missing-parent-directory   # unique, kebab-case, matches filename
title: "one-line statement of the required behavior"
category: crash          # also becomes a pytest marker (crash, cli_parsing, ...)
status: fixed            # fixed | open | needs_fixture
model: Stanford_Bunny.3mf  # optional; resolved from test_projects/
args: ["--datadir", "{datadir}", ...]   # placeholders: {datadir}, {empty_datadir},
                                        # {outputdir}, {scratch}, {cwd}, {model}
checks:                  # composable assertions from checks.py's CHECKS registry
  - no_crash: {}
  - exit_code: {equals: 253}
notes: >                 # the mechanism being pinned, in general terms
fixture_todo: >          # needs_fixture only: what fixture is missing and why
timeout: 300             # optional, seconds
```

### Status model and the strict-xfail gate

- **fixed** -- must pass. A failure is a real regression in the build under test.
- **open** -- a known, currently-reproducing bug. The case is collected and
  run, but marked `xfail(strict=True)` (see `conftest.py`), so it doesn't
  block CI while the bug exists upstream.
- **needs_fixture** -- written but blocked on a missing fixture file. Collected
  (visible in `--collect-only`, i.e. a tracked TODO) but skipped.

The strict-xfail gate only works if an `open` case's checks assert the
**desired/correct** behavior, not the current broken behavior. Then, the day
upstream fixes the bug, the case unexpectedly passes, pytest turns that XPASS
into a hard failure (`xfail_strict = true` in `pytest.ini`), and whoever sees
it flips `status: open` to `fixed` -- the suite can never silently drift out
of date. **Getting this backwards -- writing checks that assert the current
broken behavior -- makes the case trivially "fail as expected" forever and
silently defeats the mechanism.** (This exact mistake was made and caught
while building the suite: an `--help` coverage case briefly asserted the
current broken flag count, which would have passed meaninglessly. When writing
any `open` case, ask: "will this check *pass* once the bug is fixed?")

## Adding a new case

For the common path, no Python is needed:

1. Drop a new `.yaml` in the right `cases/<category>/` directory, following
   the schema above. Use a descriptive kebab-case `id` equal to the filename.
2. Pick `status` honestly: `fixed` if the correct behavior reproduces on the
   current build, `open` if the bug still reproduces (and write the checks
   for the *desired* behavior), `needs_fixture` if a supporting file is
   missing (describe it in `fixture_todo`).
3. Run the suite; the case is picked up automatically by `case_registry.py`.

Only extend Python when a new *kind* of assertion is needed: add one function
in `checks.py` and register it in `CHECKS`.

## Golden-slice layer

`test_golden_slices.py` slices every `test_projects/*.3mf` and compares the
metrics OrcaSlicer writes into the G-code against `baseline.json`
(`gcode_metrics.py`): `layers` and `max_z_mm` exact, `filament_mm/cm3/g` per
extruder and `e_sum_mm` (sum of positive E moves; deterministic per slice but
*not* equal to "filament used", which is the slicer's own estimate and
excludes e.g. the printer's start-G-code purge) within 2 %, `print_time_s`
within 5 %, `size_bytes` within 10 %. The baseline records which build each
model's metrics were captured on (v2.4.2 today); a legacy size-only
`baseline.json` is still understood. **Drift does not fail the run**: a
slice that errors, writes no G-code or fails the validator is a failure, but
metrics outside tolerance are printed as a readable `[golden]` warning
(and appear in pytest's warnings summary) naming the metric, the expected
and actual values, and the build the baseline came from -- a different build
is expected to differ, and the judgement is a human's. To add a model or
regenerate baselines after a deliberate, reviewed slicing-output change:

```
python scripts/update_baseline.py /path/to/orca-slicer [--only Model.3mf]
```

Never regenerate to silence a failure you haven't investigated. A gcode
content validator (`bin/orca_gcode_validator`) additionally runs on an
opt-in allowlist of models (`GCODE_VALIDATED_MODELS`) because its output has
false positives; vet a model's validator output by hand before adding it.

## Full CLI-surface enumeration and config fuzzing

There is deliberately no drift detector built on `--help`'s output: `--help`
only documents a small subset of what the CLI actually accepts (~53 flags on
the current build, against ~896 real options -- see below), so a detector
built on it would create false confidence about coverage of the undocumented
majority. Full CLI-surface awareness instead comes from reading the source
directly. (OrcaSlicer would still benefit from a `--help-all`-style flag
that enumerates every accepted option including print/printer/filament
settings -- that would let this awareness live in one place, checked at
test time against the actual binary, instead of requiring a separate
source checkout to regenerate a snapshot from.)

**Where the surface comes from at run time (`surface.py`).** Every consumer
(the G-code parser's type map, the fuzz and override sweeps, the GUI-guard's
action list) reads one loader. If an OrcaSlicer source checkout is available
-- `--orca-source PATH`, `$ORCA_SOURCE`, or auto-detected by `run_test.py`
from `$GITHUB_WORKSPACE` or the binary's ancestor directories, which covers
OrcaSlicer's own CI and any local build -- the surface is **enumerated live
from that tree**, so a build is always matched with the option definitions it
was compiled from and no snapshot maintenance is needed. Without a source
tree the committed `cases/_snapshots/cli_surface_full.json` is used. This
repo's scheduled `refresh-surface` job regenerates it from upstream `main`
and opens a PR with the added/removed/changed keys -- that is the snapshot's
maintenance loop. The snapshot also
carries the source's `*_options_with_variant` sets, so the settings comparator
learns new variant-expanded keys without a code change.

**Exact coverage listing.** `scripts/override_coverage.py` turns the last
sweep report (`.pytest_cache/override_report.json`) into
`cases/_snapshots/override_coverage.md`: every option in the surface in
exactly one section -- tested (landed in merge and G-code), rejected at
merge or slice, deliberately not swept, `nocli`, or skipped with the reason.
Regenerate it after a sweep on the reference build.

`scripts/enumerate_cli_options.py --orca-source /path/to/OrcaSlicer` reads
`src/libslic3r/PrintConfig.cpp` directly (walking the 4 ConfigDef classes
that actually feed `DynamicPrintAndCLIConfig` -- `PrintConfigDef`,
`CLIActionsConfigDef`, `CLITransformConfigDef`, `CLIMiscConfigDef` --
deliberately excluding sibling classes like `ReadOnlySlicingStatesConfigDef`
that define G-code *placeholder-parser* variables such as `{zhop}`, not CLI
flags) and emits every option's key, CLI flag spelling, declared type, and
whether it's `nocli`-excluded. This is the real surface (896 options as of
the checkout last enumerated against), independent of what `--help` chooses
to document. Output: `cases/_snapshots/cli_surface_full.json`. A handful of
options use a computed key (e.g. a loop over X/Y/Z/E axes) and can't be
recovered by the line-oriented scan; these are resolved once by hand in the
script's `MANUAL_OVERRIDES` and the script warns (not silently drops) about
any others of this shape it finds, so a source change that adds a new one
doesn't go unnoticed.

`test_config_fuzz.py` uses that enumeration to sweep CLI options with
type-appropriate "poison" values (`nil`/empty for vector types, negative/
huge/non-numeric for scalars, invalid literals for bools/enums, ...) and
asserts only `no_crash` -- it doesn't know what *correct* behavior looks
like for 850+ options, only that a clean usage error is fine and a
SIGSEGV/SIGABRT is not. This is deliberately a different kind of layer than
`cases/*.yaml`: coverage comes from the type system, not from a human
having hypothesized a problem with any specific option. By default it runs
a small (25), daily-rotating-but-reproducible-within-a-day sample so
routine runs stay fast; `--fuzz-full` runs the real sweep (hundreds of
options, several minutes+), meant for a scheduled job, not every commit.

**Confirmed working, not hypothetical:** the very first default-sample run
found a real, previously-unknown crash -- `--filament-multitool-ramming-flow=nil`
aborts with the same "Deserializing nil into a non-nullable object" failure
as `cases/crash/nil-value-for-vector-option.yaml`, on an option nobody had
tested by hand. That's the mechanism this layer exists for: it turned a
single hand-verified instance of a bug class into automatic, ongoing
coverage across the whole type-matching surface.

**Workflow when the fuzzer finds something:** a fuzz failure is a lead, not
a finished case. Isolate it by hand (a matching-value control, a minimal
repro) and graduate it into a proper `cases/*.yaml` entry with real notes --
the way `load-filaments-count-exceeds-project` and
`mismatched-extruder-geometry-arrays` were built this session. Don't leave
a confirmed finding living only as a recurring fuzz failure; a named case is
what makes it a tracked regression instead of something someone has to
rediscover.

## Settings import / export

The CLI records the settings it used in three places, and the whole point of
this layer is that they must (a) be the settings that were imported and (b)
agree with each other:

| artifact | produced by | what it holds |
|---|---|---|
| `plate_N.gcode` `; CONFIG_BLOCK_START` .. `; CONFIG_BLOCK_END` | `--slice` | the config the slicer **applied** (`print.full_print_config()` minus a small banned set, plus `first_layer_*`/`bed_shape` extras), one `; key = value` line per option in libslic3r's own serialization |
| `Metadata/plate_N.gcode` + `Metadata/project_settings.config` inside the 3mf | `--export-3mf` | the same G-code block (byte-identical) plus the **merged** config as JSON |
| `settings.json` | `--export-settings` | the merged config as JSON, identical to `project_settings.config` except `version` |

`settings_compare.py` parses both encodings type-aware (option types come
from `cases/_snapshots/cli_surface_full.json`): scalar strings are C-style
unescaped, string vectors are `;`-separated with quoting, numeric vectors
`,`-separated. The merged JSON can hold *N filaments x V extruder variants*
elements for the ~150 keys in libslic3r's `*_options_with_variant` sets
where the G-code holds one element per filament, so for those the G-code
vector is matched as an ordered subsequence of the JSON one. After that,
exactly three keys still differ for slicer-internal reasons and are
allow-listed in `settings_compare.KNOWN_DIVERGENT` (`flush_volumes_matrix`
is rescaled by `flush_multiplier` when written to G-code, `extruder_colour`
is GUI-managed, `independent_support_layer_height` is normalized at apply
time). Any *new* divergence fails the matrix.

`test_settings_import.py` runs one recipe per import path (13 today: 3mf
embedded only; 3mf/STL + system machine+process+filament; filament-only;
CLI overrides; user process; the 3mf < process < CLI precedence chain; user
machine; flattened user filament on STL and 3mf; 4-filament ordered load;
partial 2-of-4 load; `--uptodate`), slicing and exporting through all three
paths, then checks: exit 0 and all artifacts present; every imported probe
value (read from the fixture preset files themselves, so a new key in a
fixture is automatically covered) appears in the G-code block; the identity
keys name the loaded presets; the three artifacts agree; and re-importing the
exported 3mf and exporting its settings reproduces the same JSON. Two more
tests pin the *format* using GUI-produced reference artifacts checked in
under `test_projects/settings/` (`reference_plate_1.gcode`,
`gcode_only_export.3mf`), so the parser is validated against real GUI output
without needing a GUI at test time.

`cases/settings-import/` holds the clean-error contracts and known bugs of
this area as ordinary YAML cases: process/printer incompatibility (exit 239),
bare STL with nothing loaded (205), `from: project` JSON rejected by
`--load-settings` (251), a G-code-only 3mf rejected as input (250), and three
`open` bugs found while building this layer -- a sparse user filament preset
with `filament_colour` segfaults on STL input; a full-length per-filament
override on a 4-filament project lands as `211,213,0,0`; a single-value one
as `211,0,0,0` (variant-stride reads of the override vector).

What the CLI accepts, as established empirically on the v2.4.2 build (these
are the rules the fixtures follow; see the `test_settings_import.py`
docstring):

- a preset JSON needs `type` (machine|process|filament), `from` (`system` or
  `User`) and `name`; `name` becomes `printer_settings_id` /
  `print_settings_id` / `filament_settings_id`;
- a process preset is accepted only if its `compatible_printers` contains
  the printer's **system** name (a user machine's `inherits`); the 3mf's own
  process is re-checked the same way when a machine is swapped;
- only keys **explicitly present** in a loaded preset are applied -- the
  CLI resolves `inherits` from a `resources/profiles/BBL/*_full` preset cache
  that neither the release AppImage nor a plain build ships, so probe keys
  must be explicit and filaments should be *flattened* full presets (what the
  GUI's "export preset" writes);
- `--load-filaments` with fewer files than the project's filament count
  replaces the first N and keeps the rest; more files than filaments is a
  separate open crash (`cases/geometry-bounds/load-filaments-count-exceeds-project.yaml`);
- `--export-settings` output and `project_settings.config` are `from:
  project` files and cannot be reloaded with `--load-settings`; the round
  trip that works is `--export-3mf` -> re-import;
- `--export-gcode` is disabled in the source; G-code export is `--slice`;
- an *invalid enum value* in a loaded preset (e.g. `seam_position: rear` --
  the real value is `back`) is silently substituted by the option's default
  and only mentioned in the debug log; the slice succeeds with the default.
  Probe values in fixtures must therefore be valid enum keys, and a probe
  that "lands" as the default is a fixture mistake before it is a bug.

This layer does **not** validate G-code instructions, only the recorded
settings. Nor can it compare against the GUI's *derived* behaviour (see the
CLI-vs-GUI discussion under "Reference build"); it pins that the import
paths are faithful and mutually consistent.

## Override sweep -- every config option from the command line

`test_cli_overrides.py` takes the enumerated surface
(`cases/_snapshots/cli_surface_full.json`, which now also records each
option's `min`/`max`, enum keys, default and nullability, with commented-out
definitions excluded), keeps the ~800 `PrintConfigDef` options that are not
`nocli` and not structural/identity keys (bed shape, preset names, printer
model, network hosts, flush matrices -- listed in `SKIP_KEYS`), and for each
picks a **valid value that differs from the merged baseline** (numeric:
scaled/shifted inside `[min, max]`; percent likewise; bool flipped; enum: the
next key; strings: `probe_<key>`; vectors element-wise, keeping length).

Two stages, both against a baseline of `Stanford_Bunny.3mf` + system X1C
machine/process/filament presets, so a landed value proves
**CLI override > `--load-settings` preset > 3mf embedded** for that key:

- *merge*: options sent 40 per invocation with `--export-settings` (no
  slicing, ~0.3 s each); the value must appear in the merged JSON;
- *gcode*: the options that landed are sent 40 per invocation with
  `--slice 0` on `cube20.stl` + presets; the value must appear in the G-code
  config block (options the dump deliberately omits count as covered by the
  merge stage).

A batch that fails is bisected to the responsible option(s). Every option
ends in one bucket: **landed**; **rejected** (non-zero exit with an error --
allowed, printed, since refusing a probe value is legitimate validation);
**silent_drop** (exit 0 but the value is not the one given) -- **fails**;
**crash** or **hang** (past a per-invocation timeout) -- **fails**. Skipped
options (dynamic enums, nullable `nil` elements, empty vectors, keys absent
from the baseline) are printed too. The full report is written to the
session's `overrides0/override_report.json`. Run just this layer with
`-m cli_overrides`; it takes ~10 s (107 invocations) on the reference build.

Result on the v2.4.2 reference build: 549 options probed (178 skipped, 154
of them because they are not part of the merged config at all -- SLA and
GUI-only keys); **541 landed in the merged config and 538 of those in the
G-code of a real slice, none silently dropped**. Rejections, all
validation: `spiral_mode`, `use_firmware_retraction`,
`default_junction_deviation`, `machine_max_junction_deviation`,
`wall_maximum_resolution` at merge; `bridge_line_width`,
`filament_printable`, `other_layers_print_sequence_nums` at slice time.
Building the sweep also surfaced two bugs now pinned as cases: an empty
value for any point-list option makes the CLI block indefinitely **when a
display is available** (headless it exits 1 with usage text, which the
suite -- always headless -- pins in
`cases/crash/empty-points-option-value-hangs.yaml`), and `--export-settings`
into a directory that does not exist exits 0 without writing anything
(`cases/settings-import/export-settings-into-missing-dir-silently-noop.yaml`).

## Per-object settings

`cases/per-object/` covers settings that live in the 3mf rather than in the
plate config: object-level overrides (`<metadata key="<opt_key>" …/>`
under `<object>` in `Metadata/model_settings.config`) and per-layer-range
overrides (`Metadata/layer_config_ranges.xml`, `<objects><object id=N
(1-based)><range min_z max_z><option opt_key=…>value</option>`). Because the
G-code config block only records the *global* values, these are asserted
through slicing metrics: the cube fixture with an object `layer_height=0.28`
slices to 72 layers instead of 100 and to 2201 mm of filament (5 walls,
37 % infill) instead of 1405; a 0–10 mm range at 0.3 mm gives 84 layers.
Also pinned: **object config beats a CLI override of the same key**
(`--layer-height=0.16` changes the recorded global but the object still
slices at 0.28), and both kinds of override survive `--export-3mf`.
Fixtures were built from `cube_x1c.3mf` (the cube exported with the X1C
presets) by patching/adding the XML members with `scripts/patch_3mf.py`.
Not covered yet: modifier parts (`subtype="modifier_part"`, which need a
second mesh), painted seams/supports, and per-instance transforms.

## Custom G-code

The custom G-code options (`machine_start/end_gcode`, `layer_change_gcode`,
`before_layer_change_gcode`, `time_lapse_gcode`, `change_filament_gcode`,
`filament_start/end_gcode`, `template_custom_gcode`, `machine_pause_gcode`,
…) are covered twice. The override sweep proves each one, set on the command
line, is *recorded* in the merged config and the G-code CONFIG_BLOCK (20 such
options). `cases/custom-gcode/` then proves the snippet is actually
*emitted* in the G-code body, with the `gcode_body_count` check (which
excludes the settings dump from the count): start and end exactly once,
layer-change / before-layer-change / timelapse exactly once per layer (100
on the 20 mm cube at 0.2 mm), `change_filament_gcode` and
`filament_start_gcode` at the tool changes of the 4-filament project,
`filament_start_gcode` and `filament_end_gcode` once each on a
single-filament slice, and -- through `--load-custom-gcodes` with the
fixture `test_projects/settings/custom_gcodes_template_pause_custom.json`
(the `CustomGCode::Info` JSON: `mode` + `gcodes[]` of
`{type, print_z, color, extruder, extra}`) -- `template_custom_gcode` at a
Template item's layer, `machine_pause_gcode` at a PausePrint item's layer,
and a Custom item's own `extra` text at its layer, each exactly once at the
requested Z. That last case is also the behavioural test for the
`--load-custom-gcodes` flag. (Why `filament_end_gcode` first looked
un-emitted: the shipped Bambu PLA preset only carries it through `inherits`,
which the CLI does not resolve, so the merged value was empty -- the case
supplies its own snippet.)

One gotcha worth knowing when writing such a case: the per-filament G-code
options are string *vectors* whose CLI elements are `;`-separated, so a
G-code comment must be passed double-quoted --
`--filament-start-gcode='"; TEXT"'`. Unquoted, `; TEXT` becomes an empty
first element plus `TEXT`, and filament 1 emits nothing, which looks
exactly like the override being ignored (it cost an hour to see that this
was the harness, not the slicer).

## CLI flags

`cases/cli-flags/` covers the flags that are not config options. The live
set on the current source is 17 actions, 10 transforms and 26 misc flags
(`--center`, `--copy`, `--split`, `--scale-to-fit`, `--cut`, `--repair`,
`--align-xy`, `--duplicate-grid`, `--export-gcode/-obj/-amf/-svg`,
`--help-fff/-sla`, `--output` are commented out in `PrintConfig.cpp` and
correctly rejected with "setup params error"; the enumerator skips them).

| flag | case | what is asserted |
|---|---|---|
| `--scale`, `--rotate`, `--rotate-x`, `--rotate-y` | `transform-*` | exact `--info` extents on cube20.stl (a plain STL/mesh input, e.g. `--scale 2` -> 40 x 40 x 40); segfaults on v2.4.2, deterministic, must not crash on this input |
| `--orient`, `--convert-unit` | `transform-*` | clean completion (no observable bbox change on this model -- weak, noted) |
| `--ensure-on-bed` | `transform-ensure-on-bed` | slices Stanford_Bunny.3mf (mesh min_z = -59.21) and checks every G0/G1 move's Z stays >= 0 (`gcode_z_above_bed`) |
| `--arrange` + `--allow-rotations`, `--assemble` | `arrange-with-allow-rotations`, `assemble-export` | exit 0 + exported 3mf |
| `--export-stl` | `export-stl` | writes `stl/obj_1_<name>.stl` under `--outputdir` |
| `--export-3mf` + `--makerlab-name/-version`, `--metadata-name/-value` | `export-3mf-metadata-flags` | `<metadata>` elements in `3D/3dmodel.model` |
| `--min-save` | `export-3mf-min-save` | 3mf has no `3D/Objects/` mesh member, keeps `project_settings.config` |
| `--export-slicedata` | `export-slicedata` | exit 0 + G-code (location of the data dir not yet asserted) |
| `--mtcpp`, `--mstpp` | `limit-*-exceeded` | exit 197/-59 and 198/-58 with `result.json` |
| `--no-check`, `--normative-check` | `*-slices` | exit 0 + G-code |
| `--debug` + `--logfile` | `logfile-and-debug-level` | log created where asked, no stray `00000.log` |
| `--load-defaultfila` | `load-defaultfila` (open) | slices but `filament_settings_id` stays empty on v2.4.2 |
| `--slice`, `--info`, `--export-3mf`, `--export-settings`, `--uptodate`, `--load-settings`, `--load-filaments`, `--allow-newer-file`, `--allow-mix-temp`, `--datadir`, `--outputdir` | every other layer | exercised throughout |

**Not yet covered** (semantics need a fixture or two-step flow):
`--load-slicedata`, `--export-stls`, `--repetitions` and `--clone-objects`
(reject with -2 on the models tried), `--skip-objects` (needs an object id
that matches), `--cut-x/-y/-grid`, `--load-filament-ids`,
`--load-assemble-list`, `--downward-check/-settings`,
`--uptodate-settings/-filaments`, `--config-compatibility`,
`--ignore-nonexistent-config`, `--single-instance`, `--autosave`,
`--sw-renderer`, `--allow-multicolor-oneplate`, `--avoid-extrusion-cali-region`,
`--skip-modified-gcodes`, `--enable-timelapse` (no visible effect on the
models tried), `--pipe`, `--help`.

Removed as non-functional: the `--help` footer spelling case and the
"newer 3mf error message wording" case (cosmetic text, not behaviour).

## Already integrated into OrcaSlicer's CI

OrcaSlicer's own `build_orca.yml` workflow (`Run external slicer regression
tests` step, Linux/x86_64 leg) already clones this repo and runs it against
*that build's own freshly compiled binary*, on every push and PR:

```bash
git clone --depth 1 https://github.com/OrcaSlicer/orca-test-repo.git "$dir"
python3 "$dir/run_test.py" "$workspace/build/package/bin/orca-slicer"
```

`run_test.py` used to be this repo's entire test script; it's now kept as a
thin, dependency-free **compatibility entry point** with that exact
single-argument CLI shape, so that integration keeps working unmodified. It
sets up an isolated `.venv` on first run, installs `requirements.txt` into
it, and delegates to the real pytest suite (`python -m pytest . -c
pytest.ini --orca-bin <bin>`) -- functionally identical to running
`./run_tests.sh <bin>` by hand. This is the **primary** way the suite runs
in practice: against a real build of whatever commit/PR triggered CI, not
against a fixed release snapshot.

## Reference build

Each case's `status` (`fixed`/`open`) is a claim about a *specific* binary,
not about OrcaSlicer in the abstract. Because the suite runs continuously
against real OrcaSlicer builds via the integration above, a case's `status`
is expected to occasionally fall out of date the moment a PR happens to fix
(or regress) the thing it pins -- that's exactly what the strict-xfail gate
exists to catch, not a design flaw. When this suite goes red inside
OrcaSlicer's CI, check *which* kind of red it is before assuming a
regression:

- a plain assertion failure on a `fixed` case -- a real regression, the
  build under test broke something that used to work;
- an `[XPASS(strict)]` failure on an `open` case -- *good* news reported as
  a failure: that build's behavior now matches the case's desired-behavior
  checks, i.e. the bug got fixed. Update that case's `status` to `fixed`
  (and tighten/confirm its checks) rather than treating it as a defect.

The cases in this repo were authored and status-verified against the public
**v2.4.2** release build (the `Ubuntu2404` Linux AppImage) as a fixed,
reproducible baseline for anyone reading this repo in isolation -- e.g. this
repo's own `regression` CI job (below) uses that same reference build. When
run inside OrcaSlicer's CI against main/a PR branch, expect some drift from
that baseline in either direction as the codebase moves; that drift is the
signal, and the fix is to update the affected case's `status`, not to
silence or loosen it.

(This is not hypothetical: several cases in this repo were first drafted
against exit codes/behavior observed on a local development build with
in-progress patches, then re-verified against the public v2.4.2 release
during setup -- three of them turned out to still be open on v2.4.2 despite
appearing fixed on that dev build, and one open case turned out to already
be fixed on v2.4.2. Statuses here reflect the v2.4.2 verification, not the
dev build.)

## Running locally and in CI

Locally, once you have a built (or downloaded) `orca-slicer` binary:

```
./run_tests.sh /path/to/orca-slicer            # sets up .venv on first run
./run_tests.sh /path/to/orca-slicer -k crash    # pass extra pytest args through
ORCA_BIN=/path/to/orca-slicer ./run_tests.sh    # binary via env instead
```

`python3 run_test.py /path/to/orca-slicer` also works, with the same
first-run venv setup -- it's the single-argument compatibility entry point
described above under "Already integrated into OrcaSlicer's CI"; prefer
`run_tests.sh` for anything interactive since it passes extra args through.

Invoking pytest directly works the same way, with one gotcha: **always
include an explicit path argument (e.g. `.`)** alongside `--orca-bin`:

```
python -m pytest . -c pytest.ini --orca-bin /path/to/orca-slicer
```

Without that leading `.`, pytest can misfire with `unrecognized arguments:
--orca-bin`. Cause: pytest determines its rootdir (and therefore which
`conftest.py` to load, which is what registers `--orca-bin` as a valid
option in the first place) by scanning raw argv for path-like values before
it knows which options exist. `--orca-bin`'s *value* is itself a real
filesystem path, so with no other path argument present, pytest can anchor
rootdir discovery on that binary's path instead of the repo -- `conftest.py`
never loads, and `--orca-bin` comes back unrecognized. `run_tests.sh`
already does this correctly; it's only a concern when invoking `pytest`
directly by hand.

**This repo's own CI** (`.github/workflows/test.yml`) is a self-check, not
the primary regression gate -- that role belongs to OrcaSlicer's own CI (see
above), which exercises a real build of the actual commit under test. This
repo's workflow runs two jobs against the fixed public reference release
instead, so that changes to the *suite itself* get fast feedback without
waiting for an OrcaSlicer build:

- `validate-suite` -- every push/PR, no binary needed: confirms every
  `cases/**/*.yaml` file parses and the whole suite collects cleanly. This
  is the fast gate for changes to the suite itself.
- `regression` -- every push/PR, nightly on a schedule, and on-demand via
  `workflow_dispatch` (with an optional release-tag input) -- downloads the
  reference OrcaSlicer Linux AppImage release, extracts it (no FUSE needed,
  via `--appimage-extract`), and runs the full suite against it. The
  schedule exists so a new upstream release is checked even when nothing in
  this repo changed.

Both jobs currently target Linux only (matching the reference build above).
Extending to macOS/Windows runners means fetching the matching release
asset (`.dmg` / portable `.zip`) for that platform and is a documented
extension point, not yet implemented here.

## Standalone by design

This repo never links against, imports, or builds OrcaSlicer source. The only
coupling is the binary under test, supplied at run time:

```
./run_tests.sh /path/to/orca-slicer
# or: ORCA_BIN=/path/to/orca-slicer python -m pytest . -c pytest.ini
```

`data_dir/` holds checked-in printer/filament profiles used to seed each
test's private `--datadir` copy (tests never point at the repo's own
`data_dir/`, since the CLI writes machine-id/cache files into it).
`test_projects/` holds the fixture models. Everything else the tests create
lives in pytest temp directories.
