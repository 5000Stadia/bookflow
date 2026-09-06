"""Actual collection lifetimes across streams/host and direct command adapters."""
import re
import sqlite3
from pathlib import Path
import anyio
import pytest
from tests.mcp_matrix_support import Matrix, normalize
from tests.test_attachment_compact import catalog, body
from tests.test_mcp_registry_work import company_snapshot, GHOST

COMMANDS = frozenset({'company compact'})


@pytest.mark.timeout(180)
def test_compaction_preview_collection_replay_and_rejection_parity(root, tmp_path, catalog):
    orphan, orphan_row = body(catalog, b'collect this owned fixture')
    linked, linked_row = body(catalog, b'keep this linked fixture', linked=True)
    baseline_ids = set()
    for path in root.rglob('*.db'):
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
            for line in db.iterdump():
                baseline_ids.update(re.findall(r'\b[0-9A-HJKMNP-TV-Z]{26}\b', line))
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                here = matrix.roots[surface]
                orphan_copy, linked_copy = (here / path.relative_to(root) for path in (orphan, linked))
                before = company_snapshot(here)
                preview = await matrix.call(surface, 'company compact', {}, dry_run=True)
                assert preview['dry_run'] and preview['operation_id'] is None
                assert preview['collected_count'] == 1 and preview['bytes_collected'] == len(orphan.read_bytes())
                assert company_snapshot(here) == before and orphan_copy.read_bytes() == orphan.read_bytes()
                result = await matrix.call(surface, 'company compact', {}, idempotency_key='owned-collection')
                assert not orphan_copy.exists() and linked_copy.read_bytes() == linked.read_bytes()
                assert result['collected_count'] == 1 and result['operation_id']
                after = company_snapshot(here)
                replay = await matrix.call(surface, 'company compact', {}, idempotency_key='owned-collection')
                assert replay == dict(result, idempotent_replay=True) and company_snapshot(here) == after
                for raw, ctx, code in [({'limit':1}, {'idempotency_key':'owned-collection'}, 'E_IDEMPOTENCY_MISMATCH'),
                                       ({'limit':0}, {}, 'E_VALIDATION'), ({}, {'company':GHOST}, 'E_COMPANY_NOT_FOUND')]:
                    rejected = await matrix.call(surface, 'company compact', raw, rejected=True, **ctx)
                    assert rejected['code'] == code and company_snapshot(here) == after
                    assert not orphan_copy.exists() and linked_copy.read_bytes() == linked.read_bytes()
                with sqlite3.connect((here/catalog[0].relative_to(root)).as_uri()+'?mode=ro', uri=True) as db:
                    rows = dict(db.execute('SELECT id,collected_at FROM attachments'))
                    assert rows[orphan_row['id']] and rows[linked_row['id']] is None
                    assert db.execute('SELECT count(*) FROM attachment_collection').fetchone() == (0,)
                    assert db.execute("SELECT interface FROM audit_events WHERE command='company compact'").fetchall() == [(surface,)]
            expected = normalize(matrix.documents['python'], matrix.roots['python'], baseline_ids)
            for surface in ('cli', 'http', 'mcp'):
                actual = normalize(matrix.documents[surface], matrix.roots[surface], baseline_ids)
                assert actual == expected, (surface, actual, expected)
        finally:
            await matrix.close()
    anyio.run(witness)
