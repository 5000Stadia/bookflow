"""The preservation oracle detects differences hidden by SQLite quote()."""
import sqlite3
from tests.payment_raw_evidence import books


def test_streaming_oracle_covers_rowid_nul_storage_ddl_and_attachment(tmp_path):
    path=tmp_path/'company.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE evidence(value)')
        db.execute('INSERT INTO evidence(rowid,value) VALUES (7,?)',('same\x00old',))
    directory=tmp_path/'attachments';directory.mkdir();(directory/'blob').write_bytes(b'\x00\xffold')
    initial=books(tmp_path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT quote(value) FROM evidence').fetchone()[0]=="'same'"
        db.execute('UPDATE evidence SET value=?',('same\x00new',))
        assert db.execute('SELECT quote(value) FROM evidence').fetchone()[0]=="'same'"
    changed=books(tmp_path);assert changed!=initial
    with sqlite3.connect(path) as db:db.execute('UPDATE evidence SET rowid=8')
    identity=books(tmp_path);assert identity!=changed
    with sqlite3.connect(path) as db:db.execute('UPDATE evidence SET value=CAST(value AS BLOB)')
    storage=books(tmp_path);assert storage!=identity
    with sqlite3.connect(path) as db:db.execute('CREATE INDEX witness ON evidence(value)')
    ddl=books(tmp_path);assert ddl!=storage
    (directory/'blob').write_bytes(b'\x00\xffnew');assert books(tmp_path)!=ddl
