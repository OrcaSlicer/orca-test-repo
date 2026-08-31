"""Loads cases/**/*.yaml once, shared by conftest.py (xfail marking) and
test_cases.py (the actual parametrized runner)."""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent
CASE_FILES = sorted((REPO_ROOT / "cases").rglob("*.yaml"))


def _load_cases():
    cases = []
    seen_ids = set()
    for f in CASE_FILES:
        data = yaml.safe_load(f.read_text())
        data["_file"] = str(f.relative_to(REPO_ROOT))
        if data["id"] in seen_ids:
            raise ValueError(f"duplicate case id {data['id']!r} in {f}")
        seen_ids.add(data["id"])
        cases.append(data)
    return cases


CASES = _load_cases()
CASES_BY_ID = {c["id"]: c for c in CASES}
