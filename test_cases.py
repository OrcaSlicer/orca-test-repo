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

from case_registry import CASES, REPO_ROOT
from checks import CHECKS


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_case(case, run_orca, datadir, empty_datadir, outputdir, scratch, model):
    if case["status"] == "needs_fixture":
        pytest.skip(f"{case['id']}: {case.get('fixture_todo', 'fixture not authored yet -- see TESTING_STRATEGY.md')}")

    ctx = {
        "datadir": datadir,
        "empty_datadir": empty_datadir,
        "outputdir": outputdir,
        "scratch": scratch,
        "cwd": scratch,
        "fixtures": REPO_ROOT / "test_projects" / "settings",
    }
    if "model" in case:
        ctx["model"] = model(case["model"])

    args = [str(a).format(**ctx) for a in case["args"]]
    result = run_orca(args, cwd=scratch, timeout=case.get("timeout", 300))

    failures = []
    for check_entry in case["checks"]:
        ((name, params),) = check_entry.items()
        try:
            CHECKS[name](result, ctx, **(params or {}))
        except KeyError:
            raise KeyError(f"{case['id']}: unknown check {name!r} in {case['_file']}")
        except AssertionError as e:
            failures.append(f"[{name}] {e}")

    if failures:
        pytest.fail(f"{case['id']} -- {case['title']}\n" + "\n---\n".join(failures), pytrace=False)
