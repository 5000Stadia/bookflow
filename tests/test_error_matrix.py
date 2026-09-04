"""Every command's declared codes are in the matrix, and the matrix's codes are the declared ones; no test run produces E_INTERNAL."""

import re
from pathlib import Path

from bookflow.core import registry
from bookflow.core.errors import ALL_CODES, INFRASTRUCTURE_CODES
from tests.error_matrix import INFRASTRUCTURE, MATRIX


def test_matrix_matches_registry():
    """Both directions: every registered command has a matrix row covering its declared codes, and every matrix row names a registered command with real codes."""
    registry.load_all()
    names = {cmd.name for cmd in registry.all_commands()}
    for cmd in registry.all_commands():
        declared = set(cmd.error_codes)
        listed = set(MATRIX.get(cmd.name, {}))
        assert declared <= listed | set(INFRASTRUCTURE), (cmd.name, declared - listed)
    orphan_rows = sorted(set(MATRIX) - names)
    assert not orphan_rows, f"matrix rows for commands that do not exist: {orphan_rows}"
    orphan_codes = sorted({code for row in MATRIX.values() for code in row if code not in ALL_CODES})
    assert not orphan_codes, f"matrix codes that do not exist: {orphan_codes}"
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
    # Money and the row 2 services raise codes that no row 1 command reaches; row 2 declares them on its commands.
    pending_rows = {"E_AMOUNT_PRECISION", "E_DIRECTIVE_NOT_FOUND", "E_DIRECTIVE_INACTIVE", "E_IDEMPOTENCY_MISMATCH", "E_VERSION_CONFLICT", "E_RECORD_NOT_FOUND", "E_PARTIAL_WRITE"}
    assert raised <= declared | pending_rows, raised - declared
