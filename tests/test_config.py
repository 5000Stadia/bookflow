import pytest

from bookflow.core.config import Config, os_login
from bookflow.core.errors import BookflowError


def test_roundtrip(tmp_path):
    c = Config(tmp_path / "config.toml")
    c.set_user("k", "01U", "01C")
    c.data["client"]["display_name"] = 'Work "box"'
    c.save()
    d = Config.load(tmp_path / "config.toml")
    assert d.user_table("k") == {"user_id": "01U", "default_company": "01C"}
    assert d.data["client"]["display_name"] == 'Work "box"'
    d.clear_default_everywhere({"01C"})
    assert "default_company" not in d.user_table("k")
    assert (tmp_path / "config.toml").stat().st_mode & 0o777 == 0o600


def test_invalid(tmp_path):
    (tmp_path / "config.toml").write_text("not = [toml")
    with pytest.raises(BookflowError) as e:
        Config.load(tmp_path / "config.toml")
    assert e.value.code == "E_CONFIG_INVALID"


def test_os_login():
    assert os_login()
