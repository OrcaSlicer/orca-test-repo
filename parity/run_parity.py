#!/usr/bin/env python3
"""GUI-vs-CLI parity harness orchestrator.

Runs each fixture through up to four lanes, all with the same binary and the
same generated datadir seed, then compares the exports and emits a metrics
scorecard. Nothing is gated: lane failures and divergences are recorded as
data, and the process exits 0 unless the harness itself breaks.

Lanes:
  G   GUI headless: import -> slice -> export sliced 3mf + save project
  C   CLI independent: same input + equivalent preset files/flags
  R   CLI round-trip: CLI re-slices lane G's saved project
  RB  GUI round-trip: GUI opens lane C's export and re-slices it

Comparisons (when both sides produced output):
  pipeline  = C vs G    (whole-pipeline parity)
  engine    = R vs G    (slicing-engine parity on identical inputs)
  gui_load  = RB vs C   (what the GUI load path does to CLI-authored state)
  plus settings_survival: which config keys survive the CLI->GUI reopen (RB)

Diffs are classified against expected_differences.json into known/new; the
scorecard's headline number is the count of NEW divergences.

The suite lives in orca-test-repo; the OrcaSlicer checkout (or extracted
AppImage) that provides resources/profiles and the binary is given via
--slicer-root / ORCA_SLICER_ROOT.

Usage:
  run_parity.py --slicer-root /path/to/OrcaSlicer [--fixture ID]...
                [--lanes G,C,R,RB] [--out DIR] [--bin PATH] [--display N]

Requires for lane G/RB: Xvfb, xdotool, imagemagick, openbox (see gui_lane.sh).
Stdlib only.
"""

import argparse
import datetime
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

HERE = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, HERE)
import compare_gcode3mf as cmp3mf  # noqa: E402

CLI_TIMEOUT = 600
GUI_TIMEOUT = 900


def log(msg):
    print("[parity] %s" % msg, flush=True)


def resolve_slicer_root(arg):
    root = arg or os.environ.get("ORCA_SLICER_ROOT")
    if not root or not os.path.isdir(os.path.join(root, "resources", "profiles")):
        sys.exit("--slicer-root (or ORCA_SLICER_ROOT) must point at an OrcaSlicer"
                 " checkout or extracted AppImage containing resources/profiles")
    return os.path.abspath(root)


def default_bin(repo):
    for rel in ("build/src/RelWithDebInfo/orca-slicer", "build/package/bin/orca-slicer"):
        p = os.path.join(repo, rel)
        if os.access(p, os.X_OK):
            return p
    return None


def preset_paths(repo, fx, datadir, flatten_dir=None):
    """Resolve the fixture's preset names to files. A name matching a user
    preset installed into the seed resolves to the copy INSIDE the given
    datadir (the CLI's bundle match is filesystem-equivalence only, so the
    loaded file must be the one the bundle sees).

    With flatten_dir set, a system vendor profile is first flattened along its
    `inherits` chain into that directory. The CLI reads one file and never
    walks the chain (ConfigBase::load_from_json), so an unflattened leaf makes
    lane C slice with built-in defaults for every key its parents define."""
    base = os.path.join(repo, "resources", "profiles", fx["vendor"])
    out = {}
    for kind in ("machine", "process", "filament"):
        name = fx[kind]
        user = os.path.join(datadir, "user", "default", kind, name + ".json")
        if os.path.isfile(user):
            out[kind] = user
        elif flatten_dir:
            out[kind] = flatten_preset(repo, fx["vendor"], kind, name, flatten_dir)
        else:
            out[kind] = os.path.join(base, kind, name + ".json")
    return out


def flatten_preset(repo, vendor, kind, name, outdir):
    """Write a self-contained copy of a system preset into outdir."""
    os.makedirs(outdir, exist_ok=True)
    dst = os.path.join(outdir, "%s.json" % kind)
    cmd = [sys.executable, os.path.join(HERE, "flatten_preset.py"),
           "--profiles", os.path.join(repo, "resources", "profiles"),
           "--vendor", vendor, "--vendor", "OrcaFilamentLibrary",
           "--kind", kind, "--name", name, "--out", dst]
    subprocess.run(cmd, check=True, capture_output=True)
    return dst


def install_user_presets(fx, repo, seed):
    """Copy fixture-declared user presets into the seed's user folder."""
    for rel in fx.get("install_user_presets", []):
        src = os.path.join(HERE, rel)
        with open(src) as f:
            kind = json.load(f).get("type", "process")
        dst_dir = os.path.join(seed, "user", "default", kind)
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copy2(src, os.path.join(dst_dir, os.path.basename(src)))


def run(cmd, cwd, timeout, logfile, env=None):
    t0 = time.time()
    full_env = dict(os.environ, **(env or {}))
    # own process group so a timeout kill reaps the whole lane (the GUI is a
    # grandchild; killing only the driver script would orphan it)
    with open(logfile, "w") as lf:
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=full_env, stdout=lf,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        try:
            proc.wait(timeout=timeout)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, 15)
                proc.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(proc.pid, 9)
                except ProcessLookupError:
                    pass
            rc = "timeout"
    return {"exit": rc, "seconds": round(time.time() - t0, 1)}


def project_settings(path):
    with zipfile.ZipFile(path) as z:
        return cmp3mf.flatten_settings(
            json.loads(z.read("Metadata/project_settings.config"))
        )


# ---------------------------------------------------------------- diff atoms

def atoms_from_comparison(cjson):
    """Flatten a compare_gcode3mf --json result into (section, key) atoms."""
    out = []
    m = cjson.get("members", {})
    for k in m.get("only_a", []) + m.get("only_b", []):
        out.append(("members", k))
    ps = cjson.get("project_settings", {})
    for bucket in ("only_a", "only_b", "changed"):
        for k in ps.get(bucket, {}):
            out.append(("project_settings", k))
    for section in ("model_settings", "slice_info", "plate_json",
                    "gcode_header", "gcode_config"):
        for name, d in cjson.get(section, {}).items():
            for bucket in ("only_a", "only_b", "changed"):
                for k in d.get(bucket, {}):
                    out.append((section, k))
    for plate, d in cjson.get("gcode", {}).items():
        if not d.get("identical", True):
            out.append(("gcode", plate))
    if cjson.get("model_identical") is False:
        out.append(("model", "tree"))
    return out


def classify(atoms, ledger):
    known, new = [], []
    for section, key in atoms:
        hit = None
        for e in ledger["entries"]:
            if fnmatch.fnmatch(section, e["match"]["section"]) and fnmatch.fnmatch(
                key, e["match"]["key"]
            ):
                hit = e["id"]
                break
        (known if hit else new).append(
            {"section": section, "key": key, **({"ledger": hit} if hit else {})}
        )
    return known, new


def compare_pair(a, b, label_a, label_b, outdir, tag):
    """Run compare_gcode3mf as a subprocess, return its JSON result."""
    jpath = os.path.join(outdir, "compare_%s.json" % tag)
    tpath = os.path.join(outdir, "compare_%s.txt" % tag)
    cmd = [sys.executable, os.path.join(HERE, "compare_gcode3mf.py"),
           "--label-a", label_a, "--label-b", label_b, "--json", jpath, a, b]
    with open(tpath, "w") as tf:
        subprocess.run(cmd, stdout=tf, stderr=subprocess.STDOUT, timeout=300)
    with open(jpath) as f:
        return json.load(f)


# ---------------------------------------------------------------- lanes

def lane_C(binpath, fx, input_path, datadir, outdir, flatten=False):
    out3mf = os.path.join(outdir, "cli.gcode.3mf")
    cmd = [binpath, "--datadir", datadir]
    if not input_path.endswith(".3mf"):
        pp = preset_paths(fx["_repo_cfg"], fx, datadir,
                          os.path.join(outdir, "flat") if flatten else None)
        cmd += ["--load-settings", "%s;%s" % (pp["machine"], pp["process"]),
                "--load-filaments", pp["filament"]]
    for k, v in fx.get("flags", {}).items():
        cmd += ["--" + k] + ([str(v)] if v not in (None, "") else [])
    cmd += ["--slice", str(fx.get("slice_plate", 1)),
            "--export-3mf", out3mf, input_path]
    res = run(cmd, outdir, CLI_TIMEOUT, os.path.join(outdir, "cli.log"))
    res["output"] = out3mf if os.path.isfile(out3mf) else None
    return res


def lane_cli_reslice(binpath, project_3mf, datadir, outdir, name):
    out3mf = os.path.join(outdir, name + ".gcode.3mf")
    cmd = [binpath, "--datadir", datadir, "--slice", "1",
           "--export-3mf", out3mf, project_3mf]
    res = run(cmd, outdir, CLI_TIMEOUT, os.path.join(outdir, name + ".log"))
    res["output"] = out3mf if os.path.isfile(out3mf) else None
    return res


def gui_session_start(binpath, datadir, session_dir, display, preload=None):
    """Launch one GUI once; lanes G and RB then reuse it (gui_job), paying the
    ~15-20s wx/GL/WebKit init only once per fixture. `preload` opens a file at
    launch so the first job that wants it skips a redundant Ctrl+O."""
    os.makedirs(session_dir, exist_ok=True)
    env = {"ORCA_BIN": binpath, "ORCA_DATADIR": datadir, "RIG": session_dir,
           "SESSION_DIR": session_dir, "DISPLAY_NUM": str(display)}
    cmd = [os.path.join(HERE, "gui_lane.sh"), "start"]
    if preload:
        cmd.append(preload)
    return run(cmd, session_dir, GUI_TIMEOUT,
               os.path.join(session_dir, "start.log"), env)


def gui_session_stop(session_dir, display):
    env = {"RIG": session_dir, "SESSION_DIR": session_dir, "DISPLAY_NUM": str(display)}
    return run([os.path.join(HERE, "gui_lane.sh"), "stop"], session_dir, 60,
               os.path.join(session_dir, "stop.log"), env)


def gui_job(binpath, input_path, datadir, session_dir, outdir, name, display,
            save_project=True, slice_timeout=None):
    out3mf = os.path.join(outdir, name + ".gcode.3mf")
    project = os.path.join(outdir, name + "_project.3mf") if save_project else None
    rig = os.path.join(outdir, name + "-rig")
    os.makedirs(rig, exist_ok=True)
    cmd = [os.path.join(HERE, "gui_lane.sh"), "job", input_path, out3mf]
    if project:
        cmd.append(project)
    env = {"ORCA_BIN": binpath, "ORCA_DATADIR": datadir, "RIG": rig,
           "SESSION_DIR": session_dir, "DISPLAY_NUM": str(display), "JOB_TAG": name}
    if slice_timeout:
        env["SLICE_TIMEOUT"] = str(slice_timeout)
    res = run(cmd, outdir, max(GUI_TIMEOUT, (slice_timeout or 0) + 300),
              os.path.join(outdir, name + ".log"), env)
    res["output"] = out3mf if os.path.isfile(out3mf) else None
    res["project"] = project if project and os.path.isfile(project) else None
    return res


# ---------------------------------------------------------------- main

def display_busy(display):
    try:
        r = subprocess.run(
            ["xdotool", "search", "--name", "OrcaSlicer"],
            env=dict(os.environ, DISPLAY=":%d" % display),
            capture_output=True, timeout=10,
        )
        return bool(r.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixture", action="append", help="fixture id(s) to run (default: all)")
    ap.add_argument("--lanes", default=None, help="comma list among G,C,R,RB (default: per-fixture)")
    ap.add_argument("--slicer-root", default=None,
                    help="OrcaSlicer checkout / extracted AppImage root "
                         "(env ORCA_SLICER_ROOT)")
    ap.add_argument("--bin", default=os.environ.get("ORCA_BIN"))
    ap.add_argument("--out", default=None, help="results dir (default: temp dir)")
    ap.add_argument("--display", type=int, default=int(os.environ.get("DISPLAY_NUM", "99")))
    ap.add_argument("--seed-mode", choices=("resources", "home"), default="resources")
    ap.add_argument("--cli-presets", choices=("raw", "flat"), default="raw",
                    help="raw: hand lane C the leaf vendor profile as-is (what a "
                         "user typing --load-settings gets). flat: flatten the "
                         "inherits chain first, so lane C and lane G start from "
                         "the same effective config.")
    ap.add_argument("--manifest", default=os.path.join(HERE, "fixtures.json"))
    ap.add_argument("--ledger", default=os.path.join(HERE, "expected_differences.json"))
    args = ap.parse_args()

    repo = resolve_slicer_root(args.slicer_root)
    if not args.bin:
        args.bin = default_bin(repo)
    if not args.bin or not os.access(args.bin, os.X_OK):
        sys.exit("no usable orca-slicer binary (pass --bin or set ORCA_BIN)")
    out = args.out or tempfile.mkdtemp(prefix="orca-parity-")
    os.makedirs(out, exist_ok=True)

    with open(args.manifest) as f:
        manifest = json.load(f)
    with open(args.ledger) as f:
        ledger = json.load(f)

    fixtures = []
    for fx in manifest["fixtures"]:
        merged = dict(manifest.get("defaults", {}), **fx)
        merged["_repo_cfg"] = repo
        if not args.fixture or merged["id"] in args.fixture:
            fixtures.append(merged)
    if not fixtures:
        sys.exit("no fixtures matched %r" % (args.fixture,))

    lanes_override = args.lanes.split(",") if args.lanes else None
    need_gui = any(
        set(lanes_override or fx["lanes"]) & {"G", "RB"} for fx in fixtures
    )
    if need_gui and display_busy(args.display):
        sys.exit(
            "an OrcaSlicer window already exists on :%d - close it or pick "
            "another --display" % args.display
        )

    scorecard = {
        "run": {
            "date": datetime.datetime.now().isoformat(timespec="seconds"),
            "bin": args.bin,
            "cli_presets": args.cli_presets,
            "sha": subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"], cwd=repo,
                capture_output=True, text=True,
            ).stdout.strip() or "(no git)",
            "suite_sha": subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"], cwd=HERE,
                capture_output=True, text=True,
            ).stdout.strip(),
            "out": out,
        },
        "fixtures": [],
    }

    for fx in fixtures:
        lanes = lanes_override or fx["lanes"]
        fdir = os.path.join(out, fx["id"])
        os.makedirs(fdir, exist_ok=True)
        input_path = os.path.join(HERE, fx["input"])
        entry = {"id": fx["id"], "lanes": {}, "comparisons": {}}
        log("fixture %s: lanes %s" % (fx["id"], ",".join(lanes)))

        # per-fixture seed: presets differ between fixtures, and user presets
        # declared by the fixture must live inside the datadir
        seed = os.path.join(fdir, "seed")
        subprocess.run(
            [sys.executable, os.path.join(HERE, "make_seed.py"), "--out", seed,
             "--mode", args.seed_mode, "--repo", repo, "--force",
             "--vendor", fx["vendor"], "--machine", fx["machine"],
             "--process", fx["process"], "--filament", fx["filament"]],
            check=True,
        )
        install_user_presets(fx, repo, seed)

        # CLI lanes get a private datadir each (the CLI mutates it); GUI lanes
        # G and RB share one datadir + one reused GUI session per fixture
        datadirs = {}
        for lane in lanes:
            if lane in ("G", "RB"):
                continue
            d = os.path.join(fdir, "datadir-" + lane.lower())
            shutil.copytree(seed, d, dirs_exist_ok=True)
            datadirs[lane] = d
        gui_lanes = [lane for lane in ("G", "RB") if lane in lanes]
        gui_session = os.path.join(fdir, "gui-session")
        gui_datadir = os.path.join(fdir, "datadir-gui")
        gui_started = False
        if gui_lanes:
            shutil.copytree(seed, gui_datadir, dirs_exist_ok=True)

        def start_gui_once(preload=None):
            nonlocal gui_started
            if not gui_started:
                log("  gui session start")
                gui_session_start(args.bin, gui_datadir, gui_session, args.display,
                                  preload=preload)
                gui_started = True

        results = {}
        try:
            if "G" in lanes:
                log("  lane G (GUI)")
                start_gui_once(preload=input_path)
                results["G"] = gui_job(args.bin, input_path, gui_datadir, gui_session,
                                       fdir, "gui", args.display,
                                       slice_timeout=fx.get("gui_slice_timeout"))
                entry["lanes"]["G"] = {k: results["G"][k] for k in ("exit", "seconds")}
            if "C" in lanes:
                log("  lane C (CLI)")
                results["C"] = lane_C(args.bin, fx, input_path, datadirs["C"], fdir,
                                      flatten=args.cli_presets == "flat")
                entry["lanes"]["C"] = {k: results["C"][k] for k in ("exit", "seconds")}
            if "R" in lanes and results.get("G", {}).get("project"):
                log("  lane R (CLI round-trip)")
                results["R"] = lane_cli_reslice(args.bin, results["G"]["project"],
                                                datadirs["R"], fdir, "roundtrip_cli")
                entry["lanes"]["R"] = {k: results["R"][k] for k in ("exit", "seconds")}
            if "RB" in lanes and results.get("C", {}).get("output"):
                log("  lane RB (GUI round-trip)")
                # preload so a cold session (RB-only fixture) launches straight
                # onto the loaded project rather than the empty Home page, which
                # breaks the Ctrl+O load path; a warm session (G already ran)
                # ignores preload and loads via Ctrl+O
                start_gui_once(preload=results["C"]["output"])
                results["RB"] = gui_job(args.bin, results["C"]["output"], gui_datadir,
                                        gui_session, fdir, "roundtrip_gui", args.display,
                                        slice_timeout=fx.get("gui_slice_timeout"))
                entry["lanes"]["RB"] = {k: results["RB"][k] for k in ("exit", "seconds")}
        finally:
            if gui_started:
                log("  gui session stop")
                gui_session_stop(gui_session, args.display)

        pairs = {
            "pipeline": ("C", "G"),
            "engine": ("R", "G"),
            "gui_load": ("RB", "C"),
        }
        for tag, (la, lb) in pairs.items():
            a = results.get(la, {}).get("output")
            b = results.get(lb, {}).get("output")
            if not (a and b):
                continue
            log("  compare %s (%s vs %s)" % (tag, la, lb))
            cjson = compare_pair(a, b, la, lb, fdir, tag)
            # fixture-level expected diffs (documented behavior this fixture
            # deliberately provokes) are treated as known for this fixture only
            fx_ledger = {
                "entries": ledger["entries"] + [
                    {"id": "fixture-expected", "match": m}
                    for m in fx.get("expect", [])
                ]
            }
            known, new = classify(atoms_from_comparison(cjson), fx_ledger)
            gplates = cjson.get("gcode", {})
            entry["comparisons"][tag] = {
                "gcode_identical": all(d.get("identical") for d in gplates.values())
                if gplates else None,
                "similarity": min(
                    (d.get("similarity", 1.0) for d in gplates.values()),
                    default=None,
                ),
                "known_diffs": len(known),
                "new_diffs": len(new),
                "new": new,
            }

        # settings survival across the CLI -> GUI reopen
        rb = results.get("RB", {})
        if rb.get("project") and results.get("C", {}).get("output"):
            before = project_settings(results["C"]["output"])
            after = project_settings(rb["project"])
            changed = sorted(
                k for k in before if k in after and before[k] != after[k]
            )
            entry["settings_survival"] = {
                "changed_on_reopen": len(changed),
                "keys": changed[:50],
            }

        scorecard["fixtures"].append(entry)

    new_total = sum(
        c.get("new_diffs", 0)
        for f in scorecard["fixtures"]
        for c in f["comparisons"].values()
    )
    scorecard["summary"] = {
        "fixtures": len(scorecard["fixtures"]),
        "new_divergences": new_total,
    }

    spath = os.path.join(out, "scorecard.json")
    with open(spath, "w") as f:
        json.dump(scorecard, f, indent=1)

    # human-readable report
    lines = ["# GUI-vs-CLI parity scorecard", "",
             "run: %s @ %s" % (scorecard["run"]["date"], scorecard["run"]["sha"]),
             "new divergences: **%d**" % new_total, ""]
    for f_ in scorecard["fixtures"]:
        lines.append("## %s" % f_["id"])
        for lane, r in f_["lanes"].items():
            lines.append("- lane %s: exit %s (%ss)" % (lane, r["exit"], r["seconds"]))
        for tag, c in f_["comparisons"].items():
            lines.append(
                "- %s: gcode_identical=%s similarity=%s known=%d new=%d"
                % (tag, c["gcode_identical"], c["similarity"],
                   c["known_diffs"], c["new_diffs"])
            )
            for n in c["new"]:
                lines.append("    - NEW: %s / %s" % (n["section"], n["key"]))
        if "settings_survival" in f_:
            s = f_["settings_survival"]
            lines.append("- settings changed on CLI->GUI reopen: %d" % s["changed_on_reopen"])
            for k in s["keys"][:10]:
                lines.append("    - %s" % k)
        lines.append("")
    with open(os.path.join(out, "report.md"), "w") as f:
        f.write("\n".join(lines) + "\n")

    log("scorecard: %s" % spath)
    log("report:    %s" % os.path.join(out, "report.md"))
    log("new divergences: %d" % new_total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
