# GUI-vs-CLI parity harness

Measures behavioral differences between OrcaSlicer's GUI and CLI slicing
pipelines. Lives in orca-test-repo and drives a prebuilt `orca-slicer`;
the OrcaSlicer checkout (or extracted AppImage) that provides
`resources/profiles` is passed via `--slicer-root` / `ORCA_SLICER_ROOT`. It is **metrics-only**: nothing is gated, lane failures and
divergences are recorded as data, and the run exits 0. The headline output is
the count of *new* divergences — differences not yet documented in the
`expected_differences.json` ledger.

## How it works

Each fixture (a model or project under `parity/fixtures/`) runs through up to four
lanes, all using the same binary and the same generated datadir seed:

| Lane | What it does |
|------|--------------|
| `G`  | GUI, headless under Xvfb: import → slice → export sliced 3mf + save project |
| `C`  | CLI: same input, equivalent preset files and flags |
| `R`  | CLI re-slices lane G's saved project (round-trip) |
| `RB` | GUI opens lane C's export and re-slices it (reverse round-trip) |

Comparisons: `pipeline` = C vs G, `engine` = R vs G, `gui_load` = RB vs C,
plus `settings_survival` (which config keys survive the CLI→GUI reopen).
The lane triangle classifies divergences automatically: if `engine` is clean
but `pipeline` is not, the difference lives in the load/preset/placement
paths, not the slicing engine.

## Running locally

Prerequisites: a built `orca-slicer` (`ORCA_BIN`), an OrcaSlicer checkout or
extracted AppImage for `resources/profiles` (`--slicer-root`), plus `xvfb xdotool imagemagick openbox` for the GUI
lanes. CLI-only lanes (`--lanes C`) need none of the display tooling.

```bash
export ORCA_SLICER_ROOT=/path/to/OrcaSlicer   # checkout or extracted AppImage
export ORCA_BIN=/path/to/orca-slicer
python3 parity/run_parity.py                  # all fixtures, all lanes
python3 parity/run_parity.py --fixture cube-baseline
python3 parity/run_parity.py --lanes C,G      # skip round-trips
python3 parity/run_parity.py --cli-presets flat   # same effective config both lanes
python3 parity/run_parity.py --gui-workers 2  # run 2 fixtures at once, each on its own display
```

`--gui-workers N` runs N fixtures concurrently, each on its own X display
(`--display` base, base+1, ...) with its own seed, datadirs and GUI session
— fully isolated, so results are identical to sequential. Each GUI costs
~0.3 core idle (llvmpipe repaint) plus ~1 core in launch/slice bursts and
~0.95 GB RAM; on a 4-vCPU / 16 GB CI runner 2 is comfortable, 3+ contends
(and slice slowdowns risk tripping per-lane timeouts). CI uses 2.

Outputs land in `--out` (a temp dir by default): `scorecard.json`
(machine-readable), `report.md` (human summary), and per-fixture
subdirectories with every export, comparison dump, lane log, and — for GUI
lanes — step-by-step screenshots for post-mortem.

## Files

- `run_parity.py` — orchestrator; reads `fixtures.json`, runs lanes, emits the scorecard.
- `fixtures.json` — fixture manifest (input, presets, flags, lanes per fixture).
- `expected_differences.json` — ledger of *known* GUI/CLI differences with
  their documentation reference. Curation, not suppression: known diffs are
  still counted; only undocumented ones raise the `new_divergences` number.
- `flatten_preset.py` — resolves a vendor profile's `inherits` chain into one
  self-contained JSON. `--load-settings` reads a single file
  (`ConfigBase::load_from_json`) and never walks the chain, so handing lane C a
  leaf vendor profile slices with built-in defaults for every key the parents
  define — a 200x200 bed, 20% infill, arachne off, and ~90 more. `--cli-presets
  flat` runs lane C through this first; `raw` (the default) reproduces what a
  user typing `--load-settings` actually gets.
- `compare_gcode3mf.py` — standalone comparator for two `.gcode.3mf` (or bare
  `.gcode`) files: settings, model transforms, per-plate G-code statistics,
  and first structural divergence. Usable on its own.
- `gui_lane.sh` — standalone headless GUI driver (Xvfb + xdotool):
  import → slice (Ctrl+R) → export (Ctrl+G) → optional save-as. Slicing
  completion is detected by retrying Ctrl+G until the export dialog opens.
  Runs one-shot (`gui_lane.sh IN OUT [PROJECT]`) or as a reusable session
  (`start` / `job` / `stop`): the runner launches one GUI per fixture and
  drives both GUI lanes (G and RB) through it, paying the ~15-20s
  wx/GL/WebKit init once instead of per lane. Synchronisation polls for
  window/dialog presence and output-file settling rather than sleeping, so a
  full four-lane fixture runs in ~50s (was ~120-150s). The reused-session
  loads rely on the seed suppressing the unsaved-changes prompts.
- `make_seed.py` — generates the datadir seed. Default `--mode resources`
  builds everything from `resources/profiles` (portable, CI-safe);
  `--mode home` clones an existing `~/.config/OrcaSlicer` instead.

## Extending

- New fixture: add an entry to `fixtures.json` (commit any new model/project
  under `parity/fixtures/`). Project 3mf inputs are sliced with their
  embedded settings; other inputs load the manifest's presets.
- Newly discovered *legitimate* difference: add a ledger entry citing where
  it is documented; it then counts as known.
- CI: the harness is environment-driven (`ORCA_BIN`, `ORCA_SLICER_ROOT`,
  `--out`, `--display`) precisely so the OrcaSlicer repo's parity workflow
  can wrap it: check out this repo, download/extract the AppImage build
  artifact (it bundles resources/), install the four display packages, run,
  upload `scorecard.json` + `report.md` as artifacts.

## Known limitations

- GUI lanes drive the real UI; only one GUI lane runs at a time per display,
  and the run aborts if an OrcaSlicer window already exists on the target
  display.
- Fixtures needing hand-authored data (painting, embossing, cut) must be
  committed as prepared project files; the harness slices them but cannot
  author them.
- Object label ids, thumbnails, and device-derived keys legitimately differ
  between the pipelines; see the ledger for the documented list.
