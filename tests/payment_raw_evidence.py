"""Pure streaming evidence for row identity, storage class, exact values and DDL."""
import hashlib
from pathlib import Path
import sqlite3
import struct


def quote(name):return '"'+name.replace('"','""')+'"'


def upgrade_to(db, revision, chain='company'):
    """Run an open populated database forward to exactly `revision`.

    `migrate_to_head` runs the whole chain, which is right for an upgrade witness and wrong for
    a claim about one transition: every later migration's legitimate rewrite then reads as this
    migration failing to preserve something, and because such assertions usually come first, the
    real checks behind them stop running too.

    The preserving runner turns foreign keys off for the duration -- a rebuild recreates tables
    whose references do not exist yet -- and some migrations refuse to run without that, by name.
    Setting the same precondition here reproduces the runner's contract rather than evading it.
    """
    from alembic import command

    from bookflow.storage.migrate import _config
    db.raw.execute('PRAGMA foreign_keys=OFF')
    try:
        command.upgrade(_config(chain, db.conn), revision)
        db.raw.commit()
    finally:
        db.raw.execute('PRAGMA foreign_keys=ON')
    return db.raw.execute('SELECT version_num FROM alembic_version').fetchone()[0]


def table(raw,name,*,through_rowid=None,omit_columns=()):
    columns=[r[1] for r in raw.execute('PRAGMA table_xinfo('+quote(name)+')') if r[1] not in omit_columns]
    expressions=['rowid']
    for column in columns:
        key=quote(column);expressions.extend(('typeof('+key+')',key,'CAST('+key+' AS BLOB)'))
    sql='SELECT '+','.join(expressions)+' FROM '+quote(name)
    args=()
    if through_rowid is not None:sql+=' WHERE rowid<=?';args=(through_rowid,)
    digest=hashlib.sha256();count=0;maximum=0
    for row in raw.execute(sql+' ORDER BY rowid',args):
        # Include exact floating representation as well as raw bytes; quote()
        # alone truncates embedded-NUL text and is not a raw-value witness.
        exact=tuple(('float64',struct.pack('>d',value)) if isinstance(value,float) else value for value in row)
        digest.update(repr(exact).encode('utf-8'));digest.update(b'\n');count+=1;maximum=max(maximum,row[0])
    return dict(columns=columns,count=count,max_rowid=maximum,sha256=digest.hexdigest())


def preserved(raw,name,before):
    """The stored values of `name` as an earlier reading saw them, ignoring later columns.

    An upgrade test asserts that not one stored value moved. A migration that widens a table
    adds a column, and comparing the widened reading against the old one fails on the column
    list alone -- which is how a pinned "what may change" set falsifies itself on every new
    migration. Omitting exactly the columns that did not exist then keeps the assertion about
    the values, which is what it was ever about.
    """
    columns=[r[1] for r in raw.execute('PRAGMA table_xinfo('+quote(name)+')')]
    return table(raw,name,omit_columns=[c for c in columns if c not in before['columns']])


def database(path):
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as raw:
        raw.execute('PRAGMA query_only=ON')
        ddl=raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
        return dict(ddl=ddl,tables={name:table(raw,name) for kind,name,_,_ in ddl if kind=='table'})


def attachments(root):
    result={}
    for path in sorted(Path(root).rglob('*')):
        if path.is_file() and 'attachments' in path.parts:
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                while block:=stream.read(1024*1024):digest.update(block)
            result[str(path.relative_to(root))]=dict(bytes=path.stat().st_size,sha256=digest.hexdigest())
    return result


def books(root):
    return dict(databases={str(path.relative_to(root)):database(path) for path in sorted(Path(root).rglob('*.db'))},attachments=attachments(root))
