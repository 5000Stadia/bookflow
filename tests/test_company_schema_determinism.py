"""A fresh company database gets the same schema text in every process.

co0004 rebuilds ``customers`` through Alembic's batch mode, which wrote the table's constraints
in the iteration order of a set keyed by object identity. Two fresh databases made by two
processes then carried differently ordered CREATE TABLE text. Each child here migrates a new
company database under a different hash seed and reports a digest of its whole sqlite_schema.
"""
import subprocess
import sys

from tests import provenance

CHILD = r'''
import hashlib, sys, tempfile, pathlib
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import migrate_to_head
with tempfile.TemporaryDirectory(dir=sys.argv[1]) as where:
    with open_database(pathlib.Path(where) / 'fresh.db', writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
        rows = db.raw.execute('SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name').fetchall()
print(hashlib.sha256(repr(rows).encode()).hexdigest())
'''


def test_fresh_company_schema_text_is_identical_across_processes(tmp_path):
    digests = set()
    for seed in ('0', '1', '12345'):
        env = provenance.child_env(PYTHONHASHSEED=seed)
        done = subprocess.run([sys.executable, '-c', CHILD, str(tmp_path)], env=env, capture_output=True,
                              text=True, timeout=provenance.CHILD_SECONDS)
        assert done.returncode == 0, done.stderr
        digests.add(done.stdout.strip())
    assert len(digests) == 1
