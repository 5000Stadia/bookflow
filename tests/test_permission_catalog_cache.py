"""R180 lane D: the sealed on-disk copy of the verified permission catalog chain.

A hit must give exactly what full verification gives; anything that could make the copy
stale or damaged must fall back to full verification and rewrite it; never fail open.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bookflow.hub import permission_catalog as c, permission_catalog_cache as cache
from bookflow.hub import permission_runtime as runtime

SRC = str(Path(cache.__file__).resolve().parents[2])


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.delenv(cache.ENV, raising=False)
    monkeypatch.setattr(cache, '_root', None)
    cache.configure(tmp_path)
    monkeypatch.setattr(runtime, '_known', None)
    return tmp_path


def _files(root):
    return sorted((root / 'cache').glob('permission-catalog-*.bin'))


def _fresh_process_state(monkeypatch):
    monkeypatch.setattr(runtime, '_known', None)


def _modules():
    return {v: runtime._full_known()[v] for v in runtime.ACCEPTED_DESCRIPTOR_SHA256}


def test_hit_is_identical_to_full_verification(root, monkeypatch):
    assert type(runtime._chain()) is dict  # nothing on disk: the full check, which writes the copy
    [written] = _files(root)
    assert written.name == 'permission-catalog-%s.bin' % runtime._cache_key()[:40]
    _fresh_process_state(monkeypatch)
    chain = runtime._chain()
    assert isinstance(chain, runtime._CachedChain)
    for version, module in _modules().items():
        layer = runtime.known_catalog(version)
        assert isinstance(layer, runtime._CachedLayer)
        assert layer.CATALOG == module.CATALOG
        assert layer.MANIFEST == module.MANIFEST
        assert layer.SOURCE_COMMIT == module.SOURCE_COMMIT
        assert layer.catalog_bundle() == module.catalog_bundle()
        assert layer.MANIFEST.descriptor_sha256 == runtime.ACCEPTED_DESCRIPTOR_SHA256[version]
    assert runtime.current_catalog() is runtime.known_catalog(c.POS_REFUND_POLICY_VERSION)
    assert runtime.known_catalog('unrecognized-future') is None


def test_layer_source_change_forces_full_and_rewrites(root, monkeypatch, tmp_path_factory):
    runtime._chain()
    [before] = _files(root)
    copy = tmp_path_factory.mktemp('src') / 'permission_performance_catalog.py'
    original = cache.key_sources()
    layer = next(p for p in original if p.name == copy.name)
    copy.write_bytes(layer.read_bytes() + b'\n# one more byte of source\n')
    monkeypatch.setattr(cache, 'key_sources', lambda: [copy if p == layer else p for p in original])
    _fresh_process_state(monkeypatch)
    assert type(runtime._chain()) is dict
    [after] = _files(root)
    assert after != before and not before.exists()


def test_key_change_leaves_exactly_one_copy_and_nothing_else_touched(root, monkeypatch):
    runtime._chain()
    folder = root / 'cache'
    keep = [folder / 'notes.txt', folder / 'permission-catalog-x.tmp', root / 'permission-catalog-old.bin']
    for path in keep:
        path.write_text('not ours')
    for n in range(3):  # three code versions in a row
        monkeypatch.setattr(runtime, '_cache_key', lambda n=n: '%064x' % (n + 1))
        _fresh_process_state(monkeypatch)
        assert type(runtime._chain()) is dict
        [only] = _files(root)
        assert only.name == 'permission-catalog-%s.bin' % ('%064x' % (n + 1))[:40]
    assert all(path.read_text() == 'not ours' for path in keep)
    assert only.stat().st_size < 4 * 1024 * 1024  # compressed: about 1 MB, not 19 MB
    _fresh_process_state(monkeypatch)
    assert isinstance(runtime._chain(), runtime._CachedChain)
    assert runtime.current_catalog().MANIFEST == runtime._full_known()[c.POS_REFUND_POLICY_VERSION].MANIFEST


def test_every_key_input_moves_the_key(monkeypatch):
    pins, ancestor = dict(runtime.ACCEPTED_DESCRIPTOR_SHA256), runtime.ACCEPTED_ANCESTOR_SHA256
    key = cache.compute_key(pins, ancestor)
    assert cache.compute_key(pins, ancestor) == key
    moved = dict(pins, **{c.SCHEDULING_POLICY_VERSION: '0' * 64})
    assert cache.compute_key(moved, ancestor) != key
    assert cache.compute_key(pins, '0' * 64) != key
    monkeypatch.setattr(cache, '_bookflow_version', lambda: '9.9.9')
    assert cache.compute_key(pins, ancestor) != key
    monkeypatch.undo()
    monkeypatch.setattr(cache.sys, 'version', sys.version + ' other')
    assert cache.compute_key(pins, ancestor) != key
    names = {p.name for p in cache.key_sources()}
    assert {'permission_catalog.py', 'permission_runtime.py', 'permission_catalog_cache.py',
            'permission_performance_catalog.py', 'permission_snapshot.py', 'deletion_families.py'} <= names
    hub = Path(runtime.__file__).parent
    assert {p.name for p in hub.glob('permission_*_catalog.py')} <= names


def test_pin_change_forces_full(root, monkeypatch):
    runtime._chain()
    [before] = _files(root)
    # A re-pinned version moves the key, so the old copy is never read for it.
    monkeypatch.setattr(runtime, '_cache_key', lambda: cache.compute_key(
        dict(runtime.ACCEPTED_DESCRIPTOR_SHA256, **{c.SCHEDULING_POLICY_VERSION: 'f' * 64}),
        runtime.ACCEPTED_ANCESTOR_SHA256))
    _fresh_process_state(monkeypatch)
    assert type(runtime._chain()) is dict
    assert not before.exists() and len(_files(root)) == 1


def test_cached_version_must_reproduce_its_pin(root, monkeypatch):
    runtime._chain()
    _fresh_process_state(monkeypatch)
    assert isinstance(runtime._chain(), runtime._CachedChain)
    # Even under a matching key and seal, a decoded version is checked against its pin.
    monkeypatch.setitem(runtime.ACCEPTED_DESCRIPTOR_SHA256, c.PERFORMANCE_POLICY_VERSION, '0' * 64)
    with pytest.raises(ValueError):
        runtime._known._decode(c.PERFORMANCE_POLICY_VERSION)


def test_decode_failure_falls_back_to_full(root, monkeypatch):
    runtime._chain()
    _fresh_process_state(monkeypatch)
    chain = runtime._chain()
    monkeypatch.setattr(chain, '_decode', lambda version: (_ for _ in ()).throw(ValueError('broken')))
    got = runtime.known_catalog(c.PERFORMANCE_POLICY_VERSION)
    assert got is _modules()[c.PERFORMANCE_POLICY_VERSION]
    assert type(runtime._known) is dict


@pytest.mark.parametrize('damage', ['flip_body', 'flip_index', 'truncate', 'empty', 'garbage', 'seal_only'])
def test_damaged_file_falls_back_and_is_rewritten(root, monkeypatch, damage):
    runtime._chain()
    [path] = _files(root)
    data = path.read_bytes()
    if damage == 'flip_body':
        i = len(data) - 1000
        data = data[:i] + bytes([data[i] ^ 1]) + data[i + 1:]
    elif damage == 'flip_index':
        i = data.index(b'"versions"') + 2
        data = data[:i] + bytes([data[i] ^ 1]) + data[i + 1:]
    elif damage == 'truncate':
        data = data[:len(data) // 2]
    elif damage == 'empty':
        data = b''
    elif damage == 'garbage':
        data = os.urandom(4096)
    elif damage == 'seal_only':
        data = data[:65]
    path.write_bytes(data)
    _fresh_process_state(monkeypatch)
    assert type(runtime._chain()) is dict
    [again] = _files(root)
    assert cache.decode(again.read_bytes(), runtime._cache_key()).index


def test_env_forces_full_verification(root, monkeypatch):
    runtime._chain()
    _fresh_process_state(monkeypatch)
    monkeypatch.setenv(cache.ENV, 'full')
    assert type(runtime._chain()) is dict
    assert cache.load(runtime._cache_key()) is None
    monkeypatch.delenv(cache.ENV)
    _fresh_process_state(monkeypatch)
    assert isinstance(runtime._chain(), runtime._CachedChain)


def test_self_check_is_always_full(root, monkeypatch):
    runtime._chain()
    _fresh_process_state(monkeypatch)
    assert isinstance(runtime._chain(), runtime._CachedChain)
    calls = []
    real = runtime._full_known
    monkeypatch.setattr(runtime, '_full_known', lambda: calls.append(1) or real())
    from bookflow.commands import permission_cmds
    with pytest.raises(Exception):
        permission_cmds.state(None)  # the full check runs before the session is touched
    assert calls and type(runtime._known) is dict


def test_unconfigured_or_missing_root_never_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, '_known', None)
    monkeypatch.setattr(cache, '_root', None)
    assert type(runtime._chain()) is dict
    monkeypatch.setattr(runtime, '_known', None)
    cache.configure(tmp_path / 'not-initialised')
    assert type(runtime._chain()) is dict
    assert not (tmp_path / 'not-initialised').exists()


_CHILD = '''
import sys
from bookflow.hub import permission_catalog_cache as cache, permission_runtime as runtime
cache.configure(sys.argv[1])
print(type(runtime._chain()).__name__, runtime.current_catalog().MANIFEST.descriptor_sha256)
'''


def test_concurrent_first_runs_leave_one_valid_file(tmp_path, monkeypatch):
    env = dict(os.environ, PYTHONPATH=SRC)
    env.pop(cache.ENV, None)
    procs = [subprocess.Popen([sys.executable, '-c', _CHILD, str(tmp_path)], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(6)]
    outs = [p.communicate(timeout=600) for p in procs]
    assert all(p.returncode == 0 for p in procs), [o[1][-2000:] for o in outs]
    tip = runtime.ACCEPTED_DESCRIPTOR_SHA256[c.POS_REFUND_POLICY_VERSION]
    assert all(o[0].split()[1] == tip for o in outs)
    files = list((tmp_path / 'cache').iterdir())
    assert len(files) == 1 and files[0].name.startswith('permission-catalog-'), files
    monkeypatch.setattr(cache, '_root', tmp_path)
    assert cache.decode(files[0].read_bytes(), runtime._cache_key()).index
    after = subprocess.run([sys.executable, '-c', _CHILD, str(tmp_path)], env=env,
                           capture_output=True, text=True, timeout=600)
    assert after.stdout.split() == ['_CachedChain', tip], after.stderr[-2000:]
