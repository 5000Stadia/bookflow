"""The documentation command is registered but remains rootless and lazily loaded."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from bookflow.core import registry
from bookflow.core.context import Context, Interface
from bookflow.core.errors import BookflowError
from tests.error_matrix import STANDALONE_MATRIX


def _command():
    registry.load_all("docs")
    cmd = registry.get("docs generate")
    assert cmd is not None
    return cmd


def test_docs_generate_is_standalone_local_and_nonpermissioned():
    cmd = _command()

    assert cmd.bootstrap and cmd.local_only and cmd.standalone
    assert not cmd.permissioned
    assert cmd.capability is None and cmd.required_role is None and cmd.feature is None
    assert not cmd.writes and not cmd.is_write
    assert cmd.name not in {item.name for item in registry.all_commands()}
    assert cmd.name in {item.name for item in registry.all_commands(include_standalone=True)}
    assert cmd.name not in {item.name for item in registry.routed_commands()}
    assert set(cmd.error_codes) == set(STANDALONE_MATRIX[cmd.name])
    assert cmd.input_model().model_dump() == {"output": "docs", "check": False}
    assert list(cmd.output_model.model_fields) == ["output", "mode", "file_count", "files"]


def test_standalone_runner_uses_no_data_root_lock_or_actor(monkeypatch, tmp_path):
    from bookflow.commands import docs_cmds
    from bookflow.core import dispatch, forward

    calls = []
    generated = SimpleNamespace(generate_docs=lambda output, check: calls.append((output, check)) or ["z.md", "a.md", "a.md"])
    monkeypatch.setattr(docs_cmds.importlib, "import_module", lambda name: generated if name == "bookflow.documentation.generate" else None)
    monkeypatch.setattr(dispatch, "resolve_data_root", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("resolved data root")))
    monkeypatch.setattr(dispatch, "RootLock", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("took lock")))
    monkeypatch.setattr(dispatch, "os_login", lambda: (_ for _ in ()).throw(AssertionError("loaded actor")))
    monkeypatch.setattr(forward, "try_forward", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("forwarded")))

    output = dispatch.run(
        _command(),
        {"output": str(tmp_path / "reference"), "check": True},
        Context.new(Interface.cli, "bookflow-cli"),
        data_root=str(tmp_path / "must-not-be-resolved"),
    )

    assert calls == [(str(tmp_path / "reference"), True)]
    assert output == {
        "output": str(tmp_path / "reference"),
        "mode": "check",
        "file_count": 2,
        "files": ["a.md", "z.md"],
    }


def test_standalone_runner_preserves_named_stale_error(monkeypatch):
    from bookflow.commands import docs_cmds
    from bookflow.core import dispatch

    def stale(_output, _check):
        raise BookflowError("E_DOCS_STALE", details={"missing": ["index.md"], "extra": [], "changed": []})

    monkeypatch.setattr(docs_cmds.importlib, "import_module", lambda _name: SimpleNamespace(generate_docs=stale))
    with pytest.raises(BookflowError) as caught:
        dispatch.run(_command(), {"check": True}, Context.new(Interface.cli, "bookflow-cli"))
    assert caught.value.code == "E_DOCS_STALE"
    assert caught.value.details == {"missing": ["index.md"], "extra": [], "changed": []}


def test_docs_help_is_exact_and_does_not_import_the_renderer(cli):
    root_help = cli.run("--help").stdout
    help_text = cli.run("docs", "generate", "--help").stdout

    assert "docs" in root_help
    assert "--output" in help_text and "--check" in help_text and "--json" in help_text
    for irrelevant in ("--data-root", "--dry-run", "--company", "--reason", "--source-ref",
                       "--directive", "--idempotency-key", "--interactive"):
        assert irrelevant not in help_text

    script = """
from bookflow.adapters.cli.app import build_app
build_app('docs')
import sys
assert 'bookflow.documentation.generate' not in sys.modules
assert 'sqlalchemy' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_docs_generate_cli_needs_no_initialized_data_root(tmp_path):
    output = tmp_path / "reference"
    missing_root = tmp_path / "never-created-data-root"
    result = subprocess.run(
        [sys.executable, "-m", "bookflow.adapters.cli.app", "--json", "docs", "generate", "--output", str(output)],
        capture_output=True,
        text=True,
        env={**os.environ, "BOOKFLOW_DATA_ROOT": str(missing_root)},
    )

    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    assert document["output"] == str(output)
    assert document["mode"] == "generate"
    assert document["file_count"] == len(document["files"])
    assert document["files"] == sorted(document["files"])
    assert ".bookflow-generated" in document["files"]
    assert output.is_dir()
    assert not missing_root.exists()


@pytest.mark.parametrize(
    "arguments",
    [
        ("--data-root", "/unused", "docs", "generate"),
        ("--dry-run", "docs", "generate"),
        ("--reason", "unused", "docs", "generate"),
        ("--source-ref", "unused", "docs", "generate"),
        ("--directive", "unused", "docs", "generate"),
        ("--idempotency-key", "unused", "docs", "generate"),
        ("--company", "unused", "docs", "generate"),
        ("docs", "generate", "--data-root", "/unused"),
    ],
)
def test_docs_generate_rejects_database_and_context_flags(cli, arguments):
    error, status = cli.error(*arguments)
    assert status == 2
    assert error["code"] == "E_USAGE"


def test_standalone_registration_is_fail_closed():
    from pydantic import BaseModel

    class Empty(BaseModel):
        pass

    runner = lambda *_args: {}  # noqa: E731
    with pytest.raises(ValueError, match="bootstrap and local-only"):
        registry.command(
            "invalid standalone",
            scope="hub",
            description="Invalid.",
            input_model=Empty,
            output_model=Empty,
            standalone_runner=runner,
        )
    with pytest.raises(ValueError, match="cannot declare database writes, a role, a capability, or a feature"):
        registry.command(
            "invalid standalone",
            scope="hub",
            description="Invalid.",
            input_model=Empty,
            output_model=Empty,
            bootstrap=True,
            local_only=True,
            capability="docs",
            standalone_runner=runner,
        )
