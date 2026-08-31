"""Loads cases/**/*.yaml once, shared by conftest.py (xfail marking) and
test_cases.py (the actual parametrized runner).

Check names and their parameters are validated here rather than at call time:
every check is dispatched from YAML, so a misspelled parameter would otherwise
turn a check into a silent no-op that passes forever. Validating at load means
`pytest --collect-only` (this repo's fast CI gate) catches it.
"""
import inspect
from pathlib import Path

import yaml

from checks import CHECKS

REPO_ROOT = Path(__file__).resolve().parent
CASE_FILES = sorted((REPO_ROOT / "cases").rglob("*.yaml"))


def _check_params(fn) -> set:
    return {name for name in inspect.signature(fn).parameters} - {"result", "ctx"}


def _validate_checks(data, f):
    for entry in data.get("checks") or []:
        if len(entry) != 1:
            raise ValueError(f"{f}: each check entry is one 'name: params' mapping, got {entry!r}")
        ((name, params),) = entry.items()
        if name not in CHECKS:
            raise ValueError(f"{f}: unknown check {name!r}; known: {sorted(CHECKS)}")
        unknown = set(params or {}) - _check_params(CHECKS[name])
        if unknown:
            raise ValueError(f"{f}: check {name!r} got unknown parameter(s) {sorted(unknown)}; "
                             f"accepted: {sorted(_check_params(CHECKS[name]))}")
        if name == "exit_code":
            if not (params or {}):
                raise ValueError(f"{f}: exit_code needs one of equals/one_of/not_equals")
            if "one_of" in (params or {}) and not isinstance(params["one_of"], list):
                raise ValueError(f"{f}: exit_code one_of takes a list, got {params['one_of']!r}")


def _load_cases():
    cases = []
    seen_ids = set()
    for f in CASE_FILES:
        data = yaml.safe_load(f.read_text())
        data["_file"] = str(f.relative_to(REPO_ROOT))
        if data["id"] in seen_ids:
            raise ValueError(f"duplicate case id {data['id']!r} in {f}")
        if data["id"] != f.stem:
            raise ValueError(f"{f}: case id {data['id']!r} must match the filename")
        seen_ids.add(data["id"])
        _validate_checks(data, data["_file"])
        cases.append(data)
    return cases


CASES = _load_cases()
CASES_BY_ID = {c["id"]: c for c in CASES}
