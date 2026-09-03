import pytest

from bookflow.core.errors import BookflowError
from bookflow.storage.paths import (choose_folder_name, derive_folder_name, name_key,
                                    normalize_display_name, read_company_marker, reserve_folder,
                                    write_company_marker)


@pytest.mark.parametrize("name,expected", [
    ("Acme Plumbing", "Acme Plumbing"),
    ("Acme/Plumbing: East?", "Acme Plumbing East"),
    ("  lots   of   space  ", "lots of space"),
    ("...dots...", "dots"),
    ("", "Company"),
    ("CON", "CON Co"),
    ("con.txt", "con.txt Co"),
    ("Café", "Café"),
])
def test_derive(name, expected):
    assert derive_folder_name(name) == expected


def test_truncation_bytes():
    long = "漢" * 100
    out = derive_folder_name(long)
    assert len(out.encode()) <= 90


def test_collision_case_insensitive(tmp_path):
    (tmp_path / "Acme").mkdir()
    assert choose_folder_name(tmp_path, "ACME") == "ACME (2)"
    assert choose_folder_name(tmp_path, "ACME", exclude="Acme") == "ACME"
    (tmp_path / "ACME (2)").mkdir()
    assert choose_folder_name(tmp_path, "acme") == "acme (3)"


def test_reserve(tmp_path):
    a = reserve_folder(tmp_path, "Acme")
    b = reserve_folder(tmp_path, "acme")
    assert a.name == "Acme" and b.name == "acme (2)"
    assert (a.stat().st_mode & 0o777) == 0o700


def test_names():
    assert normalize_display_name("  Acme   Co ") == "Acme Co"
    assert name_key("ACME  co") == "acme co"
    with pytest.raises(BookflowError):
        normalize_display_name("A/B")
    with pytest.raises(BookflowError):
        normalize_display_name("   ")


def test_marker_roundtrip(tmp_path):
    write_company_marker(tmp_path, company_id="01X", state="ready", display_name='Say "hi"', schema_revision="abc")
    m = read_company_marker(tmp_path)
    assert m == {"company_id": "01X", "state": "ready", "display_name": 'Say "hi"', "schema_revision": "abc"}
    with pytest.raises(BookflowError) as e:
        read_company_marker(tmp_path / "nope")
    assert e.value.code == "E_ATTACH_INVALID"
