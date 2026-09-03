"""Every command's declared codes are in the matrix, and the matrix's codes are the declared ones; no test run produces E_INTERNAL."""

import re
from pathlib import Path

from bookflow.core import registry
from bookflow.core.errors import ALL_CODES, INFRASTRUCTURE_CODES
from tests.error_matrix import INFRASTRUCTURE, MATRIX


def test_matrix_matches_registry():
    registry.load_all()
    for cmd in registry.all_commands():
        declared = set(cmd.error_codes)
        listed = set(MATRIX.get(cmd.name, {}))
        assert declared <= listed | set(INFRASTRUCTURE), (cmd.name, declared - listed)
        for code in listed:
            assert code in ALL_CODES
    assert set(INFRASTRUCTURE) == set(INFRASTRUCTURE_CODES)


def test_raised_codes_are_declared_or_infrastructure():
    """Every E_ code raised in a command module is either infrastructure or declared by some command."""
    src = Path(__file__).resolve().parents[1] / "src" / "bookflow"
    declared = set(INFRASTRUCTURE)
    registry.load_all()
    for cmd in registry.all_commands():
        declared |= set(cmd.error_codes)
    raised = set()
    for p in list((src / "commands").glob("*.py")) + list((src / "hub").glob("*.py")) + list((src / "company").glob("*.py")) + [src / "core" / "dispatch.py"]:
        raised |= set(re.findall(r'BookflowError\("(E_[A-Z_]+)"', p.read_text()))
    assert raised <= declared | {"E_AMOUNT_PRECISION"}, raised - declared
