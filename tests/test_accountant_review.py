"""R121 accountant review: the external accountant's limits, the accountant's copy round trip, the
lock while a copy is out, cancel, refused change files, idempotent import and the closed period.

The money, by hand: an owner's 500.00 office expense dated 2026-09-10 sits in AR Office; the accountant
moves 120.00 of it to AR Repairs (a reclassification: Repairs debit 120.00, Office credit 120.00) and
accrues 80.00 of AR Repairs against AR Accrued (an adjusting entry), both on or before the dividing date
2026-09-30. After the import, Office is 380.00 and Repairs 200.00 in the owner's books.
"""
import json

import pytest

from bookflow import BookflowError
from tests.conftest import as_user, make_actor
from tests.test_service_sales_lifecycle import COMPANY

pytestmark = pytest.mark.timeout(900)
THROUGH = '2026-09-30'


def write(client, command, company=COMPANY, reason='Accountant review', **data):
    return client.run(command, data, company=company, reason=reason)


def read(client, command, company=COMPANY, **data):
    return client.run(command, data, company=company)


def refused(fn, code):
    with pytest.raises(BookflowError) as e:
        fn()
    assert e.value.code == code, (e.value.code, e.value.message)
    return e.value


def je(client, date, debit, credit, amount, company=COMPANY, memo='entry'):
    return write(client, 'journal post', company=company, date=date, memo=memo, lines=[
        dict(account=debit, side='debit', amount=amount), dict(account=credit, side='credit', amount=amount)])


def balance(client, account, company=COMPANY):
    rows = read(client, 'account list', company=company)['items']
    return next(r for r in rows if r['id'] == account)['balance']


@pytest.fixture
def books(client):
    acct = lambda name, kind: client.account.create(name=name, type=kind, company=COMPANY)['id']  # noqa: E731
    office, repairs = acct('AR Office', 'expense'), acct('AR Repairs', 'expense')
    accrued, bank = acct('AR Accrued', 'other_current_liability'), acct('AR Bank', 'bank')
    spend = je(client, '2026-09-10', office, bank, '500.00', memo='Office and repairs')
    return dict(office=office, repairs=repairs, accrued=accrued, bank=bank, spend=spend['id'])


def test_the_external_accountant_writes_only_adjustments_dated_in_their_period(root, client, books):
    company = read(client, 'company show')['company_id']
    make_actor(root, 'outside-cpa', company_role=(company, 'standard'))
    make_actor(root, 'viewer', company_role=(company, 'readonly'))
    cpa = as_user(root, 'outside-cpa')
    # A read-only member cannot be one; the designation is a person's act.
    refused(lambda: write(client, 'accountant add', user='viewer', through=THROUGH), 'E_VALIDATION')
    added = write(client, 'accountant add', user='outside-cpa', through=THROUGH, note='2026 Q3 review')
    assert added['through'] == THROUGH and added['active'] and added['username'] == 'outside-cpa'
    assert [a['username'] for a in read(client, 'accountant list')['accountants']] == ['outside-cpa']

    # Reads everything; writes adjusting entries on or before the period end.
    assert read(cpa, 'report accountant-review', as_of=THROUGH)['as_of'] == THROUGH
    mine = je(cpa, '2026-09-30', books['repairs'], books['accrued'], '80.00', memo='Accrue repairs')
    late = refused(lambda: je(cpa, '2026-10-01', books['repairs'], books['accrued'], '1.00'), 'E_PERIOD_CLOSED')
    assert late.details['reason'] == 'review_period'
    for command, data in (('customer create', dict(name='AR Not mine')),
                          ('vendor create', dict(name='AR Not my vendor'))):
        assert refused(lambda: write(cpa, command, **data), 'E_PERMISSION').details['reason'] == 'external_accountant'
    refused(lambda: write(cpa, 'company update', legal_name='Nope'), 'E_PERMISSION')  # no admin, as their role says
    # Corrects only their own entries.
    owner_entry = refused(lambda: write(cpa, 'journal void', journal=books['spend']), 'E_PERMISSION')
    assert owner_entry.details['reason'] == 'external_accountant'
    write(cpa, 'journal update', journal=mine['id'], expected_version=mine['version'], memo='Accrue repairs, Sept')

    # Reclassify: an adjusting entry moves 120.00 of the owner's expense; the expense entry is untouched.
    moved = write(cpa, 'accountant reclassify', transaction=books['spend'], from_account='AR Office',
                  to_account='AR Repairs', amount='120.00')
    assert moved['date'] == '2026-09-10'
    assert balance(client, books['office'])['amount'] == '380.00'
    assert balance(client, books['repairs'])['amount'] == '200.00'
    too_much = refused(lambda: write(cpa, 'accountant reclassify', transaction=books['spend'], from_account='AR Office',
                                     to_account='AR Repairs', amount='999.00'), 'E_VALIDATION')
    assert too_much.details['fields'][0]['field'] == 'amount'
    # The owner is not the accountant: reclassify and the marks are the accountant's.
    assert refused(lambda: write(client, 'accountant reviewed-through', through=THROUGH),
                   'E_PERMISSION').details['reason'] == 'not_external_accountant'
    refused(lambda: write(cpa, 'accountant reviewed-through', through='2026-10-31'), 'E_VALIDATION')
    write(cpa, 'accountant reviewed-through', through=THROUGH, note='Q3 looks right')
    write(cpa, 'note add', record_type='transaction', record_id=books['spend'], body='Part of this was repairs.')

    checklist = read(client, 'report accountant-review', as_of=THROUGH)
    assert checklist['reviewed_through']['through'] == THROUGH
    assert next(i for i in checklist['items'] if i['key'] == 'reviewed_through')['done']
    assert any(a['name'] == 'AR Bank' for a in checklist['unreconciled'])
    report = read(client, 'report accountant-changes')
    assert report['journal_entries'] == 2 and report['reclassifications'] == 1 and report['notes'] == 1
    assert {r['accountant_name'] for r in report['rows']} == {'Outside-Cpa'}

    # Removed, they are an ordinary member again.
    write(client, 'accountant remove', user='outside-cpa')
    write(cpa, 'customer create', name='AR Now allowed')


def _copy(root, client, books, through=THROUGH):
    """Export, and open the copy as the accountant would on their own Bookflow: restored as a separate company."""
    out = write(client, 'accountant-copy export', through=through)
    assert out['status'] == 'out' and out['path'].endswith('.bookflow-backup') and out['manifest_file']
    make_actor(root, 'copy-cpa', hub_admin=True)
    cpa = as_user(root, 'copy-cpa')
    copy = cpa.run('company restore', {'archive': out['path'], 'as_copy': True, 'name': 'AR Accountant copy'},
                   reason='Accountant copy')
    return cpa, copy['company_id']


def test_the_copy_round_trip_brings_back_only_the_accountants_changes_attributed(root, client, books):
    cpa, copy = _copy(root, client, books)
    # The owner's books: locked on or before the dividing date, open after it; no account edits.
    lock = refused(lambda: je(client, '2026-09-15', books['office'], books['bank'], '1.00'), 'E_PERIOD_CLOSED')
    assert lock.details == {**lock.details, 'reason': 'accountant_copy_out', 'dividing_date': THROUGH}
    je(client, '2026-10-02', books['office'], books['bank'], '10.00', memo='October')
    assert refused(lambda: write(client, 'account update', account=books['office'], name='AR Office 2'),
                   'E_PERMISSION').details['reason'] == 'accountant_copy_out'
    assert read(client, 'accountant-copy status')['locked_through'] == THROUGH
    refused(lambda: write(client, 'accountant-copy export', through=THROUGH), 'E_VALIDATION')

    # The accountant's copy: only their kinds of change, only on or before the dividing date.
    status = read(cpa, 'accountant-copy status', company=copy)
    assert status['restricted_to'] == THROUGH and status['current']['side'] == 'accountant'
    assert refused(lambda: write(cpa, 'customer create', company=copy, name='AR Copy customer'),
                   'E_PERMISSION').details['reason'] == 'accountant_copy'
    refused(lambda: je(cpa, '2026-10-01', books['repairs'], books['accrued'], '5.00', company=copy), 'E_PERIOD_CLOSED')
    refused(lambda: write(cpa, 'journal void', company=copy, journal=books['spend']), 'E_PERMISSION')
    write(cpa, 'accountant reclassify', company=copy, transaction=books['spend'], from_account='AR Office',
          to_account='AR Repairs', amount='120.00', reason='Repairs booked to office')
    accrual = je(cpa, '2026-09-30', books['repairs'], books['accrued'], '80.00', company=copy, memo='Accrue repairs')
    write(cpa, 'accountant reviewed-through', company=copy, through=THROUGH, note='Q3 done')
    write(cpa, 'note add', company=copy, record_type='transaction', record_id=accrual['id'], body='Invoice came in October.')
    changes = write(cpa, 'accountant-copy changes', company=copy)
    assert changes['counts']['new'] == 4 and not changes['skipped']
    path = changes['path']

    # The owner previews, then imports; each change is posted as the accountant.
    seen = read(client, 'accountant-copy preview', file=path)
    assert seen['counts'] == {'new': 4, 'imported': 0, 'refused': 0}
    assert {c['kind'] for c in seen['changes']} == {'reclassification', 'journal', 'reviewed_through', 'note'}
    taken = write(client, 'accountant-copy import', file=path, reason='Q3 adjustments')
    assert len(taken['imported']) == 4 and taken['status'] == 'returned'
    assert balance(client, books['office'])['amount'] == '390.00'  # 500 - 120 + October's 10
    assert balance(client, books['repairs'])['amount'] == '200.00'
    events = read(client, 'audit list', limit=50)['items']
    imported = [e for e in events if e['command'] == 'accountant-copy import' and (e.get('source_ref') or '').startswith('accountant-copy:')]
    assert len(imported) == 4 and {e['reason'] for e in imported} >= {'Repairs booked to office'}
    by_copy = read(client, 'report accountant-changes')
    assert by_copy['reclassifications'] == 1 and all(r['from_copy'] for r in by_copy['rows'])
    assert {r['accountant_name'] for r in by_copy['rows']} == {'Copy-Cpa'}  # posted as the accountant, not the owner
    # The lock is lifted.
    je(client, '2026-09-20', books['office'], books['bank'], '1.00', memo='after return')

    # Importing again changes nothing.
    again = write(client, 'accountant-copy import', file=path, reason='again')
    assert again['imported'] == [] and again['counts']['imported'] == 4
    assert balance(client, books['repairs'])['amount'] == '200.00'


def test_a_tampered_or_foreign_change_file_is_refused_whole(root, client, books, tmp_path):
    cpa, copy = _copy(root, client, books)
    je(cpa, '2026-09-29', books['repairs'], books['accrued'], '80.00', company=copy, memo='Accrue')
    path = write(cpa, 'accountant-copy changes', company=copy)['path']
    body = json.loads(open(path, encoding='utf-8').read())
    body['changes'][0]['lines'][0]['amount'] = '8000.00'
    tampered = tmp_path / 'tampered.json'
    tampered.write_text(json.dumps(body))
    assert refused(lambda: read(client, 'accountant-copy preview', file=str(tampered)), 'E_VALIDATION').details['reason'] == 'tampered'
    # The accountant's copy is another company: its own change file is not for it.
    refused(lambda: write(cpa, 'accountant-copy import', company=copy, file=path), 'E_PERMISSION')
    assert refused(lambda: read(cpa, 'accountant-copy preview', company=copy, file=path),
                   'E_VALIDATION').details['reason'] == 'wrong_company'
    body = json.loads(open(path, encoding='utf-8').read())
    body['copy_id'] = '01ARZ3NDEKTSV4RRFFQ69G5FAV'
    forged = tmp_path / 'forged.json'
    forged.write_text(json.dumps(body))
    assert refused(lambda: read(client, 'accountant-copy preview', file=str(forged)), 'E_VALIDATION').details['reason'] == 'unknown_copy'
    # Nothing came in, and the copy is still out.
    assert read(client, 'accountant-copy status')['current']['status'] == 'out'


def test_cancel_lifts_the_lock_and_the_file_can_no_longer_come_back(root, client, books):
    cpa, copy = _copy(root, client, books)
    je(cpa, '2026-09-29', books['repairs'], books['accrued'], '80.00', company=copy, memo='Accrue')
    path = write(cpa, 'accountant-copy changes', company=copy)['path']
    refused(lambda: client.run('accountant-copy cancel', {}, company=COMPANY), 'E_REASON_REQUIRED')
    cancelled = write(client, 'accountant-copy cancel', reason='Accountant sent a list instead')
    assert cancelled['status'] == 'cancelled' and cancelled['close_reason'] == 'Accountant sent a list instead'
    je(client, '2026-09-15', books['office'], books['bank'], '1.00')
    assert refused(lambda: read(client, 'accountant-copy preview', file=path), 'E_VALIDATION').details['reason'] == 'cancelled'
    assert read(client, 'accountant-copy status')['current'] is None


def test_closed_periods_stay_closed(root, client, books):
    write(client, 'company update', closing_date='2026-09-05')
    refused(lambda: write(client, 'accountant-copy export', through='2026-09-05'), 'E_VALIDATION')
    cpa, copy = _copy(root, client, books)
    early = je(cpa, '2026-09-08', books['repairs'], books['accrued'], '5.00', company=copy, memo='Early')
    je(cpa, '2026-09-28', books['repairs'], books['accrued'], '7.00', company=copy, memo='Late')
    path = write(cpa, 'accountant-copy changes', company=copy)['path']
    # The owner closes through 2026-09-10 while the copy is out: that change cannot come in.
    write(client, 'company update', closing_date='2026-09-10')
    seen = {c['key']: c for c in read(client, 'accountant-copy preview', file=path)['changes']}
    assert seen[early['id']]['state'] == 'refused' and 'E_PERIOD_CLOSED' in seen[early['id']]['problem']
    refused(lambda: write(client, 'accountant-copy import', file=path, changes=[early['id']]), 'E_VALIDATION')
    taken = write(client, 'accountant-copy import', file=path)
    assert len(taken['imported']) == 1 and taken['counts']['refused'] == 1
