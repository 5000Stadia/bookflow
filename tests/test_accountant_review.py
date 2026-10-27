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
    make_actor(root, 'admin-cpa', company_role=(company, 'admin'))
    cpa = as_user(root, 'outside-cpa')
    # A read-only member cannot be one; the designation is a person's act.
    refused(lambda: write(client, 'accountant add', user='viewer', through=THROUGH), 'E_VALIDATION')
    # Nor an admin: an External Accountant administers nothing, so no membership above standard.
    above = refused(lambda: write(client, 'accountant add', user='admin-cpa', through=THROUGH), 'E_VALIDATION')
    assert above.details['role'] == 'admin' and 'standard' in above.message
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
    assert next(i for i in checklist['rows'] if i['key'] == 'reviewed_through')['done']
    assert any(a['name'] == 'AR Bank' for a in checklist['unreconciled'])
    report = read(client, 'report accountant-changes')
    assert report['journal_entries'] == 2 and report['reclassifications'] == 1 and report['notes'] == 1
    assert {r['accountant_name'] for r in report['rows']} == {'Outside-Cpa'}

    # Removed, they are an ordinary member again.
    write(client, 'accountant remove', user='outside-cpa')
    write(cpa, 'customer create', name='AR Now allowed')


def _copy(root, client, books, through=THROUGH):
    """Export, and open the copy as the accountant would on their own Bookflow: restored as a separate company."""
    company = read(client, 'company show')['company_id']
    make_actor(root, 'copy-cpa', company_role=(company, 'standard'))
    # The accountant's own Bookflow, standing in for their machine: here, an installation administrator of the same root.
    make_actor(root, 'acct-desk', hub_admin=True)
    # The copy is for the designated accountant: everything it sends back is posted as them.
    refused(lambda: write(client, 'accountant-copy export', through=through), 'E_VALIDATION')
    write(client, 'accountant add', user='copy-cpa', through=through)
    out = write(client, 'accountant-copy export', through=through)
    assert out['status'] == 'out' and out['path'].endswith('.bookflow-backup') and out['manifest_file']
    assert out['accountant_name'] == 'Copy-Cpa'
    cpa = as_user(root, 'acct-desk')
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
    assert len(imported) == 4 and 'Repairs booked to office (entered in the copy as Acct-Desk)' in {e['reason'] for e in imported}
    by_copy = read(client, 'report accountant-changes')
    assert by_copy['reclassifications'] == 1 and all(r['from_copy'] for r in by_copy['rows'])
    assert {r['accountant_name'] for r in by_copy['rows']} == {'Copy-Cpa'}  # posted as the accountant, not the owner
    # The lock is lifted.
    je(client, '2026-09-20', books['office'], books['bank'], '1.00', memo='after return')

    # The copy is closed: its file cannot come in again, and nothing changes.
    again = refused(lambda: write(client, 'accountant-copy import', file=path, reason='again'), 'E_VALIDATION')
    assert again.details['reason'] == 'returned'
    assert balance(client, books['repairs'])['amount'] == '200.00'
    assert by_copy['journal_entries'] == 2  # the reclassification and the accrual, both posted as the accountant


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


def _as_agent(root, login, principal, name, raw):
    from bookflow.core import registry
    from bookflow.core.context import Context, Interface
    from bookflow.core.dispatch import run as dispatch_run
    ctx = Context.new(Interface.python, 'accountant agent', on_behalf_of=principal, reason='agent work')
    return dispatch_run(registry.get(name), raw, ctx, data_root=str(root), company_selector=COMPANY,
                        company_source='option', _login=login)


def test_an_agent_acting_for_the_accountant_is_held_to_the_accountants_terms(root, client, books):
    from bookflow.core.config import Config
    from tests.conftest import make_agent
    company = read(client, 'company show')['company_id']
    cpa = make_actor(root, 'outside-cpa', company_role=(company, 'standard'))
    write(client, 'accountant add', user='outside-cpa', through=THROUGH)
    agent = make_agent(lambda n, b: client.run(n, b), 'cpa-agent', principals=cpa, company=company, role='standard')
    cfg = Config.load(root / 'config.toml')
    cfg.set_user('cpa-agent', agent)
    cfg.save()
    run = lambda name, raw: _as_agent(root, 'cpa-agent', cpa, name, raw)  # noqa: E731
    line = lambda date, amount: {'date': date, 'memo': 'agent', 'lines': [  # noqa: E731
        {'account': books['repairs'], 'side': 'debit', 'amount': amount}, {'account': books['accrued'], 'side': 'credit', 'amount': amount}]}
    assert refused(lambda: run('customer create', {'name': 'Agent customer'}), 'E_PERMISSION').details['reason'] == 'external_accountant'
    assert refused(lambda: run('journal post', line('2026-12-15', '7.00')), 'E_PERIOD_CLOSED').details['reason'] == 'review_period'
    assert refused(lambda: run('journal void', {'journal': books['spend']}), 'E_PERMISSION').details['reason'] == 'external_accountant'
    # Inside the terms it works, and the report counts it as the accountant's.
    run('journal post', line('2026-09-29', '3.00'))
    run('note add', {'record_type': 'transaction', 'record_id': books['spend'], 'body': 'Agent: part of this is repairs.'})
    owner_note = write(client, 'note add', record_type='transaction', record_id=books['spend'], body="Owner's note.")['note']
    refused(lambda: run('note edit', {'note': owner_note['id'], 'expected_version': owner_note['version'], 'body': 'x'}), 'E_PERMISSION')
    assert refused(lambda: run('accountant reviewed-through', {'through': THROUGH}), 'E_PERMISSION').details['reason'] == 'person_only'
    report = read(client, 'report accountant-changes')
    assert report['journal_entries'] == 1 and report['notes'] == 1
    assert {(r['accountant_name'], r['agent_name']) for r in report['rows']} == {('Outside-Cpa', 'cpa-agent')}


def test_the_change_file_cannot_name_who_made_a_change(root, client, books, tmp_path):
    import hashlib
    import hmac
    import sqlite3
    cpa, copy = _copy(root, client, books)
    je(cpa, '2026-09-29', books['repairs'], books['accrued'], '80.00', company=copy, memo='Accrue')
    path = write(cpa, 'accountant-copy changes', company=copy)['path']
    copy_db = next(p for p in root.rglob('company.db') if p.parent.name == 'AR Accountant copy')
    with sqlite3.connect(copy_db) as db:  # the copy holder has the key: it is in their copy
        key = db.execute("SELECT mac_key FROM accountant_copies WHERE side='accountant'").fetchone()[0]
    owner_db = next(p for p in root.rglob('company.db') if p.parent.name == COMPANY)
    with sqlite3.connect(owner_db) as db:
        owner_id = db.execute("SELECT created_by FROM transactions WHERE id=?", (books['spend'],)).fetchone()[0]
        principals = db.execute('SELECT user_id, username, display_name FROM principals ORDER BY user_id').fetchall()
    body = json.loads(open(path, encoding='utf-8').read())
    body.pop('mac')
    body['changes'][0]['by'] = {'user_id': owner_id, 'name': 'TOTALLY THE OWNER'}
    body['accountant'] = {'user_id': owner_id, 'username': 'owner', 'name': 'TOTALLY THE OWNER'}
    canonical = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    body['mac'] = hmac.new(bytes.fromhex(key), canonical, hashlib.sha256).hexdigest()
    forged = tmp_path / 'forged.json'
    forged.write_text(json.dumps(body))
    seen = read(client, 'accountant-copy preview', file=str(forged))
    assert seen['accountant_name'] == 'Copy-Cpa' and seen['named_in_file_as'] == 'TOTALLY THE OWNER'
    assert seen['changes'][0]['by_name'] == 'Copy-Cpa' and seen['changes'][0]['named_in_file_as'] == 'TOTALLY THE OWNER'
    taken = write(client, 'accountant-copy import', file=str(forged), reason='Q3')
    tx = taken['imported'][0]
    with sqlite3.connect(owner_db) as db:
        actors = {r[0] for r in db.execute("""SELECT e.actor_id FROM audit_events e JOIN audit_entries ae ON ae.event_id=e.id
                                               WHERE ae.record_type='transaction' AND ae.record_id IN
                                               (SELECT transaction_id FROM accountant_adjustments)""")}
        adjusted = db.execute('SELECT accountant_user_id FROM accountant_adjustments').fetchall()
        reasons = [r[0] for r in db.execute("SELECT reason FROM audit_events WHERE source_ref LIKE 'accountant-copy:%'")]
        after = db.execute('SELECT user_id, username, display_name FROM principals ORDER BY user_id').fetchall()
        cpa_id = db.execute("SELECT user_id FROM accountant_designations").fetchone()[0]
    assert tx and actors == {cpa_id} and adjusted == [(cpa_id,)], 'posted as the copy\'s accountant, never the file\'s name'
    assert after == principals, 'no principal is created or renamed from a change file'
    assert any('TOTALLY THE OWNER' in (r or '') for r in reasons)  # kept only as a label in the reason


def test_no_backup_carries_the_copy_key_and_a_returned_copy_is_closed(root, client, books, tmp_path):
    import sqlite3
    import zipfile
    cpa, copy = _copy(root, client, books)
    je(cpa, '2026-09-29', books['repairs'], books['accrued'], '80.00', company=copy, memo='Accrue')
    path = write(cpa, 'accountant-copy changes', company=copy)['path']
    archive = write(client, 'company backup')['path']
    with zipfile.ZipFile(archive) as z:
        z.extract('company.db', tmp_path)
    with sqlite3.connect(tmp_path / 'company.db') as db:
        keys = {r[0] for r in db.execute("SELECT mac_key FROM accountant_copies WHERE side='owner'")}
    assert keys == {'0' * 64}
    owner_db = next(p for p in root.rglob('company.db') if p.parent.name == COMPANY)
    write(client, 'accountant-copy import', file=path, reason='Q3')
    with sqlite3.connect(owner_db) as db:
        assert {r[0] for r in db.execute('SELECT mac_key FROM accountant_copies')} == {'0' * 64}  # wiped on return
    assert refused(lambda: read(client, 'accountant-copy preview', file=path), 'E_VALIDATION').details['reason'] == 'returned'


def test_a_designated_accountant_stays_at_standard_and_changes_no_ones_access(root, client, books):
    company = read(client, 'company show')['company_id']
    make_actor(root, 'outside-cpa', company_role=(company, 'standard'))
    make_actor(root, 'root-cpa', hub_admin=True, company_role=(company, 'standard'))
    make_actor(root, 'friend')
    assert refused(lambda: write(client, 'accountant add', user='root-cpa', through=THROUGH),
                   'E_VALIDATION').details['reason'] == 'hub_admin'
    write(client, 'accountant add', user='outside-cpa', through=THROUGH)
    grant = lambda who, actor=client, role='admin': actor.run('membership grant', {'user': who, 'company': company, 'role': role},  # noqa: E731
                                                              reason='access')
    raised = refused(lambda: grant('outside-cpa'), 'E_VALIDATION')
    assert raised.details['reason'] == 'external_accountant' and 'accountant remove' in raised.message
    # The accountant administers no one's access there, whatever they hold.
    cpa = as_user(root, 'outside-cpa')
    refused(lambda: grant('friend', cpa), 'E_PERMISSION')
    write(client, 'accountant remove', user='outside-cpa')
    assert grant('outside-cpa')['role'] == 'admin'


def test_the_accountant_edits_only_their_own_notes(root, client, books):
    company = read(client, 'company show')['company_id']
    make_actor(root, 'outside-cpa', company_role=(company, 'standard'))
    write(client, 'accountant add', user='outside-cpa', through=THROUGH)
    cpa = as_user(root, 'outside-cpa')
    theirs = write(client, 'note add', record_type='transaction', record_id=books['spend'], body="Owner's note.")['note']
    mine = write(cpa, 'note add', record_type='transaction', record_id=books['spend'], body='Accountant note.')['note']
    assert refused(lambda: write(cpa, 'note edit', note=theirs['id'], expected_version=theirs['version'], body='Rewritten'),
                   'E_PERMISSION').details['reason'] == 'external_accountant'
    assert write(cpa, 'note edit', note=mine['id'], expected_version=mine['version'], body='Accountant note, fixed.')['note']['body'] \
        == 'Accountant note, fixed.'


def test_an_import_that_takes_nothing_needs_close_copy(root, client, books):
    cpa, copy = _copy(root, client, books)
    je(cpa, '2026-09-29', books['repairs'], books['accrued'], '80.00', company=copy, memo='Accrue')
    je(cpa, '2026-09-28', books['repairs'], books['accrued'], '5.00', company=copy, memo='Small')
    path = write(cpa, 'accountant-copy changes', company=copy)['path']
    nothing = refused(lambda: write(client, 'accountant-copy import', file=path, changes=[]), 'E_VALIDATION')
    assert nothing.details['reason'] == 'nothing_taken' and nothing.details['declined'] == 2 and '2 would be declined' in nothing.message
    assert read(client, 'accountant-copy status')['current']['status'] == 'out'
    closed = write(client, 'accountant-copy import', file=path, changes=[], close_copy=True)
    assert closed['imported'] == [] and closed['declined'] == 2 and closed['status'] == 'returned'
