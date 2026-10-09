"""R170: Harbor Electric, a fake company kept in Bookflow and checked against its answer key.

tests/fixtures/fakeco/ is written by tests/fakeco.py (see its README). The first test holds the
committed files to exactly what the generator writes, so the key and the files never drift apart.
"""
from tests import fakeco, fakeco_render


def test_the_fixture_is_what_the_generator_writes():
    files = fakeco_render.render()
    committed = {str(p.relative_to(fakeco.OUT)): p.read_bytes() for p in fakeco.OUT.rglob("*")
                 if p.is_file() and p.name != "README.md"}
    assert sorted(files) == sorted(committed)
    stale = [path for path, data in files.items() if committed[path] != data]
    assert not stale, f"regenerate with `python -m tests.fakeco`: {stale}"
