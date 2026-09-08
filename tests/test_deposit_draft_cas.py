"""Real SQLite lost-CAS faults at the private nonposting writer boundary."""
import hashlib
import json

import pytest

from bookflow.company import deposit_drafts as drafts, deposit_selection as child
from bookflow.company import deposit_draft_models as m
from bookflow.core.context import Context, Interface
from bookflow.core.errors import BookflowError
from tests.test_deposit_drafts import cash, driver, financial, run
from tests.test_service_sales_lifecycle import sale


@pytest.mark.parametrize('kind', ['draft', 'selection'])
def test_zero_row_header_cas_rolls_back_and_allows_retry(kind, cash, driver, run, tmp_path):
    parent = run('create', dict(header=dict(date='2026-06-03')))
    original = (run('create', dict(draft=parent.id, expected_version=parent.version), True)
                if kind == 'selection' else parent)
    owner = child if kind == 'selection' else drafts
    header_table = 'deposit_selections' if kind == 'selection' else 'deposit_drafts'
    revision_table = 'deposit_' + kind + '_revisions'
    source_table = 'deposit_' + kind + '_sources'
    inp = owner.INPUTS['update'].model_validate({kind: original.id,
        'expected_version': original.version, 'set_sources': [cash]})
    show_input = m.SelectionShow(selection=original.id) if kind == 'selection' else m.DraftShow(draft=original.id)
    ctx = Context.new(Interface.python, 'G3 real SQLite CAS fault')
    with driver.session() as s:
        raw = s.company.raw
        before_financial = financial(s)
        raw.execute('CREATE TABLE own_cas_sentinel(value BLOB NOT NULL)')
        raw.execute("INSERT INTO own_cas_sentinel VALUES (X'00FF41')")
        # Valid SQLite fault: prior version reads succeed, but the actual UPDATE
        # affects zero rows. No rowcount/Session/production function is forged.
        # Both table and identity come only from the fixed enum/owned producer.
        raw.execute(f"CREATE TRIGGER own_cas_ignore BEFORE UPDATE ON {header_table} "
                    f"WHEN OLD.id='{original.id}' BEGIN SELECT RAISE(IGNORE); END")
        before = tuple(raw.iterdump())
        event_count = raw.execute('SELECT count(*) FROM audit_events').fetchone()[0]
        revision_count = raw.execute(f'SELECT count(*) FROM {revision_table}').fetchone()[0]
        source_count = raw.execute(f'SELECT count(*) FROM {source_table}').fetchone()[0]
        with pytest.raises(BookflowError) as error:
            owner.run(s, ctx, inp, 'update')
        assert error.value.code == 'E_VERSION_CONFLICT' and error.value.details == {}
        after = tuple(raw.iterdump())
        assert after == before  # Entire revision/content/audit/financial graph + DDL.
        assert raw.execute('SELECT value FROM own_cas_sentinel').fetchall() == [(b'\x00\xffA',)]
        assert owner.show(s, show_input) == original
        assert drafts.show(s, m.DraftShow(draft=parent.id)) == parent
        assert raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        raw.execute('DROP TRIGGER own_cas_ignore')
        updated = owner.run(s, ctx, inp, 'update')  # Same version/input, normal SQL.
        assert updated.version == original.version + 1
        assert updated.revision_id != original.revision_id
        assert owner.show(s, show_input) == updated
        assert raw.execute('SELECT count(*) FROM audit_events').fetchone()[0] == event_count + 1
        assert raw.execute(f'SELECT count(*) FROM {revision_table}').fetchone()[0] == revision_count + 1
        assert raw.execute(f'SELECT count(*) FROM {source_table}').fetchone()[0] == source_count + 1
        assert financial(s) == before_financial
        assert raw.execute('SELECT value FROM own_cas_sentinel').fetchall() == [(b'\x00\xffA',)]
        if kind == 'selection':
            assert drafts.show(s, m.DraftShow(draft=parent.id)) == parent
        assert raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
    with driver.session() as s:
        assert owner.show(s, show_input) == updated
        assert financial(s) == before_financial
    digest = lambda rows: hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()
    (tmp_path / 'cas-receipt.json').write_text(json.dumps(dict(kind=kind,
        original=original.model_dump(mode='json'), subsequent=updated.model_dump(mode='json'),
        source=cash, failed_before_sha256=digest(before), failed_after_sha256=digest(after),
        event_delta=1, revision_delta=1, source_row_delta=1,
        failure='E_VERSION_CONFLICT', sentinel_hex='00ff41', financial_unchanged=True), indent=2))
