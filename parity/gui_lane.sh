#!/usr/bin/env bash
# Headless GUI slice-and-export driver for OrcaSlicer (parity harness lane G/RB).
#
# Loads a model/project, slices the current plate (Ctrl+R) and exports the
# sliced .gcode.3mf (Ctrl+G), optionally saving the project .3mf (Ctrl+Shift+S).
# Screenshots are written at every step for post-mortem; success is judged by
# the output file appearing, not pixels.
#
# Two ways to invoke:
#
#   gui_lane.sh <input> <output.gcode.3mf> [project-out.3mf]
#       One-shot: launch a GUI, do the job, tear it down. Backward compatible.
#
#   gui_lane.sh start [<input>]                # launch + clear startup dialogs
#   gui_lane.sh job   <input> <out> [project]  # load+slice+export in that session
#   gui_lane.sh stop                           # tear the session down
#       Session reuse: the launch cost (~15-20s of wx/GL/WebKit init under
#       llvmpipe) is paid once; each `job` reuses the running GUI, loading the
#       next file with Ctrl+O. The seed suppresses the unsaved-changes prompts
#       (make_seed.py: save_project_choise / save_preset_choise), so loads are
#       unattended. `start`, `job`, `stop` share $RIG (its session-* files).
#
# Synchronisation is by polling a condition, not fixed sleeps: window/dialog
# presence via `xdotool search`, file readiness via size settling. The only
# remaining fixed waits are short post-condition settles for the canvas/UI to
# repaint, which have no observable signal.
#
# Environment:
#   ORCA_BIN       path to orca-slicer (or ORCA_SLICER_ROOT/build/... fallback)
#   ORCA_DATADIR   GUI datadir (generated seed; see make_seed.py)
#   RIG            working dir for logs/screenshots/session state
#   DISPLAY_NUM    Xvfb display number (default 99)
#   SLICE_TIMEOUT  max seconds to wait for a slice (default 600)
#   WINDOW_TIMEOUT max seconds to wait for the GUI's first window (default 180)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCA_BIN="${ORCA_BIN:-${ORCA_SLICER_ROOT:-/nonexistent}/build/src/RelWithDebInfo/orca-slicer}"
DISPLAY_NUM="${DISPLAY_NUM:-99}"
SLICE_TIMEOUT="${SLICE_TIMEOUT:-600}"
# a crashed GUI is detected by its pid, not by this expiring, so the budget only
# has to cover a slow start on a contended runner
WINDOW_TIMEOUT="${WINDOW_TIMEOUT:-180}"
D=":$DISPLAY_NUM"

# session-* state, gui/xvfb logs live in SESSION_DIR (shared across reused
# jobs); screenshots go in RIG, tagged per job so G and RB don't collide.
# SESSION is set in the dispatch below, once RIG is known.
shot() { DISPLAY=$D import -window root "$RIG/${JOB_TAG:+$JOB_TAG-}$1.png" 2>/dev/null || true; }
now() { date +%s; }

# poll <timeout_s> <cmd...> : return 0 as soon as cmd exits 0, else 1 at timeout
poll() {
    local deadline=$(( $(now) + $1 )); shift
    while [ "$(now)" -lt "$deadline" ]; do
        "$@" >/dev/null 2>&1 && return 0
        sleep 0.2
    done
    return 1
}
# scratch file for one xdotool stderr, see find_wins
XDO_ERR=$(mktemp -t gui_lane_xdo.XXXXXX)
trap 'rm -f "$XDO_ERR"' EXIT

# xdotool walks the window tree (QueryTree) and reads a property on every window
# it finds. A window destroyed between those two steps -- a dialog closing while
# the GUI settles -- makes Xlib raise BadWindow and xdotool abort: exit 1, no
# output, the error on stderr. That is indistinguishable from "no such window"
# unless stderr is read, so an aborted walk is retried rather than reported as
# an empty result. An unmatched search is a normal answer, not a failure, so it
# must not fail the pipeline either: under pipefail set -e would kill the script.
find_wins() {
    local out
    for _ in 1 2 3; do
        : > "$XDO_ERR"
        out=$(DISPLAY=$D xdotool search --name "$1" 2>"$XDO_ERR") || true
        grep -q "^X Error" "$XDO_ERR" || { printf '%s' "$out"; return 0; }
        sleep 0.1
    done
    printf '%s' "$out"          # three aborted walks running: report what we saw
    return 0
}
find_win() { find_wins "$1" | tail -1; }
have_win() { [ -n "$(find_win "$1")" ]; }
gone_win() { [ -z "$(find_win "$1")" ]; }

# id of the GUI's main window, resolved by resolve_main and carried across the
# start/job dispatch through $SESSION/session-main
MAIN=""

# re-resolve the main window id after something may have replaced it (a dialog
# closing, a load). Belt and braces with the retry in find_wins: an empty answer
# here is far more likely to be a lost walk than a lost window, so keep the last
# known id rather than dropping it.
resolve_main() {
    local found; found=$(find_win "OrcaSlicer")
    [ -n "$found" ] && MAIN="$found"
    return 0
}

# wait until a file exists and its size has stopped growing (finished writing)
wait_file_stable() {
    local f="$1" timeout="$2" prev=-1 cur
    poll "$timeout" test -s "$f" || return 1
    for _ in $(seq 20); do
        cur=$(stat -c %s "$f" 2>/dev/null || echo -1)
        [ "$cur" = "$prev" ] && return 0
        prev=$cur; sleep 0.3
    done
    return 0
}

# answer any blocking modal (unit-conversion prompt, STEP import options, info
# dialogs) by accepting its default button, recording the occurrence -- a dialog
# the GUI raises where the CLI proceeds silently is itself a divergence data
# point. Returns 0 if it answered at least one dialog, 1 if none were present.
sweep_dialogs() {
    local answered=1
    for w in $(find_wins "."); do
        [ "$w" = "$MAIN" ] && continue
        local n
        n=$(DISPLAY=$D xdotool getwindowname "$w" 2>/dev/null || true)
        case "$n" in
            "" | orca-slicer | *" - OrcaSlicer" | "Loading..."* \
            | "Save Sliced"* | "Save file as"* | "Choose "* ) continue ;;
        esac
        answered=0
        echo "modal dialog answered: $n" >> "$RIG/dialogs.log"
        shot "modal-$w"
        # real XTEST keypress: wx dialogs ignore synthetic --window keys
        DISPLAY=$D xdotool windowactivate --sync "$w" 2>/dev/null || true
        DISPLAY=$D xdotool windowfocus "$w" 2>/dev/null || true
        DISPLAY=$D xdotool key --clearmodifiers Return || true
        # fallback: a dialog whose focus sits in a text field swallows Return
        # (STEP import parameters). wx right-aligns the affirmative button.
        if DISPLAY=$D xdotool getwindowname "$w" >/dev/null 2>&1; then
            eval "$(DISPLAY=$D xdotool getwindowgeometry --shell "$w" 2>/dev/null || true)"
            [ -n "${WIDTH:-}" ] && DISPLAY=$D xdotool mousemove \
                $((X + WIDTH - 170)) $((Y + HEIGHT - 27)) click 1 || true
        fi
    done
    return $answered
}

# clear startup/late dialogs: sweep until two consecutive passes find nothing
# (or a cap), instead of a fixed six passes
clear_dialogs() {
    local empty=0
    for _ in $(seq 12); do
        if sweep_dialogs; then empty=0; else empty=$((empty + 1)); fi
        [ "$empty" -ge 2 ] && break
        sleep 1
    done
}

# wait for the GUI's first window, failing early if the process is gone. Exits
# 3 when the window never arrives, 8 when the GUI died before showing one.
wait_for_window() {
    local pid="$1" deadline=$(( $(now) + WINDOW_TIMEOUT ))
    while [ "$(now)" -lt "$deadline" ]; do
        have_win "OrcaSlicer" && return 0
        if ! kill -0 "$pid" 2>/dev/null; then
            # it may have mapped a window and exited between the two checks
            have_win "OrcaSlicer" && return 0
            echo "the GUI exited during startup, see $SESSION/gui.log" >&2
            return 8
        fi
        sleep 0.2
    done
    echo "GUI window never appeared within ${WINDOW_TIMEOUT}s" >&2
    return 3
}

ensure_display() {
    if ! DISPLAY=$D xdotool getdisplaygeometry >/dev/null 2>&1; then
        Xvfb "$D" -screen 0 1920x1080x24 -nolisten tcp > "$SESSION/xvfb.log" 2>&1 &
        echo $! > "$SESSION/xvfb.pid"
        poll 15 env DISPLAY=$D xdotool getdisplaygeometry
        DISPLAY=$D openbox > "$SESSION/openbox.log" 2>&1 &
        echo $! > "$SESSION/openbox.pid"
        sleep 0.5
    fi
}

# focus the 3D canvas (empty area, bottom-center) so accelerators are received
focus_canvas() {
    DISPLAY=$D xdotool windowactivate --sync "$MAIN"
    DISPLAY=$D xdotool mousemove 900 900 click 1
}

do_start() {
    local input="${1:-}"
    : "${ORCA_DATADIR:?ORCA_DATADIR required}"
    [ -x "$ORCA_BIN" ] || { echo "no orca-slicer at $ORCA_BIN" >&2; exit 2; }
    [ -d "$ORCA_DATADIR" ] || { echo "no datadir at $ORCA_DATADIR" >&2; exit 2; }
    mkdir -p "$RIG"
    ensure_display

    DISPLAY=$D LIBGL_ALWAYS_SOFTWARE=1 "$ORCA_BIN" --datadir "$ORCA_DATADIR" \
        ${input:+"$input"} > "$SESSION/gui.log" 2>&1 &
    echo $! > "$SESSION/session-pid"

    # wait for the first window, but stop the moment the GUI exits instead of
    # sitting out the whole budget and then blaming the window: a GUI that died
    # on startup says so in gui.log, and that is a different failure
    local gui_pid; gui_pid=$(cat "$SESSION/session-pid")
    wait_for_window "$gui_pid" || exit $?
    resolve_main
    DISPLAY=$D xdotool windowmove "$MAIN" 0 0 windowsize "$MAIN" 1920 1080 || true

    # network-plugin dialog (plugins/ excluded from the seed): closing it can
    # change the main window id, so re-resolve MAIN after
    if poll 8 have_win "Plug-in"; then
        DISPLAY=$D xdotool windowclose "$(find_win "Plug-in")" || true
        poll 5 gone_win "Plug-in" || true
        resolve_main
    fi
    clear_dialogs
    resolve_main
    [ -n "$MAIN" ] || { echo "the GUI window vanished during startup" >&2; exit 3; }
    echo "$MAIN" > "$SESSION/session-main"
    echo "${input:-}" > "$SESSION/session-loaded"
    shot 01-loaded
}

# load a model/project into the running session (skipped if already loaded)
load_input() {
    local input="$1"
    [ "$input" = "$(cat "$SESSION/session-loaded" 2>/dev/null || true)" ] && return 0
    focus_canvas
    DISPLAY=$D xdotool key --clearmodifiers ctrl+o
    poll 15 have_win "Choose " || { echo "open dialog never appeared" >&2; return 1; }
    local dlg; dlg=$(find_win "Choose ")
    DISPLAY=$D xdotool key --clearmodifiers ctrl+a
    DISPLAY=$D xdotool type --delay 15 "$input"
    DISPLAY=$D xdotool key Return
    # the unsaved-changes / modified-preset prompts are suppressed by the seed;
    # wait for the open dialog to close, then clear any import dialog
    poll 20 gone_win "Choose " || true
    resolve_main
    clear_dialogs
    resolve_main
    echo "$input" > "$SESSION/session-loaded"
}

do_job() {
    local input="$1" out="$2" project="${3:-}"
    MAIN=$(cat "$SESSION/session-main" 2>/dev/null || true)
    [ -n "$MAIN" ] || { echo "no GUI session to run in: start did not complete" >&2; exit 7; }
    load_input "$input"

    focus_canvas
    shot 02-focused
    DISPLAY=$D xdotool key --clearmodifiers ctrl+r
    shot 03-slicing

    # retry Ctrl+G until the export dialog opens -- it only appears once
    # can_export_gcode() is true, i.e. slicing finished (this IS the poll)
    local deadline=$(( $(now) + SLICE_TIMEOUT )) dlg=""
    while [ "$(now)" -lt "$deadline" ]; do
        sweep_dialogs || true
        DISPLAY=$D xdotool windowactivate --sync "$MAIN" 2>/dev/null || true
        DISPLAY=$D xdotool key --clearmodifiers ctrl+g
        if poll 3 have_win "Save Sliced"; then dlg=1; break; fi
    done
    [ -n "$dlg" ] || { shot 99-no-dialog; echo "export dialog never opened" >&2; exit 4; }
    shot 04-export-dialog

    rm -f "$out"
    DISPLAY=$D xdotool key --clearmodifiers ctrl+a
    DISPLAY=$D xdotool type --delay 15 "$out"
    DISPLAY=$D xdotool key Return
    wait_file_stable "$out" 40 || { shot 99-no-output; echo "export never appeared" >&2; exit 5; }
    # early Ctrl+G presses (before slicing finished) can leave a "Jump to layer"
    # popup on the Preview tab -- dismiss it
    DISPLAY=$D xdotool key Escape
    shot 05-exported
    echo "exported: $out"

    if [ -n "$project" ]; then
        DISPLAY=$D xdotool key --clearmodifiers ctrl+shift+s
        if poll 10 have_win "Save file as"; then
            rm -f "$project"
            DISPLAY=$D xdotool key --clearmodifiers ctrl+a
            DISPLAY=$D xdotool type --delay 15 "$project"
            DISPLAY=$D xdotool key Return
            wait_file_stable "$project" 20 || true
        fi
        [ -s "$project" ] && echo "project saved: $project" \
            || { shot 99-no-project; echo "project save failed" >&2; exit 6; }
        shot 06-project-saved
    fi
}

do_stop() {
    local pid f
    # the GUI first, then the display servers this script started, if any
    for f in session-pid openbox.pid xvfb.pid; do
        pid=$(cat "$SESSION/$f" 2>/dev/null || true)
        [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
        rm -f "$SESSION/$f"
    done
    rm -f "$SESSION/session-main" "$SESSION/session-loaded"
}

# --- dispatch ----------------------------------------------------------------
case "${1:-}" in
    start) RIG="${RIG:?RIG required}"; SESSION="${SESSION_DIR:-$RIG}"; do_start "${2:-}" ;;
    job)   RIG="${RIG:?RIG required}"; SESSION="${SESSION_DIR:-$RIG}"; do_job "$2" "$3" "${4:-}" ;;
    stop)  RIG="${RIG:?RIG required}"; SESSION="${SESSION_DIR:-$RIG}"; do_stop ;;
    *)
        # one-shot: launch, do the job, tear down (backward-compatible interface)
        IN="$1"; OUT="$2"; PROJECT_OUT="${3:-}"
        RIG="${RIG:-$(dirname "$OUT")/rig}"
        SESSION="${SESSION_DIR:-$RIG}"
        ORCA_DATADIR="${ORCA_DATADIR:-$RIG/datadir}"
        trap 'do_stop' EXIT
        do_start "$IN"
        do_job "$IN" "$OUT" "$PROJECT_OUT"
        ;;
esac
