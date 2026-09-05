"""Company-secret signatures authenticate continuation state and report metadata."""
import base64
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from bookflow import BookflowError
from bookflow.company import schema
from bookflow.company.ledger_reports import _decode_cursor
from bookflow.storage.engine import open_database
from tests.test_row8_reports import ledger, tb, gl  # noqa: F401
from tests.test_migration_chain import _make_revision


def tamper(cursor, change):
    payload, signature = cursor.split('.')
    value = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    change(value)
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=') + '.' + signature


@pytest.mark.parametrize('change', [
    lambda c: c['metadata'].update(generation_time='1900-01-01T00:00:00.000Z'),
    lambda c: c['metadata'].update(audit_watermark=0),
    lambda c: c.update(account_id='b'),
    lambda c: c.update(offset=999),
    lambda c: c.update(permissions='forged'),
    lambda c: c.update(watermark='forged'),
    lambda c: c.update(query='forged'),
    lambda c: c.update(company='another'),
])
def test_signed_cursor_rejects_modified_metadata_and_state(ledger, change):
    s, batch, _ = ledger
    batch('2026-01-10', 100)
    first = gl(s, account='a', limit=1)
    forged = tamper(first.next_cursor, change)
    with pytest.raises(BookflowError) as caught:
        gl(s, account='a', limit=1, cursor=forged)
    assert caught.value.code == 'E_VALIDATION'
    assert not s.company.raw.in_transaction
    second = gl(s, account='a', limit=1, cursor=first.next_cursor)
    assert second.metadata == first.metadata


def test_unsigned_cursor_is_rejected_and_key_material_is_never_output(ledger):
    s, batch, _ = ledger
    batch('2026-01-10', 100)
    first = tb(s, limit=1)
    with pytest.raises(BookflowError) as caught:
        tb(s, limit=1, cursor=first.next_cursor.split('.')[0])
    assert caught.value.code == 'E_VALIDATION'
    payload = base64.urlsafe_b64decode(first.next_cursor.split('.')[0] + '==')
    assert b'key_material' not in payload and b'r'*32 not in payload
    assert 'key_material' not in first.model_dump_json()
    from bookflow.company.records import target_types
    assert 'report_cursor_keys' not in target_types()


def test_company_copy_keeps_key_and_another_company_has_a_distinct_key(tmp_path):
    one, two, copied = (tmp_path / name for name in ('one.db', 'two.db', 'copied.db'))
    for path in (one, two):
        _make_revision(path, 'company', 'co0007', lambda _c: None)
    with sqlite3.connect(one) as db:
        key = db.execute('SELECT key_material FROM report_cursor_keys').fetchone()[0]
    with sqlite3.connect(two) as db:
        other = db.execute('SELECT key_material FROM report_cursor_keys').fetchone()[0]
    assert isinstance(key, bytes) and len(key) == 32 and key != other
    shutil.copy2(one, copied)
    with sqlite3.connect(copied) as db:
        assert db.execute('SELECT key_material FROM report_cursor_keys').fetchone()[0] == key


def test_rotated_company_key_invalidates_old_cursor(ledger):
    s, batch, _ = ledger
    batch('2026-01-10', 100)
    first = tb(s, limit=1)
    s.company.raw.execute('UPDATE report_cursor_keys SET key_material=?', (b's' * 32,))
    with pytest.raises(BookflowError) as caught:
        tb(s, limit=1, cursor=first.next_cursor)
    assert caught.value.code == 'E_VALIDATION'
