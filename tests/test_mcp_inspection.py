import hashlib
import io
import json

import pytest

from bookflow.adapters.mcp.files import Directories
from bookflow.adapters.mcp.inspection import Mappings, identity, inspect_file, select
from bookflow.core.errors import BookflowError


def test_event_paths_preserve_dots_slashes_tildes_indices_and_large_integers():
    value = {'a.b': {'a/b': {'~': [2**100, {'nested': [True, None, 'é']}, 3]}}, 'a': {'b': 'wrong'}}
    raw = json.dumps(value).encode()
    assert select(io.BytesIO(raw), '/a.b/a~1b/~0/0', 0, 1) == {'kind': 'scalar', 'value': 2**100}
    page = select(io.BytesIO(raw), '/a.b/a~1b/~0', 1, 1)
    assert page == {'kind': 'array', 'items': [{'nested': [True, None, 'é']}], 'total': 3}
    assert select(io.BytesIO(raw), '/a', 0, 1) == {'kind': 'object', 'items': [{'key': 'b', 'value': 'wrong'}], 'total': 1}
    with pytest.raises(BookflowError):
        select(io.BytesIO(raw), '/a.b/a~1b/~0/01', 0, 1)


def test_mapping_expiry_count_and_files_are_not_deleted():
    class Clock:
        now = 0
        def __call__(self):
            return self.now
    clock = Clock()
    mappings = Mappings(clock=clock)
    for index in range(40):
        mappings.add(str(index), '/out/result', 'digest', (1, 2))
    assert len(mappings.entries) == 32
    with pytest.raises(BookflowError):
        mappings.get('0')
    for second in [50, 100, 150, 200, 250, 299]:
        clock.now = second
        mappings.get('39')
    clock.now = 300
    with pytest.raises(BookflowError):
        mappings.get('39')


def test_verified_file_cursor_continuation_and_tamper_rejection(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / 'result.json'
    path.write_bytes(b'{"items":[{"id":1},{"id":2},{"id":3}]}')
    caps = Directories([str(tmp_path)])
    try:
        with caps.input(str(path)) as stream:
            file_identity = identity(stream)
        mapping = {'path': str(path), 'identity': file_identity, 'digest': hashlib.sha256(path.read_bytes()).hexdigest()}
        first = inspect_file(caps, mapping, 'ref', '/items', 2, None)
        assert first['items'] == [{'id': 1}, {'id': 2}] and first['total'] == 3
        second = inspect_file(caps, mapping, 'ref', '/items', 2, first['next_cursor'])
        assert second['items'] == [{'id': 3}] and second['next_cursor'] is None
        with pytest.raises(BookflowError) as mismatch:
            inspect_file(caps, mapping, 'ref', '', 2, first['next_cursor'])
        assert mismatch.value.code == 'E_QUERY_STALE'
        path.write_bytes(b'{"items":[{"id":9},{"id":2},{"id":3}]}')
        with pytest.raises(BookflowError) as tampered:
            inspect_file(caps, mapping, 'ref', '/items', 2, None)
        assert tampered.value.code == 'E_QUERY_STALE'
    finally:
        caps.close()
