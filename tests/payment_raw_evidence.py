"""Pure streaming evidence for row identity, storage class, exact values and DDL."""
import hashlib
from pathlib import Path
import sqlite3
import struct


def quote(name):return '"'+name.replace('"','""')+'"'


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
