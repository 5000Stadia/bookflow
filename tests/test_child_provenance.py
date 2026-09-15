"""Every child process this suite starts must be told which product to import.

A test that spawns the packaged launcher or a `python -c` snippet is measuring
the product only if the child actually loads the product under test. When it
does not, the child dies with `ModuleNotFoundError: No module named 'bookflow'`
and the suite reports a *product* failure for what is a harness mistake — 1074
red outcomes on 2026-09-15 were read through that fog.

So this file checks the rule rather than any one caller of it: the owner
(tests/provenance.py) hands a child a pinned import path and nothing it was not
given, and no spawn site in tests/ quietly goes back to a replaced environment
that carries no provenance at all.
"""

import ast
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import bookflow
from tests import provenance
from tests.conftest import BIN

TESTS = Path(__file__).resolve().parent
SPAWN = ("StdioServerParameters", "subprocess.run", "subprocess.Popen",
         "subprocess.check_output", "subprocess.check_call", "subprocess.call")


# --- the rule at runtime ----------------------------------------------------

def test_a_child_imports_the_same_product_this_process_imported():
    """Pinned, not hoped for: the child reports the file the parent is running."""
    assert provenance.resolve_child_artifact() == str(Path(bookflow.__file__).resolve())


def test_the_pin_is_what_does_it_not_the_interpreter():
    """The negative control. Without the pin the child gets whatever the interpreter
    has — which in a source checkout that was never installed is nothing at all."""
    stripped = {name: value for name, value in provenance.child_env().items() if name != "PYTHONPATH"}
    completed = subprocess.run([sys.executable, "-c", "import bookflow; print(bookflow.__file__)"],
                               capture_output=True, text=True, env=stripped,
                               cwd=str(Path(sys.executable).parent))
    installed = provenance.installed_product()
    if installed is None:
        assert completed.returncode != 0 and "No module named 'bookflow'" in completed.stderr, completed.stderr
    else:
        # An installed interpreter still resolves something; the pin is what
        # decides *which* something, and it must win over the installation.
        assert str(Path(completed.stdout.strip()).resolve()) == installed


def test_the_packaged_launcher_runs_under_the_owners_environment():
    """The fresh-install reproduction, in one line: the launcher must start."""
    completed = subprocess.run([str(BIN), "--help"], capture_output=True, text=True,
                               env=provenance.child_env())
    assert completed.returncode == 0, completed.stderr
    assert "No module named 'bookflow'" not in completed.stderr


def test_a_child_is_given_only_what_the_test_named(monkeypatch):
    """A child must not inherit a credential the test did not hand it."""
    monkeypatch.setenv("BOOKFLOW_TOKEN", "parent-secret-that-must-not-travel")
    monkeypatch.setenv("BOOKFLOW_URL", "http://parent.invalid")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unrelated-credential")
    environment = provenance.child_env(BOOKFLOW_DATA_ROOT="/nowhere")
    assert "BOOKFLOW_TOKEN" not in environment and "BOOKFLOW_URL" not in environment
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    assert set(environment) <= {*provenance.PASSTHROUGH, "PYTHONPATH", "BOOKFLOW_DATA_ROOT"}
    # And what the test did name arrives unchanged.
    assert provenance.child_env(BOOKFLOW_TOKEN="named")["BOOKFLOW_TOKEN"] == "named"


def test_the_owner_refuses_a_second_spelling_of_the_import_path():
    with pytest.raises(TypeError, match="PYTHONPATH"):
        provenance.child_env(PYTHONPATH="/somewhere")


def test_the_owner_refuses_a_pin_at_a_path_that_holds_no_product(tmp_path):
    """A historical checkout that failed to extract is caught here, not in a child's stderr."""
    with pytest.raises(ValueError, match="No bookflow package"):
        provenance.child_env(tmp_path)


def test_a_missing_installation_is_said_plainly_rather_than_thrown(monkeypatch):
    """Requirement four: a test that needs an installed product must say so."""
    monkeypatch.setattr(provenance, "installed_product", lambda: None)
    with pytest.raises(BaseException) as stopped:
        provenance.require_installed_product("This witness")
    message = str(getattr(stopped.value, "msg", stopped.value))
    assert "This witness requires the product installed" in message
    assert "No module named" not in message


# --- failing fast, because a wait with no bound is unmeasurable -------------

def test_a_child_that_cannot_start_is_a_message_not_a_wait(tmp_path, monkeypatch):
    """The 2.2-hour wedge, in reverse: the run must end in seconds with a reason.

    A launcher that never answers stands in for the real failure — a child that
    dies before it can speak, with the parent already waiting on its pipe.
    """
    stuck = tmp_path / "launcher-that-never-answers"
    stuck.write_text("#!/bin/sh\nsleep 3600\n")
    stuck.chmod(0o700)
    monkeypatch.setenv("BOOKFLOW_MCP_TEST_BINARY", str(stuck))
    started = time.perf_counter()
    with pytest.raises(provenance.ChildProvenanceError) as stopped:
        provenance.preflight(seconds=2, force=True)
    waited = time.perf_counter() - started
    assert waited < 30, f"a bounded preflight waited {waited:.1f}s"
    assert "did not start as a child within 2" in str(stopped.value)
    assert str(stuck) in str(stopped.value)


def test_a_child_that_dies_at_once_reports_what_it_said(tmp_path, monkeypatch):
    """The ModuleNotFoundError case, told as a sentence rather than a traceback."""
    broken = tmp_path / "launcher-that-dies"
    broken.write_text("#!/bin/sh\necho 'ModuleNotFoundError: No module named bookflow' >&2\nexit 1\n")
    broken.chmod(0o700)
    monkeypatch.setenv("BOOKFLOW_MCP_TEST_BINARY", str(broken))
    with pytest.raises(provenance.ChildProvenanceError) as stopped:
        provenance.preflight(force=True)
    message = str(stopped.value)
    assert "could not start as a child" in message
    assert "No module named bookflow" in message, "the child's own words must reach the reader"
    assert str(provenance.parent_source_root()) in message, "and the path it was pinned to"


def test_the_preflight_runs_once_and_then_stays_out_of_the_way():
    """It is a session smoke test, not a tax on every spawn."""
    provenance.preflight(force=True)
    started = time.perf_counter()
    for _ in range(50):
        provenance.child_env()
    assert time.perf_counter() - started < 2


def test_every_wait_the_owner_reaches_is_bounded():
    """Named bounds, and the two owners that apply them."""
    assert 0 < provenance.PREFLIGHT_SECONDS <= provenance.CHILD_SECONDS
    assert 0 < provenance.HANDSHAKE_SECONDS <= provenance.CHILD_SECONDS
    conftest = (TESTS / "conftest.py").read_text()
    assert "timeout=provenance.CHILD_SECONDS" in conftest, (
        "tests.conftest.Cli must bound the CLI witness it starts")
    assert "ChildProvenanceError" in conftest, "and say which command ran out of time"
    matrix = (TESTS / "mcp_matrix_support.py").read_text()
    assert "anyio.fail_after(provenance.HANDSHAKE_SECONDS)" in matrix, (
        "the parity matrix must bound the child's first reply")


# --- the rule in the source, so it cannot quietly come back -----------------

def _spawn_calls():
    """(path, lineno, env expression) for every spawn call in tests/ that names an env."""
    for path in sorted(TESTS.glob("*.py")):
        source = path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            parts, cursor = [], node.func
            while isinstance(cursor, ast.Attribute):
                parts.append(cursor.attr)
                cursor = cursor.value
            if isinstance(cursor, ast.Name):
                parts.append(cursor.id)
            if ".".join(reversed(parts)) not in SPAWN:
                continue
            for keyword in node.keywords:
                if keyword.arg == "env":
                    yield path, node.lineno, ast.get_source_segment(source, keyword.value) or ""


def test_no_spawn_site_hands_a_child_a_replaced_environment_without_provenance():
    """The exact regression: an env built from nothing, carrying no import path.

    Either the expression comes from the owner, or it starts from the parent's
    own environment (whose provenance is the parent's), or it states an import
    path itself. A bare `{'BOOKFLOW_TOKEN': ...}` is none of those, and the
    child it starts cannot import the product it is supposed to be testing.
    """
    offenders = []
    for path, lineno, expression in _spawn_calls():
        flat = " ".join(expression.split())
        if "provenance.child_env" in flat or "provenance_owner.child_env" in flat:
            continue
        if "os.environ" in flat or "PYTHONPATH" in flat:
            continue
        if not flat.startswith("{") and not flat.startswith("dict("):
            continue  # a name; the binding it points at is checked where it is built
        offenders.append(f"{path.name}:{lineno} env={flat[:90]}")
    assert not offenders, (
        "These spawn sites replace the child's environment without giving it an import path, "
        "so the child cannot import bookflow:\n  " + "\n  ".join(offenders))


def test_the_two_sites_the_suite_measures_itself_through_use_the_owner():
    """Named outright, because a regression in either one re-reds hundreds of nodes."""
    matrix = (TESTS / "mcp_matrix_support.py").read_text()
    assert "provenance.child_env(" in matrix and "provenance.launcher()" in matrix
    conftest = (TESTS / "conftest.py").read_text()
    assert "provenance.child_env(" in conftest, "tests.conftest.Cli must spawn through the owner"
    assert "BIN = Path(provenance.launcher())" in conftest


def test_only_the_owner_decides_which_launcher_a_child_runs():
    """One rule in one place. This is the duplication that caused the fog."""
    elsewhere = []
    for path in sorted(TESTS.glob("*.py")):
        if path.name in ("provenance.py", Path(__file__).name):
            continue  # the owner, and this census that quotes the spelling it forbids
        text = path.read_text()
        for spelling in ("with_name(" + "'bookflow')", 'with_name(' + '"bookflow")'):
            if spelling in text:
                elsewhere.append(f"{path.name}: {spelling}")
    assert not elsewhere, (
        "The launcher is resolved by tests.provenance.launcher() and nowhere else; "
        "these files derive it again:\n  " + "\n  ".join(elsewhere))


def test_every_spawn_sites_environment_is_accounted_for():
    """A census, so a new shape of env expression cannot slip past unnoticed."""
    shapes = set()
    for _, _, expression in _spawn_calls():
        flat = " ".join(expression.split())
        if "child_env" in flat:
            shapes.add("owner")
        elif "os.environ" in flat:
            shapes.add("inherits the parent")
        elif flat.isidentifier():
            shapes.add("a name bound elsewhere")
        else:
            shapes.add(flat[:60])
    assert shapes <= {"owner", "inherits the parent", "a name bound elsewhere", "environment"}, shapes
