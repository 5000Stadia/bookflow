import pytest

from bookflow.core.errors import BookflowError
from bookflow.core.moves import move_dir


def test_move_and_noreplace(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f").write_text("x")
    move_dir(tmp_path / "a", tmp_path / "b")
    assert (tmp_path / "b" / "f").read_text() == "x"
    (tmp_path / "c").mkdir()
    with pytest.raises(BookflowError) as e:
        move_dir(tmp_path / "b", tmp_path / "c")
    assert e.value.code == "E_IO" and e.value.details["errno"] == "EEXIST"
    assert (tmp_path / "b" / "f").exists()


def test_case_only(tmp_path):
    (tmp_path / "acme").mkdir()
    move_dir(tmp_path / "acme", tmp_path / "ACME", company_id="01X")
    assert (tmp_path / "ACME").exists()
