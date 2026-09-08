"""Generic runner for cases/**/*.yaml.

This is the whole regression layer: every known CLI bug gets one declarative
case file (CLI args + expected checks), not a bespoke Python test function.
Adding coverage for a new bug or a new feature/config almost never touches
this file -- see TESTING_STRATEGY.md "Adding a new case".

Case `status`:
  fixed         -- must pass. A failure here is a real regression.
  open          -- known, currently-failing upstream bug. Marked xfail(strict)
                   so it doesn't block CI, but the day it starts passing
                   unexpectedly (upstream fixed it) pytest turns that XPASS
                   into a hard failure, forcing a status bump to `fixed`.
  needs_fixture -- the case is written but a supporting .3mf/profile fixture
                   doesn't exist yet. Collected (visible in --collect-only,
                   i.e. it's a tracked TODO) but skipped, not silently absent.
"""
import pytest

import presets
from case_registry import CASES, REPO_ROOT
from checks import CHECKS


class _FlatFixtures:
    """`{flat_fixtures}` -- the preset fixtures made self-contained against the
    datadir under test. Only a preset with no `inherits` can be loaded from
    outside the datadir, so a case that hands the CLI a fixture preset uses
    this instead of `{fixtures}`. Flattened on first use, not per case."""

    def __init__(self, datadir, scratch):
        self._datadir, self._out, self._done = datadir, scratch / "flat_presets", False

    def __str__(self):
        if not self._done:
            for src in sorted((REPO_ROOT / "test_projects" / "settings").glob("*.json")):
                try:
                    presets.flatten_to(src, self._datadir, self._out)
                except ValueError:
                    continue  # not a preset (project settings, custom gcode list)
            self._done = True
        return str(self._out)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_case(case, run_orca, datadir, empty_datadir, outputdir, scratch, model):
    if case["status"] == "needs_fixture":
        pytest.skip(f"{case['id']}: {case.get('fixture_todo', 'fixture not authored yet -- see TESTING_STRATEGY.md')}")

    ctx = {
        "datadir": datadir,
        "empty_datadir": empty_datadir,
        "outputdir": outputdir,
        "cwd": scratch,
        "fixtures": REPO_ROOT / "test_projects" / "settings",
        "flat_fixtures": _FlatFixtures(datadir, scratch),
    }
    if "model" in case:
        ctx["model"] = model(case["model"])

    args = [str(a).format(**ctx) for a in case["args"]]
    result = run_orca(args, cwd=scratch, timeout=case.get("timeout", 300))

    failures = []
    for check_entry in case["checks"]:
        ((name, params),) = check_entry.items()
        fn = CHECKS[name]  # names are validated at load time (case_registry)
        try:
            fn(result, ctx, **(params or {}))
        except AssertionError as e:
            failures.append(f"[{name}] {e}")

    if failures:
        pytest.fail(f"{case['id']} -- {case['title']}\n" + "\n---\n".join(failures), pytrace=False)
