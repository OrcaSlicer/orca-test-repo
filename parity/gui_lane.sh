#!/usr/bin/env bash
# Headless GUI slice-and-export driver for OrcaSlicer (Option B rig).
#
# Launches the OrcaSlicer GUI under Xvfb, loads a model, slices the current
# plate (Ctrl+R) and exports the sliced .gcode.3mf (Ctrl+G). Optionally also
# saves the project .3mf (Ctrl+S). Screenshots are written at every step for
# post-mortem; pass/fail is judged by the output file appearing, not pixels.
#
# Usage:
#   gui_lane.sh <input model/3mf> <output.gcode.3mf> [project-out.3mf]
#
# Environment overrides:
#   ORCA_BIN        path to orca-slicer (default: build/src/RelWithDebInfo/orca-slicer
#                   next to this script's repo — NOT build/package/bin, which goes
#                   stale; see the worktree-binary gotcha)
#   ORCA_DATADIR    GUI datadir (default: $RIG/datadir). Seed it from a real
#                   ~/.config/OrcaSlicer (minus plugins/log/cache) so no wizard
#                   or network prompts appear.
#   RIG             working dir for logs/screenshots (default: alongside output)
#   DISPLAY_NUM     Xvfb display number (default 99)
#   SLICE_TIMEOUT   max seconds to wait for slicing (default 600)
#
# Sync strategy discovered in the pilot:
#  - Slicing completion is detected by retrying Ctrl+G: the export dialog only
#    opens once can_export_gcode() is true, i.e. slicing finished.
#  - The GTK save dialog is driven with Ctrl+A + typing the absolute path.
#  - The Bambu Network Plug-in dialog (appears when plugins/ excluded from the
#    datadir) is closed via its window id.
set -euo pipefail

IN="$1"; OUT="$2"; PROJECT_OUT="${3:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# binary comes from the env (the suite lives outside the OrcaSlicer repo);
# ORCA_SLICER_ROOT may provide a fallback build location
ORCA_BIN="${ORCA_BIN:-${ORCA_SLICER_ROOT:-/nonexistent}/build/src/RelWithDebInfo/orca-slicer}"
RIG="${RIG:-$(dirname "$OUT")/rig}"
ORCA_DATADIR="${ORCA_DATADIR:-$RIG/datadir}"
DISPLAY_NUM="${DISPLAY_NUM:-99}"
SLICE_TIMEOUT="${SLICE_TIMEOUT:-600}"
D=":$DISPLAY_NUM"
mkdir -p "$RIG" "$(dirname "$OUT")"

[ -x "$ORCA_BIN" ] || { echo "no orca-slicer at $ORCA_BIN" >&2; exit 2; }
[ -d "$ORCA_DATADIR" ] || { echo "no datadir at $ORCA_DATADIR (seed it first)" >&2; exit 2; }

shot() { DISPLAY=$D import -window root "$RIG/$1.png" 2>/dev/null || true; }

# --- display -----------------------------------------------------------------
if ! DISPLAY=$D xdotool getdisplaygeometry >/dev/null 2>&1; then
    Xvfb "$D" -screen 0 1920x1080x24 -nolisten tcp > "$RIG/xvfb.log" 2>&1 &
    echo $! > "$RIG/xvfb.pid"
    sleep 1.5
    DISPLAY=$D openbox > "$RIG/openbox.log" 2>&1 &
    echo $! > "$RIG/openbox.pid"
    sleep 1
fi

# --- launch GUI --------------------------------------------------------------
DISPLAY=$D LIBGL_ALWAYS_SOFTWARE=1 "$ORCA_BIN" --datadir "$ORCA_DATADIR" "$IN" \
    > "$RIG/gui.log" 2>&1 &
GUI_PID=$!
trap 'kill $GUI_PID 2>/dev/null || true' EXIT

# wait for the main window (title contains "OrcaSlicer")
MAIN=""
for _ in $(seq 60); do
    MAIN=$(DISPLAY=$D xdotool search --name "OrcaSlicer" 2>/dev/null | tail -1 || true)
    [ -n "$MAIN" ] && break
    sleep 1
done
[ -n "$MAIN" ] || { echo "GUI window never appeared (see $RIG/gui.log)" >&2; exit 3; }
# pin the window geometry: a fresh seed opens a default-sized window, and
# fixed-coordinate interactions assume a maximized 1920x1080 layout
DISPLAY=$D xdotool windowmove "$MAIN" 0 0 windowsize "$MAIN" 1920 1080 || true
sleep 6   # let the project load and the canvas initialise
shot 01-loaded

# dismiss the network-plugin dialog if present; closing it can change the main
# window id (observed at runtime), so re-search and re-activate afterwards
PLUGIN=$(DISPLAY=$D xdotool search --name "Plug-in" 2>/dev/null | head -1 || true)
if [ -n "$PLUGIN" ]; then
    DISPLAY=$D xdotool windowclose "$PLUGIN" || true
    sleep 2
    MAIN=$(DISPLAY=$D xdotool search --name "OrcaSlicer" 2>/dev/null | tail -1 || true)
    [ -n "$MAIN" ] || { echo "main window lost after plugin dialog" >&2; exit 3; }
fi

# answer any blocking modal (unit-conversion prompt, STEP import options,
# info dialogs): screenshot it, press Return to accept the default button,
# and record the occurrence - a dialog the GUI raises where the CLI proceeds
# silently is itself a divergence data point. Dialogs can appear late (e.g.
# STEP options after a slow load), so this also runs inside the export loop.
sweep_dialogs() {
    for w in $(DISPLAY=$D xdotool search --name "." 2>/dev/null); do
        [ "$w" = "$MAIN" ] && continue
        n=$(DISPLAY=$D xdotool getwindowname "$w" 2>/dev/null || true)
        case "$n" in
            # skip: unnamed, the app's stub windows, the main frame, progress
            # popups (whose only button is Cancel), and our own save dialogs
            "" | orca-slicer | *" - OrcaSlicer" | "Loading..."* \
            | "Save Sliced"* | "Save file as"* | "Choose "* ) continue ;;
        esac
        echo "modal dialog answered with Return: $n" >> "$RIG/dialogs.log"
        shot "modal-$w"
        # activate + real XTEST keypress: wx dialogs ignore synthetic
        # XSendEvent keys from `xdotool key --window`
        DISPLAY=$D xdotool windowactivate --sync "$w" 2>/dev/null || true
        DISPLAY=$D xdotool windowfocus "$w" 2>/dev/null || true
        DISPLAY=$D xdotool key --clearmodifiers Return || true
        sleep 1
        # fallback: dialogs whose focus sits in a text field swallow Return
        # (e.g. STEP import parameters). wx right-aligns the affirmative
        # button; click its zone relative to the dialog geometry.
        if DISPLAY=$D xdotool getwindowname "$w" >/dev/null 2>&1; then
            eval "$(DISPLAY=$D xdotool getwindowgeometry --shell "$w" 2>/dev/null || true)"
            if [ -n "${WIDTH:-}" ]; then
                echo "  still open, clicking affirmative-button zone" >> "$RIG/dialogs.log"
                DISPLAY=$D xdotool mousemove $((X + WIDTH - 170)) $((Y + HEIGHT - 27)) click 1 || true
                sleep 1
            fi
        fi
    done
}
for _ in 1 2 3 4 5 6; do
    sleep 2
    sweep_dialogs
done
MAIN=$(DISPLAY=$D xdotool search --name "OrcaSlicer" 2>/dev/null | tail -1 || true)

# focus the 3D canvas (empty area, bottom-center) so accelerators are received
DISPLAY=$D xdotool windowactivate --sync "$MAIN"
DISPLAY=$D xdotool mousemove 900 900 click 1
sleep 1
shot 02-focused

# --- slice -------------------------------------------------------------------
DISPLAY=$D xdotool key --clearmodifiers ctrl+r
shot 03-slicing

# --- export: retry Ctrl+G until the save dialog opens (== slicing done) ------
DIALOG=""
elapsed=0
while [ "$elapsed" -lt "$SLICE_TIMEOUT" ]; do
    sweep_dialogs
    DISPLAY=$D xdotool windowactivate --sync "$MAIN" 2>/dev/null || true
    DISPLAY=$D xdotool key --clearmodifiers ctrl+g
    sleep 3; elapsed=$((elapsed + 3))
    DIALOG=$(DISPLAY=$D xdotool search --name "Save Sliced" 2>/dev/null | head -1 || true)
    [ -n "$DIALOG" ] && break
done
[ -n "$DIALOG" ] || { shot 99-no-dialog; echo "export dialog never opened" >&2; exit 4; }
shot 04-export-dialog

rm -f "$OUT"
DISPLAY=$D xdotool key --clearmodifiers ctrl+a
DISPLAY=$D xdotool type --delay 15 "$OUT"
sleep 1
DISPLAY=$D xdotool key Return
for _ in $(seq 30); do [ -s "$OUT" ] && break; sleep 1; done
[ -s "$OUT" ] || { shot 99-no-output; echo "export file never appeared" >&2; exit 5; }
# early Ctrl+G presses (before slicing finished) fall through to the canvas
# and open a "Jump to layer" popup on the Preview tab — dismiss it
DISPLAY=$D xdotool key Escape
shot 05-exported
echo "exported: $OUT"

# --- optionally save the project (geometry-bearing 3mf for CLI input) --------
if [ -n "$PROJECT_OUT" ]; then
    DISPLAY=$D xdotool key --clearmodifiers ctrl+shift+s
    sleep 3
    SAVED=$(DISPLAY=$D xdotool search --name "Save file as" 2>/dev/null | head -1 || true)
    if [ -n "$SAVED" ]; then
        rm -f "$PROJECT_OUT"
        DISPLAY=$D xdotool key --clearmodifiers ctrl+a
        DISPLAY=$D xdotool type --delay 15 "$PROJECT_OUT"
        sleep 1
        DISPLAY=$D xdotool key Return
        for _ in $(seq 15); do [ -s "$PROJECT_OUT" ] && break; sleep 1; done
    fi
    [ -s "$PROJECT_OUT" ] && echo "project saved: $PROJECT_OUT" \
        || { shot 99-no-project; echo "project save failed" >&2; exit 6; }
    shot 06-project-saved
fi
