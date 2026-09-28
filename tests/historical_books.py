"""Company files written by an earlier release of Bookflow, for first-writer upgrade witnesses.

A witness about "an existing company at revision N meets today's writer" needs a company that
was really written at N. Writing it with today's code under a patched head works only until
today's code reads a column that did not exist at N -- and then every such witness breaks at
once. So the file is written the way a customer's was: by the source and tests of a commit at
which N was the head, run in a child process, and handed back to today's code as a data root.
"""
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import bookflow
from tests import provenance

REPOSITORY = Path(__file__).resolve().parents[1]

#: A commit at which each revision was the company head (the parent of the commit that
#: moved the head past it). Its `tests.test_bill_item_lines.books` builds the company.
PINS = {
    'co0044': '6ca91eeb0c2d2debf550fa45b16d2b34e280ccc8',
    'co0045': '76fa65b2a0a42ea2f2c194ab2f63b707a947d0bc',
    'co0047': '2ef538fba800e21892fe27fb232ee483e4494dd5',
    'co0048': '191280472564c44ebe27c62b632027de381b4db6',
    'co0049': 'dc3a773bf139d9a683a08273ab1fe890c082e579',
    'co0052': '1cb443ce669269cb10c3a6f9c4e5bf4f576e3019',
    'co0053': '94c4898172eaa240ae610180fcadd4c26e0216d6',
}

_PRELUDE = '''
import json, sys
from pathlib import Path
import pytest
from {module} import books{imports}
_out, _root = Path(sys.argv[1]), Path(sys.argv[2])
_patch = pytest.MonkeyPatch()
b = books.__wrapped__(_root, _patch)
result = {{}}
'''

_EPILOGUE = '''
result['books'] = {k: v for k, v in b.items() if k not in ('client', 'run')}
_out.write_text(json.dumps(result))
'''


def _source(folder: Path, pin: str) -> Path:
    source = folder / ('source-' + pin[:12])
    if not source.exists():
        source.mkdir(parents=True)
        archive = subprocess.check_output(['git', 'archive', pin, 'src', 'tests'], cwd=REPOSITORY)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(source, filter='data')
    return source


def books_at(folder: Path, revision: str, script: str = '', *, module: str = 'tests.test_bill_item_lines',
             imports: str = '_inventory_part', data: str = 'items', company: str | None = None) -> dict:
    """`books` as the release at `revision` built it, then `script` run in that release.

    `script` sees `b` (that release's books dict, with `run` and `client`) and fills `result`
    with anything JSON the caller needs back. Returns today's books dict over the same data
    root -- today's client and `run` -- with `result` under 'historical'. `module` names the
    test module whose `books` fixture builds the company (its data root is `folder/data`).
    """
    folder = Path(folder)
    source = _source(folder, PINS[revision])
    out = folder / 'historical.json'
    code = _PRELUDE.format(module=module, imports=', ' + imports if imports else '') + script + _EPILOGUE
    env = provenance.child_env(os.pathsep.join((str(source / 'src'), str(source))),
                               PYTHONDONTWRITEBYTECODE='1', BOOKFLOW_DATA_ROOT=str(folder / data))
    completed = subprocess.run([sys.executable, '-c', code, str(out), str(folder)], cwd=source,
                               env=env, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(out.read_text())
    client = bookflow.connect(data_root=str(folder / data))
    company = company or result['books']['company']

    def run(name, raw, **context):
        return client.run(name, raw, company=company, **context)

    return dict(result.pop('books'), client=client, run=run, historical=result)
