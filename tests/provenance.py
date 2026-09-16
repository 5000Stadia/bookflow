"""The one owner of how a spawned child is told where to find the product.

Every test in this suite that starts a *child process running Bookflow* — the
packaged console script, `python -m bookflow...`, or a `python -c` snippet that
imports `bookflow` — has to answer the same question: which copy of the product
does that child import? There is exactly one answer, and it lives here.

Why this file exists
--------------------
The answer used to be spelled out at each spawn site, and the spellings
disagreed. Some sites handed the child a *replaced* environment holding only
``BOOKFLOW_TOKEN``/``BOOKFLOW_COMPANY``, so no import path reached it at all;
under a checkout that is not installed into the test interpreter the child died
with ``ModuleNotFoundError: No module named 'bookflow'`` and the suite reported
it as a product failure. Other sites inherited the parent environment wholesale,
which works only by accident — it happens to carry whatever ``PYTHONPATH`` the
operator typed, so the child's provenance is whatever the shell had, never
something the test stated.

Both are the same defect: the child's provenance was implicit. Here it is
explicit, pinned, and asserted.

The rule
--------
``child_env()`` builds the environment for a child. It starts from a small,
named passthrough list — never from ``os.environ`` wholesale — pins the import
path to the artifact **the parent itself imported**, and then applies exactly
what the caller passed. Nothing else crosses the boundary: no tokens, no data
root, no credentials the test did not name.

A test that needs the child to run a *different* copy of the product — a
historical checkout extracted with ``git archive``, say — passes that copy as
``child_env()``'s first argument. That is still an explicit, pinned provenance;
it is just pinned somewhere other than the parent's own source.

A test that genuinely requires the product to be **installed** into the test
interpreter, rather than merely importable from a source path, calls
``require_installed_product()``. It stops with a sentence a reader can act on
instead of dying on an import error that looks like a defect in the product.

Failing fast
------------
A child that cannot start is worse than a child that fails: the parent opens a
pipe to a process that is already dead and then waits on it. One such wait was
found wedged for 2.2 hours — asleep on a stream nothing would ever write to —
where a suite that reported the failure in a second would have cost nothing. So
the owner also bounds the waits it can reach. The first time a test builds a
child environment, `preflight()` starts the product as a child once, under
``PREFLIGHT_SECONDS``, and stops the run with a sentence naming the launcher and
the child's own stderr if it cannot answer. `CHILD_SECONDS` bounds a single CLI
witness, and `HANDSHAKE_SECONDS` bounds an MCP child's first reply. A bound is
not a target: these are set far above what healthy work takes, and exist so that
a broken child costs a message rather than a night.
"""

from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
from pathlib import Path

import bookflow

__all__ = [
    "CHILD_SECONDS",
    "ChildProvenanceError",
    "HANDSHAKE_SECONDS",
    "PASSTHROUGH",
    "PREFLIGHT_SECONDS",
    "child_env",
    "installed_product",
    "launcher",
    "parent_source_root",
    "preflight",
    "require_installed_product",
    "resolve_child_artifact",
]


#: Bounds, not targets. Each is far above what the healthy case takes, and each
#: exists so that a child that never answers costs a message instead of a night.
PREFLIGHT_SECONDS = 60      # the product, started once as a child, saying anything
CHILD_SECONDS = 300         # one CLI witness, start to exit
HANDSHAKE_SECONDS = 90      # an MCP child's first reply over its own pipe

# The floor for a test that drives the four-surface Matrix, and the reason it is derived rather
# than chosen. Such a test is permitted to spend PREFLIGHT_SECONDS starting a child, then
# HANDSHAKE_SECONDS waiting for the MCP child to speak, then CHILD_SECONDS on a *single* CLI
# witness -- and it makes many. A per-test cap below that sum can kill a run while a call it is
# expressly allowed to make is still inside its own bound, which is not a timeout, it is a test
# killing itself. `test_purchase_deletion_transports` carried 180 and was killed at 181.29s and
# 181.47s in two independent runs doing nothing wrong.
#
# This is a floor, not a budget: a cap must be high enough to be incoherent to trip by accident,
# and low enough to catch a genuine hang. Raise a test above it when the test earns it; never
# set one below it.
MATRIX_SECONDS = PREFLIGHT_SECONDS + HANDSHAKE_SECONDS + CHILD_SECONDS


class ChildProvenanceError(AssertionError):
    """A child could not be started or could not answer. Says which, and why."""


#: Variables a child process legitimately needs from the parent to be a working
#: process at all — how to find programs, where to write temporary files, how to
#: format text, how to verify TLS. Deliberately *not* here: anything holding a
#: credential, a session, a data root, or a URL. A test that wants a child to
#: see one of those passes it by name.
PASSTHROUGH = (
    "PATH",
    "HOME",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "XDG_RUNTIME_DIR",
    # Windows needs these to start a process at all; harmless where unset.
    "SYSTEMROOT",
    "COMSPEC",
    "PATHEXT",
    # Deliberate suite-wide tuning set in tests/conftest.py, not a credential:
    # a child that waited the production lock timeout would hang the suite.
    "BOOKFLOW_LOCK_TIMEOUT",
)


def parent_source_root() -> Path:
    """The directory that must be on a child's import path to reach *this* product.

    Read off the package the parent process actually imported, so the child is
    pinned to the same artifact rather than to a path the test hoped was right.
    """
    return Path(bookflow.__file__).resolve().parent.parent


@functools.lru_cache(maxsize=1)
def installed_product() -> str | None:
    """Where the test interpreter finds `bookflow` with no import path help, if anywhere.

    Returns the resolved ``bookflow/__init__.py`` of a genuine installation, or
    ``None`` when the interpreter can only reach the product through an
    explicitly supplied source path. Editable installs that still point at a
    live directory count as installed; a dangling editable pointer does not.
    """
    environment = {name: os.environ[name] for name in PASSTHROUGH if name in os.environ}
    environment["PYTHONPATH"] = ""
    probed = subprocess.run(
        [sys.executable, "-c", "import bookflow; print(bookflow.__file__)"],
        capture_output=True, text=True, cwd=str(Path(sys.executable).parent), env=environment)
    return str(Path(probed.stdout.strip()).resolve()) if probed.returncode == 0 else None


def require_installed_product(what: str) -> str:
    """Stop with a readable sentence when a test needs a real installation and has none.

    `what` names the thing under test, so the message says which requirement is
    unmet rather than leaving a reader to infer it from a traceback.
    """
    import pytest

    found = installed_product()
    if found is None:
        pytest.skip(
            f"{what} requires the product installed into the test interpreter "
            f"({sys.executable}), and it is not: `import bookflow` fails there without "
            f"PYTHONPATH. Install the checkout into that interpreter "
            f"(`pip install -e .`) and re-run. The parent process is importing "
            f"{bookflow.__file__} from an explicit source path, which is not an "
            f"installation and cannot stand in for one here.")
    return found


def launcher(*, required: bool = False) -> str:
    """The packaged console script a child should be started as.

    ``BOOKFLOW_MCP_TEST_BINARY`` names a launcher explicitly; otherwise it is the
    ``bookflow`` script beside the running interpreter. With `required`, a
    missing script stops the test with a sentence instead of an exec error.
    """
    binary = os.environ.get("BOOKFLOW_MCP_TEST_BINARY") or str(Path(sys.executable).with_name("bookflow"))
    if required and not Path(binary).is_file():
        import pytest

        pytest.fail(
            f"No Bookflow launcher at {binary}. Install the checkout's entry point beside "
            f"the test interpreter, or set BOOKFLOW_MCP_TEST_BINARY to the launcher to use.")
    return binary


_started_once = False


def preflight(*, seconds: float | None = None, force: bool = False) -> None:
    """Start the product as a child once, under a bound, before anything opens a pipe.

    The failure this exists for is not a child that errors — it is a child that
    dies before it can speak, leaving the parent asleep on a stream nothing will
    ever write to. Finding that out here costs a second and prints the child's
    own stderr; finding it out at a session handshake costs however long anyone
    is willing to wait.

    Runs at most once per session unless `force`.
    """
    global _started_once
    if _started_once and not force:
        return
    _started_once = True
    bound = PREFLIGHT_SECONDS if seconds is None else seconds
    binary = launcher()
    command = [binary, "--help"] if Path(binary).is_file() else [sys.executable, "-c", "import bookflow"]
    environment = _environment(None)
    try:
        started = subprocess.run(command, capture_output=True, text=True, timeout=bound,
                                 env=environment, cwd=str(Path(sys.executable).parent))
    except subprocess.TimeoutExpired:
        raise ChildProvenanceError(
            f"The product did not start as a child within {bound}s: {' '.join(command)}\n"
            f"Nothing in this suite can be measured through a child that never answers, and a "
            f"test that opened a pipe to it would have waited for as long as anyone let it. "
            f"Its import path was pinned to {environment['PYTHONPATH']}.") from None
    if started.returncode != 0:
        raise ChildProvenanceError(
            f"The product could not start as a child: {' '.join(command)} exited "
            f"{started.returncode}.\nIts import path was pinned to {environment['PYTHONPATH']}.\n"
            f"The child said:\n{(started.stderr or started.stdout).strip()[-2000:]}")


def _environment(source) -> dict[str, str]:
    """The environment dict itself, with no preflight — what preflight uses to run."""
    if source is None:
        entries = [parent_source_root()]
    elif isinstance(source, (str, os.PathLike)):
        entries = [Path(part) for part in str(source).split(os.pathsep) if part]
    else:
        entries = [Path(part) for part in source]
    entries = [entry.resolve() for entry in entries]
    if not any((entry / "bookflow" / "__init__.py").is_file() for entry in entries):
        raise ValueError(
            f"No bookflow package under any of {[str(entry) for entry in entries]}; "
            f"a child pinned there could not import the product.")
    environment = {name: os.environ[name] for name in PASSTHROUGH if name in os.environ}
    # The pinned source leads, so the child imports the artifact the test named
    # even where the interpreter also has an installation of its own.
    environment["PYTHONPATH"] = os.pathsep.join(str(entry) for entry in entries)
    return environment


def child_env(source=None, /, **named: str) -> dict[str, str]:
    """The environment for a child process that runs Bookflow.

    The passthrough list, then a pinned import path, then exactly what the
    caller named. `source` pins a copy of the product other than the parent's
    own — a historical checkout extracted with ``git archive``, a built wheel's
    tree — and is a directory containing a ``bookflow`` package, a
    ``os.pathsep``-joined string of such entries, or a sequence of them. At
    least one entry must actually hold the package, so a pin at a path that was
    never populated is caught here rather than three seconds later as an import
    error in a child's stderr.

    Callers must not pass ``PYTHONPATH`` themselves; that is what `source` is
    for, and two spellings of one rule is the defect this module exists to end.
    """
    if "PYTHONPATH" in named:
        raise TypeError(
            "child_env() sets the child's import path itself. Pass the source directory as its "
            "first argument to pin a copy other than the parent's; do not pass PYTHONPATH.")
    environment = _environment(source)
    # The first child environment of the session is also the moment to find out,
    # cheaply and with a bound, that a child can start at all.
    preflight()
    environment.update({name: str(value) for name, value in named.items() if value is not None})
    return environment


def resolve_child_artifact(command: list[str] | None = None, *, env: dict[str, str] | None = None) -> str:
    """Ask a child where it imports `bookflow` from, the way the real children do.

    Used by the provenance tests to prove that a child and this parent load the
    same files, rather than asserting it about the environment dict alone.
    """
    environment = env if env is not None else child_env()
    probe = "import bookflow, json; print(json.dumps(bookflow.__file__))"
    completed = subprocess.run(
        (command or [sys.executable]) + ["-c", probe],
        capture_output=True, text=True, env=environment, cwd=str(Path(sys.executable).parent))
    if completed.returncode != 0:
        raise AssertionError(f"Child could not report its bookflow: {completed.stderr.strip()}")
    return str(Path(json.loads(completed.stdout.strip().splitlines()[-1])).resolve())
