"""Metrics OrcaSlicer writes into every G-code file, parsed into numbers.

Header (top of file):
    ; model printing time: 10m 55s; total estimated time: 17m 35s
    ; total layer number: 100
    ; max_z_height: 20.00
Footer:
    ; filament used [mm] = 1405.11, 655.30      (one value per filament)
    ; filament used [cm3] = 3.38
    ; filament used [g] = 4.21                  (absent when filament_density is 0)
Plus two derived from the body:
    e_sum_mm   -- sum of all positive E moves in G0/G1 lines outside the config
                  block (relative-E files; for absolute-E files the deltas).
                  Deterministic per slice; it is NOT equal to "filament used"
                  because that figure is the slicer's own estimate and excludes
                  e.g. the printer profile's start-G-code purge line.
    size_bytes -- file size.

These are the "golden" quantities test_golden_slices.py compares against
baseline.json: they change when the slicing result changes, unlike byte size
alone which can stay put while the print is wrong.
"""
from __future__ import annotations

import re
from pathlib import Path

BLOCK_START = "; CONFIG_BLOCK_START"
BLOCK_END = "; CONFIG_BLOCK_END"


def _duration_s(text: str) -> int:
    total = 0
    for value, unit in re.findall(r"(\d+)\s*([dhms])", text):
        total += int(value) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return total


def _body(text: str) -> str:
    if BLOCK_START in text and BLOCK_END in text:
        head, rest = text.split(BLOCK_START, 1)
        return head + rest.split(BLOCK_END, 1)[1]
    return text


def e_sum(text: str) -> float:
    body = _body(text)
    relative = re.search(r"^; use_relative_e_distances = (\d)", text, re.M)
    relative = relative is None or relative.group(1) == "1"
    total, last = 0.0, 0.0
    # OrcaSlicer writes E without a leading zero ("E.04213"), so accept ".123" as well as "1.23"
    for m in re.finditer(r"^G[01]\s[^;\n]*?\bE(-?(?:\d+\.?\d*|\.\d+))", body, re.M):
        e = float(m.group(1))
        if relative:
            if e > 0:
                total += e
        else:
            if e > last:
                total += e - last
            last = e
        if not relative and re.match(r"^G92\s", m.group(0)):
            last = 0.0
    return round(total, 3)


def parse(text: str, size_bytes: int | None = None) -> dict:
    m = {}
    if (x := re.search(r"^; total layer number: (\d+)", text, re.M)):
        m["layers"] = int(x.group(1))
    if (x := re.search(r"^; max_z_height: ([\d.]+)", text, re.M)):
        m["max_z_mm"] = float(x.group(1))
    if (x := re.search(r"^; model printing time: ([^;\n]+); total estimated time: ([^\n]+)", text, re.M)):
        m["print_time_s"] = _duration_s(x.group(1))
        m["total_time_s"] = _duration_s(x.group(2))
    if (x := re.search(r"^; filament used \[mm\] = ([\d., ]+)", text, re.M)):
        m["filament_mm"] = [float(v) for v in x.group(1).split(",")]
    if (x := re.search(r"^; filament used \[cm3\] = ([\d., ]+)", text, re.M)):
        m["filament_cm3"] = [float(v) for v in x.group(1).split(",")]
    if (x := re.search(r"^; filament used \[g\] = ([\d., ]+)", text, re.M)):
        m["filament_g"] = [float(v) for v in x.group(1).split(",")]
    m["e_sum_mm"] = e_sum(text)
    if size_bytes is not None:
        m["size_bytes"] = size_bytes
    return m


def parse_file(path: Path) -> dict:
    return parse(path.read_text(errors="replace"), size_bytes=path.stat().st_size)


# Per-metric tolerance: exact for counts/heights, relative for continuous ones.
TOLERANCES = {
    "layers": ("exact", 0),
    "max_z_mm": ("abs", 0.011),
    "filament_mm": ("rel", 0.02),
    "filament_cm3": ("rel", 0.02),
    "filament_g": ("rel", 0.02),
    "e_sum_mm": ("rel", 0.02),
    "print_time_s": ("rel", 0.05),
    "total_time_s": ("rel", 0.05),
    "size_bytes": ("rel", 0.10),
}


def compare(actual: dict, expected: dict) -> list[str]:
    """Human-readable list of metrics outside tolerance ([] when all agree)."""
    problems = []
    for name, exp in expected.items():
        if name not in actual:
            problems.append(f"{name}: expected {exp!r} but the G-code has no such metric")
            continue
        kind, tol = TOLERANCES.get(name, ("rel", 0.05))
        got = actual[name]
        exp_l = exp if isinstance(exp, list) else [exp]
        got_l = got if isinstance(got, list) else [got]
        if len(exp_l) != len(got_l):
            problems.append(f"{name}: expected {exp!r}, got {got!r} (different length)")
            continue
        for i, (e, g) in enumerate(zip(exp_l, got_l)):
            ok = (g == e) if kind == "exact" else (abs(g - e) <= tol) if kind == "abs" else (abs(g - e) <= tol * max(abs(e), 1e-9))
            if not ok:
                idx = f"[{i}]" if isinstance(exp, list) else ""
                problems.append(f"{name}{idx}: expected {e} (tolerance {kind} {tol}), got {g}")
    return problems
