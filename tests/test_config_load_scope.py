"""R180: Config.load reads each source once per command scope, and never serves stale settings."""
import pytest

from bookflow.core import config as cfg
from bookflow.core.config import Config, load_scope
from bookflow.core.errors import BookflowError


@pytest.fixture
def counted(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('[users."ann"]\nuser_id = "U1"\n', encoding="utf-8")
    reads = []
    real = cfg._pending_from_disk
    monkeypatch.setattr(cfg, "_pending_from_disk", lambda hub: reads.append(hub) or real(hub))
    return path, reads


def test_outside_a_scope_every_load_reads_disk(counted):
    path, reads = counted
    Config.load(path)
    Config.load(path)
    assert len(reads) == 2


def test_inside_a_scope_the_first_read_is_kept_and_each_load_gets_its_own_copy(counted):
    path, reads = counted
    with load_scope():
        first = Config.load(path)
        first.data["users"]["ann"]["user_id"] = "changed in memory"
        second = Config.load(path)
        with load_scope():  # a nested scope shares the outer one
            third = Config.load(path)
    assert len(reads) == 1
    assert second.data == third.data == {"client": {}, "host": {}, "users": {"ann": {"user_id": "U1"}}, "backups": {}}
    assert second.data is not third.data and second.path == path
    Config.load(path)
    assert len(reads) == 2  # the scope ended


def test_a_settings_write_in_the_command_turns_the_cache_off(counted):
    path, reads = counted
    with load_scope():
        Config.load(path)
        changed = Config.load(path)
        changed.set_user("bob", "U2")
        changed.save()
        assert Config.load(path).user_table("bob") == {"user_id": "U2"}
        Config.load(path)
    assert len(reads) == 3


def test_stage_and_flush_turn_the_cache_off(counted):
    path, _ = counted
    for hook in ("stage_pending", "flush_pending"):
        with load_scope():
            Config.load(path)
            assert cfg._SCOPE.get()
            cfg._settings_changed()  # what stage_pending and flush_pending call first
            path.write_text('[users."ann"]\nuser_id = "U9"\n', encoding="utf-8")
            assert Config.load(path).user_table("ann") == {"user_id": "U9"}, hook
        path.write_text('[users."ann"]\nuser_id = "U1"\n', encoding="utf-8")
    import inspect
    for method in (Config.stage_pending, Config.flush_pending, Config.save):
        assert "_settings_changed()" in inspect.getsource(method)


def test_an_unreadable_file_is_never_cached(counted):
    path, reads = counted
    path.write_text("not toml [", encoding="utf-8")
    with load_scope():
        for _ in range(2):
            with pytest.raises(BookflowError) as e:
                Config.load(path)
            assert e.value.code == "E_CONFIG_INVALID"
    assert len(reads) == 2
