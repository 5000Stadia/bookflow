import pytest

from bookflow.core.errors import ALL_CODES, BookflowError


def test_codes_and_exit():
    e = BookflowError("E_USAGE")
    assert e.exit_code == 2 and e.to_dict()["code"] == "E_USAGE"
    assert BookflowError("E_INTERNAL").exit_code == 3
    assert BookflowError("E_VALIDATION", details={"fields": []}).exit_code == 1
    with pytest.raises(ValueError):
        BookflowError("E_NOPE")


def test_all_codes_prefixed():
    assert all(c.startswith("E_") for c in ALL_CODES)
