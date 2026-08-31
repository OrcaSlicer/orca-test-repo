"""Shared pytest fixtures for the OrcaSlicer CLI black-box suite.

Nothing here links against OrcaSlicer source — every fixture drives the
already-built `orca-slicer` binary as a subprocess. See TESTING_STRATEGY.md.
"""
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from case_registry import CASES_BY_ID

REPO_ROOT = Path(__file__).resolve().parent
TEST_PROJECTS = REPO_ROOT / "test_projects"
DATA_DIR_SEED = REPO_ROOT / "data_dir"


def pytest_collection_modifyitems(config, items):
    """Mark `status: open` cases xfail(strict) -- see test_cases.py docstring."""
    for item in items:
        if not item.name.startswith("test_case["):
            continue
        case = CASES_BY_ID.get(item.callspec.id)
        if case is None:
            continue
        if case["status"] == "open":
            item.add_marker(
                pytest.mark.xfail(
                    reason=f"{case['id']}: known open bug on the current build; "
                    f"checks assert the desired behavior (see {case['_file']})",
                    strict=True,
                )
            )
        item.add_marker(getattr(pytest.mark, case["category"].replace("-", "_")))


def pytest_addoption(parser):
    parser.addoption(
        "--orca-bin",
        action="store",
        default=None,
        help="Path to the orca-slicer CLI binary under test (or set $ORCA_BIN).",
    )
    parser.addoption(
        "--orca-source",
        action="store",
        default=None,
        help="Path to an OrcaSlicer source checkout (or set $ORCA_SOURCE): the CLI option surface is "
             "enumerated live from it instead of the committed cases/_snapshots/cli_surface_full.json.",
    )
    parser.addoption(
        "--fuzz-sample",
        action="store",
        type=int,
        default=25,
        help="How many (option, poison-value) pairs test_config_fuzz.py runs by default "
             "(a daily-rotating but reproducible-within-a-day sample). See TESTING_STRATEGY.md.",
    )
    parser.addoption(
        "--fuzz-full",
        action="store_true",
        default=False,
        help="Run test_config_fuzz.py's full sweep instead of a sample (slow -- intended for a "
             "scheduled/nightly run, not routine local use).",
    )


@pytest.fixture(scope="session")
def orca_bin(request):
    import os

    path = request.config.getoption("--orca-bin") or os.environ.get("ORCA_BIN")
    if not path:
        pytest.exit(
            "Pass --orca-bin /path/to/orca-slicer (or set $ORCA_BIN) so the suite "
            "knows which build to test.",
            returncode=2,
        )
    p = Path(path).expanduser().resolve()
    if not p.exists():
        pytest.exit(f"orca-slicer binary not found: {p}", returncode=2)
    if p.is_dir():
        # A common mistake: pointing --orca-bin at a package/install directory
        # instead of the executable inside it. Left unchecked, subprocess.run()
        # raises a raw, confusing "PermissionError: [Errno 13] Permission
        # denied" when it tries to execve() the directory -- catch it here with
        # a message that says what actually went wrong, and suggest a fix if an
        # obvious candidate binary is sitting right there.
        candidates = [
            c for c in (p / "orca-slicer", p / "bin" / "orca-slicer")
            if c.is_file() and os.access(c, os.X_OK)
        ]
        hint = f" Did you mean: {candidates[0]}?" if candidates else ""
        pytest.exit(f"--orca-bin points at a directory, not the binary itself: {p}.{hint}", returncode=2)
    if not os.access(p, os.X_OK):
        pytest.exit(f"orca-slicer binary is not executable: {p}", returncode=2)
    return p


@pytest.fixture(scope="session")
def seeded_data_dir(tmp_path_factory):
    """One copy-on-session of the checked-in data_dir/ (printer & filament profiles).

    orca-slicer writes a machine-id file and a hint cache into --datadir at
    runtime, so tests must never point --datadir at the repo's own data_dir/
    directly -- that would mutate a tracked fixture on every run.
    """
    dest = tmp_path_factory.mktemp("data_dir_seed")
    shutil.copytree(DATA_DIR_SEED, dest, dirs_exist_ok=True)
    return dest


@pytest.fixture
def datadir(tmp_path_factory, seeded_data_dir):
    """Per-test --datadir, pre-seeded with real printer/filament profiles."""
    dest = tmp_path_factory.mktemp("datadir")
    shutil.copytree(seeded_data_dir, dest, dirs_exist_ok=True)
    return dest


@pytest.fixture
def empty_datadir(tmp_path):
    """A --datadir path that deliberately does not exist yet (and whose parent
    may not either) -- for exercising datadir-creation code paths."""
    return tmp_path / "empty_datadir"


@pytest.fixture
def outputdir(tmp_path):
    d = tmp_path / "result"
    d.mkdir()
    return d


@pytest.fixture
def scratch(tmp_path):
    return tmp_path


@pytest.fixture
def model():
    def _model(name):
        p = TEST_PROJECTS / name
        if not p.exists():
            pytest.fail(f"missing test fixture model: {p}")
        return p

    return _model


@dataclass
class RunResult:
    args: list
    returncode: int
    stdout: str
    stderr: str
    duration: float
    cwd: Path
    extra: dict = field(default_factory=dict)


def pytest_configure(config):
    """Point the shared surface loader at a source checkout if one was given, so
    every consumer (parser types, sweeps, GUI guard) uses the definitions the
    binary under test was built from instead of the committed snapshot."""
    import surface

    src = config.getoption("--orca-source", default=None)
    if src:
        surface.set_source_dir(src)


def assert_has_cli_action(argv):
    """With no *action* flag present the binary starts the GUI instead
    (OrcaSlicer.cpp: `start_gui = m_actions.empty() && !downward_check`), which
    would block a test behind a window -- refuse such an argv up front."""
    import surface

    given = {str(a).split("=", 1)[0] for a in argv}
    if not given & surface.action_flags():
        raise RuntimeError(
            "refusing to run orca-slicer without an action flag (--slice/--info/--export-*/...): "
            "the CLI would open the GUI and block. argv: " + " ".join(map(str, argv))
        )


def headless_env():
    """Child environment with no display, so an invocation that still slips
    into GUI mode fails immediately instead of opening a window."""
    import os

    env = dict(os.environ)
    for k in ("DISPLAY", "WAYLAND_DISPLAY"):
        env.pop(k, None)
    return env


@pytest.fixture
def run_orca(orca_bin, tmp_path):
    def _run(args, cwd=None, timeout=300):
        cwd_path = Path(cwd) if cwd else tmp_path
        cwd_path.mkdir(parents=True, exist_ok=True)
        argv = [str(orca_bin)] + [str(a) for a in args]
        assert_has_cli_action(argv[1:])
        start = time.time()
        proc = subprocess.run(
            argv, cwd=str(cwd_path), capture_output=True, text=True, timeout=timeout, env=headless_env()
        )
        return RunResult(argv, proc.returncode, proc.stdout, proc.stderr, time.time() - start, cwd_path)

    return _run
