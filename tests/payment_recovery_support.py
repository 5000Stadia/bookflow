"""Configured real launcher and explicit reviewed-source provenance for recovery gates."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import bookflow
from tests import conftest


def launcher(monkeypatch=None):
    binary=Path(os.environ.get('BOOKFLOW_MCP_TEST_BINARY',str(conftest.BIN))).resolve()
    if not binary.is_file():
        raise AssertionError('Install the reviewed package entry point beside the test interpreter, or set BOOKFLOW_MCP_TEST_BINARY to its launcher: '+str(binary))
    if monkeypatch is not None:
        monkeypatch.setenv('BOOKFLOW_MCP_TEST_BINARY',str(binary))
        monkeypatch.setenv('PYTHONPATH',source_environment()['PYTHONPATH'])
        monkeypatch.setattr(conftest,'BIN',binary)
    return binary


def source_environment():
    return {'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')}


def provenance(binary):
    repository=Path(__file__).resolve().parents[1]
    package=Path(bookflow.__file__).resolve().parent
    assert package==repository/'src/bookflow',('Recovery host must load the reviewed source',str(package))
    files={str(path.relative_to(repository)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(package.rglob('*.py'))}
    return dict(commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repository,text=True).strip(),
        package=str(package),python=sys.executable,launcher=str(binary),launcher_resolved=str(binary.resolve()),
        launcher_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),source_environment=source_environment(),source_files=files)
