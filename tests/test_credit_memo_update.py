"""Actual correction commands: endpoint cents, immutable history, and atomic refusal."""
import sqlite3

import pytest

from bookflow.core.errors import BookflowError
from tests.credit_support import books, balances, goodwill_credit, returned_credit, taxed_invoice
from tests.test_credit_memo_lifecycle import _three_unit_invoice


def update(books, credit, **patch):
    return books['run']('credit-memo update', dict(
        credit_memo=credit['id'], expected_version=credit['version'], **patch), reason='Correct the credit')


def snapshot(books):
    with sqlite3.connect(books['database']) as db:
        return list(db.iterdump())


def test_quantity_correction_returns_exact_endpoint_cents_and_preserves_history(books):
    invoice = _three_unit_invoice(books)
    source_line = invoice['revision']['lines'][0]['line_id']
    first = returned_credit(books, invoice['id'], source_line)
    second = returned_credit(books, invoice['id'], source_line)
    # First unit (33+2) and last unit (34+3), while the second credit still owns the middle.
    corrected = update(books, first, lines=[dict(
        line_id=first['revision']['lines'][0]['line_id'], source_invoice=invoice['id'],
        source_line=source_line, quantity='2')])
    assert corrected['total_minor_units'] == 72
    line = corrected['revision']['lines'][0]
    assert (line['net_minor_units'], line['tax_minor_units']) == (67, 5)
    assert line['line_id'] == first['revision']['lines'][0]['line_id']
    assert [(row['start_microunits'], row['end_microunits']) for row in line['claims']] == [
        (0, 1_000_000), (2_000_000, 3_000_000)]
    assert corrected['source_current']['credit_source_key_id'] == first['source_current']['credit_source_key_id']
    old = books['run']('credit-memo show', dict(credit_memo=first['id'], revision_number=1))
    assert old['revision']['lines'] == first['revision']['lines']
    assert books['run']('credit-memo show', dict(credit_memo=second['id']))['total_minor_units'] == 35
    totals, _ = balances(books)
    assert totals.get(books['receivable'], 0) == totals.get(books['income'], 0) == totals.get(books['liability'], 0) == 0
    before = snapshot(books)
    with pytest.raises(BookflowError) as error:
        returned_credit(books, invoice['id'], source_line)
    assert error.value.code == 'E_RETURN_EXHAUSTED'
    assert snapshot(books) == before


@pytest.mark.parametrize('linked', [False, True])
def test_header_only_retains_captures_after_master_changes_and_retries_once(books, linked):
    invoice = taxed_invoice(books)
    credit = (returned_credit(books, invoice['id'], invoice['revision']['lines'][0]['line_id']) if linked
              else goodwill_credit(books, '30.00'))
    item = books['widget'] if linked else books['service']
    books['run']('item update', dict(item=item, price='99.00', description='Changed description'))
    request = dict(credit_memo=credit['id'], expected_version=credit['version'], memo='Corrected note')
    result = books['run']('credit-memo update', request, idempotency_key='memo-correction', reason='Correct note')
    assert result['total_minor_units'] == credit['total_minor_units']
    old_line, new_line = credit['revision']['lines'][0], result['revision']['lines'][0]
    for key in ('line_id', 'item_snapshot', 'quantity', 'description', 'net_minor_units', 'tax_minor_units'):
        assert new_line[key] == old_line[key]
    before = snapshot(books)
    replay = books['run']('credit-memo update', request, idempotency_key='memo-correction', reason='Correct note')
    assert replay['revision']['id'] == result['revision']['id']
    assert snapshot(books) == before
    with pytest.raises(BookflowError) as error:
        update(books, credit, memo='Stale correction')
    assert error.value.code == 'E_VERSION_CONFLICT'
    assert snapshot(books) == before


def test_refused_quantity_leaves_every_row_unchanged(books):
    invoice = _three_unit_invoice(books)
    source_line = invoice['revision']['lines'][0]['line_id']
    credit = returned_credit(books, invoice['id'], source_line)
    returned_credit(books, invoice['id'], source_line)
    before = snapshot(books)
    with pytest.raises(BookflowError) as error:
        update(books, credit, lines=[dict(source_invoice=invoice['id'], source_line=source_line, quantity='3')])
    assert error.value.code == 'E_RETURN_EXHAUSTED'
    assert snapshot(books) == before


@pytest.mark.parametrize('consumed', ['applied', 'refunded'])
def test_consumed_noop_is_independent_of_pending_correction_policy(books, consumed):
    from tests.credit_support import apply_credit, invoice, refund
    credit = goodwill_credit(books, '30.00')
    if consumed == 'applied':
        target = invoice(books)
        apply_credit(books, credit, target['id'], amount='10.00')
    else:
        refund(books, credit['id'], '10.00')
    current = books['run']('credit-memo show', dict(credit_memo=credit['id']))
    before = snapshot(books)
    assert update(books, current)['changed'] is False
    assert update(books, current, number=current['number'], memo=current['revision']['memo'])['changed'] is False
    assert snapshot(books) == before
    with pytest.raises(BookflowError) as error:
        update(books, dict(current, version=current['version'] + 1))
    assert error.value.code == 'E_VERSION_CONFLICT'
    assert snapshot(books) == before


def test_closed_dates_refuse_without_writes_and_empty_patch_stays_empty(books):
    credit = goodwill_credit(books, '30.00')
    books['run']('company update', {'closing_date': '2026-03-31'})
    before = snapshot(books)
    assert update(books, credit)['changed'] is False
    with pytest.raises(BookflowError) as error:
        update(books, credit, date='2026-04-10', memo='Move to April')
    assert error.value.code == 'E_PERIOD_CLOSED'
    assert snapshot(books) == before


def test_preview_stales_when_another_return_takes_its_quantity(books):
    invoice = _three_unit_invoice(books)
    source_line = invoice['revision']['lines'][0]['line_id']
    credit = returned_credit(books, invoice['id'], source_line)
    patch = dict(credit_memo=credit['id'], expected_version=credit['version'], lines=[dict(
        line_id=credit['revision']['lines'][0]['line_id'],
        source_invoice=invoice['id'], source_line=source_line, quantity='2')])
    preview = books['run']('credit-memo update', patch, dry_run=True)
    # The other writer takes the next interval; corrected total changes from 70 to 72.
    returned_credit(books, invoice['id'], source_line)
    before = snapshot(books)
    with pytest.raises(BookflowError) as error:
        books['run']('credit-memo update', dict(patch, expected_facts_fingerprint=preview['facts_fingerprint']))
    assert error.value.code == 'E_PREVIEW_STALE'
    assert snapshot(books) == before


def test_validator_rejects_balanced_but_wrong_reversal_and_claim_release(books, monkeypatch):
    from bookflow.company import credit_validation
    invoice = _three_unit_invoice(books)
    credit = returned_credit(books, invoice['id'], invoice['revision']['lines'][0]['line_id'])
    original = credit_validation.validate_update
    before = snapshot(books)
    for corrupt in ('reversal', 'claim'):
        def validate(plan, session, context):
            if corrupt == 'reversal':
                # Still balances at the same gross, but reverses the wrong account.
                plan.data['pending']['posting_lines'][0]['account_id'] = books['checking']
            else:
                plan.data['pending']['credit_source_claims'][0]['end_microunits'] += 1
            return original(plan, session, context)
        monkeypatch.setattr(credit_validation, 'validate_update', validate)
        with pytest.raises(BookflowError) as error:
            update(books, credit, memo='Cannot commit corrupt plan')
        assert error.value.code == 'E_INTERNAL'
        assert snapshot(books) == before


def test_failed_write_rolls_back_releases_and_replacement_as_one_operation(books, monkeypatch):
    import sqlalchemy as sa
    from sqlalchemy.engine import Engine
    invoice = _three_unit_invoice(books)
    credit = returned_credit(books, invoice['id'], invoice['revision']['lines'][0]['line_id'])
    before = snapshot(books)
    observed = []

    def after_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('INSERT INTO credit_source_claims'):
            # The pending releases AND retakes have reached SQLite. An independent reader
            # must still see the old whole document until the outer transaction commits.
            with sqlite3.connect(books['database']) as reader:
                observed.append(reader.execute(
                    'SELECT current_revision_id FROM transactions WHERE id=?', (credit['id'],)).fetchone()[0])
            raise RuntimeError('injected after claim insertion')
    sa.event.listen(Engine, 'after_cursor_execute', after_insert)
    try:
        with pytest.raises(RuntimeError, match='injected'):
            update(books, credit, memo='Rolled back')
    finally:
        sa.event.remove(Engine, 'after_cursor_execute', after_insert)
    assert observed == [credit['revision']['id']]
    assert snapshot(books) == before


def test_reducing_return_releases_the_last_endpoint_for_another_credit(books):
    invoice = _three_unit_invoice(books)
    source_line = invoice['revision']['lines'][0]['line_id']
    credit = returned_credit(books, invoice['id'], source_line, quantity='3')
    corrected = update(books, credit, lines=[dict(
        line_id=credit['revision']['lines'][0]['line_id'],
        source_invoice=invoice['id'], source_line=source_line, quantity='2')])
    assert corrected['total_minor_units'] == 70
    last = returned_credit(books, invoice['id'], source_line)
    assert last['total_minor_units'] == 37
    assert (last['revision']['lines'][0]['net_minor_units'], last['revision']['lines'][0]['tax_minor_units']) == (34, 3)
    totals, _ = balances(books)
    assert totals.get(books['receivable'], 0) == 0
    # Voiding a corrected credit must reverse the replacement, not the first original batch.
    books['run']('credit-memo void', dict(credit_memo=corrected['id'], expected_version=corrected['version']),
                 reason='Cancel corrected credit')
    again = returned_credit(books, invoice['id'], source_line, quantity='2')
    assert again['total_minor_units'] == 70


def test_standalone_amount_correction_and_retained_tax_after_rate_change(books):
    credit = books['run']('credit-memo post', dict(
        customer=books['kerr'], date='2026-04-05', sales_tax_item=books['tax_item'],
        lines=[dict(item=books['widget'], quantity='4', unit_price='25.00', tax_code=books['taxable'])]))
    books['run']('item update', dict(item=books['tax_item'], tax_percent='10'))
    header = update(books, credit, memo='Preserve eight percent')
    assert header['total_minor_units'] == 10800
    assert header['revision']['lines'][0]['tax_components'][0]['rate_percent_millionths'] == 8_000_000
    corrected = update(books, header, lines=[dict(
        line_id=header['revision']['lines'][0]['line_id'], item=books['widget'], quantity='2')])
    assert corrected['total_minor_units'] == 5400
    totals, _ = balances(books)
    assert totals[books['receivable']] == -5400
    assert totals[books['income']] == 5000
    assert totals[books['liability']] == 400


@pytest.mark.parametrize('linked', [False, True])
def test_customer_conflict_never_leaves_profile_and_capacity_owned_by_different_parties(books, linked):
    if linked:
        invoice = taxed_invoice(books)
        credit = returned_credit(books, invoice['id'], invoice['revision']['lines'][0]['line_id'])
        customer = books['ridge']
    else:
        credit = goodwill_credit(books, '30.00')
        customer = books['kerr']
    before = snapshot(books)
    with pytest.raises(BookflowError) as error:
        update(books, credit, customer=customer)
    assert error.value.code == 'E_VALIDATION'
    assert error.value.details['fields'][0]['field'] == ('source_invoice' if linked else 'customer')
    assert snapshot(books) == before


def test_cli_correction_can_be_continued_and_cleared_by_python(books):
    import json
    import subprocess
    import sys
    credit = goodwill_credit(books, '30.00')
    result = subprocess.run([
        sys.executable, '-c', 'from bookflow.bootstrap import main; main()', 'credit-memo', 'update', credit['id'],
        '--expected-version', str(credit['version']), '--memo', 'Entered through CLI',
        '--company', books['company'], '--reason', 'Correct credit', '--json'],
        text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert isinstance(json.loads(result.stdout), dict)
    # CLI returns the common result envelope, just as other commands do.
    saved = books['run']('credit-memo show', dict(credit_memo=credit['id']))
    assert saved['revision']['memo'] == 'Entered through CLI'
    assert saved['version'] == credit['version'] + 1
    corrected = update(books, saved, memo=None)
    assert corrected['revision']['memo'] is None
    assert corrected['total_minor_units'] == 3000
