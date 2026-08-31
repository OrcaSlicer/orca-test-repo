"""Composable assertions used by cases/*.yaml.

Each check is a plain function: (result, ctx, **params) -> None, raising
AssertionError on failure. Adding a new kind of assertion means adding one
function here and registering it in CHECKS -- case files never need new
Python beyond that.
"""
import json
from pathlib import Path


def _fail(msg, result):
    raise AssertionError(
        f"{msg}\n"
        f"--- argv ---\n{result.args}\n"
        f"--- stdout (tail) ---\n{result.stdout[-2000:]}\n"
        f"--- stderr (tail) ---\n{result.stderr[-2000:]}"
    )


def no_crash(result, ctx, **_):
    """A negative returncode means Python's subprocess saw the child killed by
    a signal (SIGSEGV=-11, SIGABRT=-6, ...) -- the one universal "didn't crash"
    check, usable even when we don't know what a clean exit code looks like."""
    if result.returncode < 0:
        _fail(f"process was killed by signal {-result.returncode}", result)


def exit_code(result, ctx, equals=None, one_of=None, not_equals=None, **_):
    if equals is not None and result.returncode != equals:
        _fail(f"expected exit code {equals}, got {result.returncode}", result)
    if one_of is not None and result.returncode not in one_of:
        _fail(f"expected exit code in {one_of}, got {result.returncode}", result)
    if not_equals is not None and result.returncode == not_equals:
        _fail(f"expected exit code other than {not_equals}, got {result.returncode}", result)


def stdout_contains(result, ctx, text, **_):
    if text not in result.stdout:
        _fail(f"expected stdout to contain {text!r}", result)


def stderr_contains(result, ctx, text, **_):
    if text not in result.stderr:
        _fail(f"expected stderr to contain {text!r}", result)


def output_not_contains(result, ctx, text, **_):
    combined = result.stdout + result.stderr
    if text in combined:
        _fail(f"expected output to NOT contain {text!r} (crash/error signature)", result)


def file_exists(result, ctx, path, **_):
    p = Path(str(path).format(**ctx))
    if not p.exists():
        _fail(f"expected file to exist: {p}", result)


def file_not_exists(result, ctx, path, **_):
    p = Path(str(path).format(**ctx))
    if p.exists():
        _fail(f"expected file to NOT exist: {p} (regression: stray/leaked file)", result)


def file_contains(result, ctx, path, text, **_):
    p = Path(str(path).format(**ctx))
    if not p.exists():
        _fail(f"expected file to exist to check its content: {p}", result)
    if text not in p.read_text(errors="replace"):
        _fail(f"expected {p} to contain {text!r}", result)


def file_not_contains(result, ctx, path, text, **_):
    p = Path(str(path).format(**ctx))
    if p.exists() and text in p.read_text(errors="replace"):
        _fail(f"expected {p} to NOT contain {text!r}", result)


def _gcode_config(result, ctx, path):
    import settings_compare as sc

    p = Path(str(path).format(**ctx))
    if not p.exists():
        _fail(f"expected G-code to exist to read its config block: {p}", result)
    try:
        return sc.parse_gcode_config(p.read_text(errors="replace"))
    except ValueError as e:
        _fail(f"{p}: {e}", result)


def gcode_config_value(result, ctx, key, equals, path="{outputdir}/plate_1.gcode", **_):
    """The G-code config block records `key` as `equals` (string, or list for
    vector options -- compared element-wise, numerically tolerant)."""
    import settings_compare as sc

    cfg = _gcode_config(result, ctx, path)
    if key not in cfg:
        _fail(f"G-code config block has no key {key!r}", result)
    expected = [str(x) for x in equals] if isinstance(equals, list) else str(equals)
    if not sc.values_match(key, cfg[key], expected) or (
        isinstance(cfg[key], list) and isinstance(expected, list) and len(cfg[key]) != len(expected)
    ):
        _fail(f"G-code config {key!r}: expected {expected!r}, got {cfg[key]!r}", result)


def gcode_config_vector_nonzero(result, ctx, key, path="{outputdir}/plate_1.gcode", **_):
    """Every element of vector option `key` in the G-code config block is non-zero
    -- catches a partial CLI override that silently zero-fills the rest."""
    cfg = _gcode_config(result, ctx, path)
    vals = cfg.get(key)
    vals = vals if isinstance(vals, list) else [vals]
    zeros = [i for i, v in enumerate(vals) if v in ("0", "0.0", "nil", "")]
    if zeros:
        _fail(f"G-code config {key!r} has zero/empty elements at {zeros}: {vals!r}", result)


def gcode_metric(result, ctx, name, equals=None, min=None, max=None, index=0, path="{outputdir}/plate_1.gcode", **_):
    """A slicing metric parsed from the G-code (see gcode_metrics.py: layers,
    max_z_mm, filament_mm, e_sum_mm, print_time_s, ...) equals / is within
    bounds. Vector metrics (per-filament) are indexed with `index`."""
    import gcode_metrics as gm

    p = Path(str(path).format(**ctx))
    if not p.exists():
        _fail(f"expected G-code to exist: {p}", result)
    m = gm.parse_file(p)
    if name not in m:
        _fail(f"G-code has no metric {name!r}; available: {sorted(m)}", result)
    v = m[name][index] if isinstance(m[name], list) else m[name]
    if equals is not None and v != equals:
        _fail(f"metric {name}: expected {equals}, got {v}", result)
    if min is not None and v < min:
        _fail(f"metric {name}: expected >= {min}, got {v}", result)
    if max is not None and v > max:
        _fail(f"metric {name}: expected <= {max}, got {v}", result)


def gcode_z_above_bed(result, ctx, min=0.0, path="{outputdir}/plate_1.gcode", **_):
    """No G0/G1 move's Z coordinate (outside the CONFIG_BLOCK settings dump)
    goes below `min` -- catches a placement bug (e.g. --ensure-on-bed not
    applied) that sends the toolhead under the bed. Absolute Z only: a
    G91-relative Z segment would read wrong, but OrcaSlicer's own G-code
    doesn't use relative Z."""
    import builtins
    import re

    import settings_compare as sc

    p = Path(str(path).format(**ctx))
    if not p.exists():
        _fail(f"expected G-code to exist: {p}", result)
    t = p.read_text(errors="replace")
    if sc.BLOCK_START in t and sc.BLOCK_END in t:
        head, rest = t.split(sc.BLOCK_START, 1)
        body = head + rest.split(sc.BLOCK_END, 1)[1]
    else:
        body = t
    zs = [float(m.group(1)) for m in re.finditer(r"^G[01]\s[^;\n]*\bZ(-?\d+\.?\d*)", body, re.M)]
    if not zs:
        _fail("no G0/G1 move with a Z coordinate found in the G-code body", result)
    lowest = builtins.min(zs)
    if lowest < min:
        _fail(f"expected all G-code Z moves >= {min}, found Z={lowest}", result)


def gcode_body_count(result, ctx, text, equals=None, at_least=None, path="{outputdir}/plate_1.gcode", **_):
    """How many times `text` occurs in the *emitted* G-code (everything outside
    the `; CONFIG_BLOCK_START..END` settings dump, which merely records
    settings) -- e.g. a custom G-code snippet must be emitted once (start/end),
    once per layer (layer change), or once per tool change."""
    import settings_compare as sc

    p = Path(str(path).format(**ctx))
    if not p.exists():
        _fail(f"expected G-code to exist: {p}", result)
    t = p.read_text(errors="replace")
    if sc.BLOCK_START in t and sc.BLOCK_END in t:
        head, rest = t.split(sc.BLOCK_START, 1)
        body = head + rest.split(sc.BLOCK_END, 1)[1]
    else:
        body = t
    n = body.count(text)
    if equals is not None and n != equals:
        _fail(f"expected {text!r} to be emitted exactly {equals} time(s) in the G-code body, found {n}", result)
    if at_least is not None and n < at_least:
        _fail(f"expected {text!r} to be emitted at least {at_least} time(s) in the G-code body, found {n}", result)


def threemf_member_contains(result, ctx, path, member, text, **_):
    """A member file inside an exported 3mf (a zip) contains `text`."""
    import zipfile

    p = Path(str(path).format(**ctx))
    if not p.exists():
        _fail(f"expected 3mf to exist: {p}", result)
    with zipfile.ZipFile(p) as zf:
        if member not in zf.namelist():
            _fail(f"{p.name} has no member {member!r}; members: {zf.namelist()[:12]}", result)
        data = zf.read(member).decode("utf-8", errors="replace")
    if text not in data:
        _fail(f"expected {p.name}:{member} to contain {text!r}", result)


def threemf_member_absent(result, ctx, path, member_prefix, **_):
    """No member inside the exported 3mf starts with `member_prefix`
    (e.g. "3D/Objects/" to assert a mesh-less --min-save export)."""
    import zipfile

    p = Path(str(path).format(**ctx))
    if not p.exists():
        _fail(f"expected 3mf to exist: {p}", result)
    with zipfile.ZipFile(p) as zf:
        hits = [n for n in zf.namelist() if n.startswith(member_prefix)]
    if hits:
        _fail(f"expected no member under {member_prefix!r} in {p.name}, found {hits}", result)


def result_json_field(result, ctx, field, equals=None, **_):
    p = Path(ctx["outputdir"]) / "result.json"
    if not p.exists():
        _fail(f"expected {p} to exist to check field {field!r}", result)
    data = json.loads(p.read_text())
    if field not in data:
        _fail(f"result.json has no field {field!r}: {data}", result)
    if equals is not None and data[field] != equals:
        _fail(f"expected result.json[{field!r}] == {equals!r}, got {data[field]!r}", result)


CHECKS = {
    "no_crash": no_crash,
    "exit_code": exit_code,
    "stdout_contains": stdout_contains,
    "stderr_contains": stderr_contains,
    "output_not_contains": output_not_contains,
    "file_exists": file_exists,
    "file_not_exists": file_not_exists,
    "file_contains": file_contains,
    "file_not_contains": file_not_contains,
    "gcode_config_value": gcode_config_value,
    "gcode_config_vector_nonzero": gcode_config_vector_nonzero,
    "gcode_metric": gcode_metric,
    "gcode_z_above_bed": gcode_z_above_bed,
    "gcode_body_count": gcode_body_count,
    "threemf_member_contains": threemf_member_contains,
    "threemf_member_absent": threemf_member_absent,
    "result_json_field": result_json_field,
}
