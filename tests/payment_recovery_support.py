"""Configured real launcher and explicit reviewed-source provenance for recovery gates."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import bookflow
from tests import conftest
from tests import provenance as provenance_owner


def launcher(monkeypatch=None):
    binary=Path(provenance_owner.launcher(required=True)).resolve()
    if monkeypatch is not None:
        monkeypatch.setenv('BOOKFLOW_MCP_TEST_BINARY',str(binary))
        monkeypatch.setattr(conftest,'BIN',binary)
    return binary


def source_environment():
    """The pinned import path, recorded in the receipt; tests/provenance.py owns it."""
    return {'PYTHONPATH':provenance_owner.child_env()['PYTHONPATH']}


def provenance(binary):
    repository=Path(__file__).resolve().parents[1]
    package=Path(bookflow.__file__).resolve().parent
    assert package==repository/'src/bookflow',('Recovery host must load the reviewed source',str(package))
    files={str(path.relative_to(repository)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(package.rglob('*.py'))}
    return dict(commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repository,text=True).strip(),
        package=str(package),python=sys.executable,launcher=str(binary),launcher_resolved=str(binary.resolve()),
        launcher_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),source_environment=source_environment(),source_files=files)
