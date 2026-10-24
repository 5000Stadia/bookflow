"""Print Checks (R152): queue, print run, "Did checks print OK?", reprints, alignment, words.

The chequebook, by hand. Checking's next check number is unset, so the first run proposes 1;
these tests type 1001 as the anchor's Select Checks to Print lets a person do.

    Queued, oldest first: check A 1,234.56 (03-04), check B 0.05 (03-04), and a bill payment
    by check (03-05) paying bill INV-77 of 300.00 in full, so its stub reads 300.00 paid.

    Run 1 from 1001: A=1001, B=1002, payment=1003. "Did checks print OK?" -- 1002 onward jammed.
    A keeps 1001; 1002 and 1003 are spoiled (misprinted) and B and the payment go back to the
    queue. Run 2 proposes 1004: B=1004, payment=1005, both fine. missing-checks then reads
    1001 used, 1002-1003 spoiled, 1004-1005 used: no hole at all.
"""
import base64
import io

import pytest
from pypdf import PdfReader

import bookflow
from bookflow import BookflowError
from bookflow.documents import check_pdf

AS_OF = '2026-12-31'


@pytest.fixture
def books(tmp_path, monkeypatch):
    root = tmp_path / 'printing'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.organization.new(name='Print organization')
    company = client.company.new(legal_name='Print Shop', home_currency='USD', organization='Print organization',
                                 timezone='UTC', chart='general')['company_id']
    accounts = client.account.query(company=company, limit=200)['items']
    bank = next(row['id'] for row in accounts if row['type'] == 'bank')
    expense = next(row['id'] for row in accounts if row['type'] == 'expense')
    vendor = client.vendor.create(name='Northside Supply', company=company,
                                  address={'line1': '1 Main St', 'city': 'Springfield', 'state': 'IL',
                                           'postal_code': '62701'})['id']
    methods = {row['kind']: row['id'] for row in
               client.run('payment-method query', dict(limit=50), company=company)['items']}

    def run(name, raw=None, **context):
        return client.run(name, raw or {}, company=company, **context)

    return dict(client=client, company=company, bank=bank, expense=expense, vendor=vendor, methods=methods, run=run)


def _check(books, amount, **extra):
    body = dict(account=books['bank'], pay_to={'name_type': 'vendor', 'name_id': books['vendor']},
                date='2026-03-04', amount=amount, memo='Supplies',
                expenses=[{'account': books['expense'], 'amount': amount, 'memo': 'Paper stock'}], to_be_printed=True)
    body.update(extra)
    return books['run']('check post', body, reason='Write a check to print later')


def _bill_payment(books, amount='300.00'):
    bill = books['run']('bill post', dict(vendor=books['vendor'], date='2026-03-03', number='INV-77',
                                          expenses=[{'account': books['expense'], 'amount': amount}]), reason='Enter a bill')
    paid = books['run']('bill pay', dict(date='2026-03-05', bills=[{'bill': bill['id']}], funding_account=books['bank'],
                                         method=books['methods']['check'], to_be_printed=True), reason='Pay Bills')
    return bill, paid['payments'][0]


def _queue(books):
    out = books['run']('check print-queue', {'account': books['bank']})
    return out['accounts'][0]


def _pdf(out):
    return PdfReader(io.BytesIO(base64.b64decode(out['content_base64'])))


def _placed(reader, page=0):
    """Every string drawn on a page with the point it was drawn at."""
    found = []

    def visit(text, cm, tm, *_):
        if text.strip():
            found.append((text.strip(), round(tm[4], 2), round(tm[5], 2)))
    reader.pages[page].extract_text(visitor_text=visit)
    return found


# ---------------------------------------------------------------- the queue and a run


def test_queued_checks_print_in_order_from_the_confirmed_first_number(books):
    a = _check(books, '1234.56')
    b = _check(books, '0.05')
    _, payment = _bill_payment(books)
    # Saved to be printed: no number yet, and in the queue in date order.
    assert a['document']['check_number'] is None and a['document']['print_status'] == 'queued'
    assert payment['check_number'] is None
    queue = _queue(books)
    assert [row['transaction_id'] for row in queue['checks']] == [a['id'], b['id'], payment['id']]
    assert [row['document_type'] for row in queue['checks']] == ['check', 'check', 'bill_payment']
    assert queue['total']['amount'] == '1534.61'

    run = books['run']('check print', {'account': books['bank'], 'first_number': '1001', 'style': 'voucher'},
                       reason='Print checks')
    assert [(i['check_number'], i['transaction_id'], i['outcome']) for i in run['items']] == [
        ('1001', a['id'], 'pending'), ('1002', b['id'], 'pending'), ('1003', payment['id'], 'pending')]
    assert run['status'] == 'awaiting_confirmation'
    assert run['items'][0]['amount_in_words'] == 'One thousand two hundred thirty-four and 56/100'
    # Out of the queue while in the printer; the numbers are held, so a hand-written 1002 is refused.
    assert _queue(books)['checks'] == []
    assert books['run']('check show', {'check': b['id']})['document']['print_status'] == 'in_run'
    with pytest.raises(BookflowError) as held:
        _check(books, '5.00', to_be_printed=False, number='1002')
    assert held.value.code == 'E_DUPLICATE_NUMBER' and held.value.details['held_by'] == b['id']
    # ...and a check in the printer cannot be corrected or voided until the run is answered.
    with pytest.raises(BookflowError) as busy:
        books['run']('check update', {'check': a['id'], 'memo': 'changed'}, reason='Edit')
    assert busy.value.code == 'E_VALIDATION' and busy.value.details['run'] == run['id']

    confirmed = books['run']('check print-confirm', {'run': run['id']}, reason='All printed')
    assert [i['outcome'] for i in confirmed['items']] == ['printed'] * 3 and confirmed['status'] == 'confirmed'
    assert books['run']('check show', {'check': a['id']})['document']['check_number'] == '1001'
    assert books['run']('check show', {'check': a['id']})['document']['print_status'] == 'printed'
    assert books['run']('bill payment show', {'payment': payment['id']})['check_number'] == '1003'
    # The pointer moved past the run, so the next check written by hand is 1004.
    assert _check(books, '9.00', to_be_printed=False)['document']['check_number'] == '1004'


def test_failed_checks_go_back_for_new_numbers_and_missing_checks_explains_the_spoiled_ones(books):
    a = _check(books, '1234.56')
    b = _check(books, '0.05')
    _, payment = _bill_payment(books)
    first = books['run']('check print', {'account': books['bank'], 'first_number': '1001'}, reason='Print')
    answered = books['run']('check print-confirm', {'run': first['id'], 'failed_from': '1002'}, reason='Jammed at 1002')
    assert [(i['check_number'], i['outcome']) for i in answered['items']] == [
        ('1001', 'printed'), ('1002', 'failed'), ('1003', 'failed')]
    assert answered['spoiled_numbers'] == ['1002', '1003']
    queue = _queue(books)
    assert [row['transaction_id'] for row in queue['checks']] == [b['id'], payment['id']]
    assert queue['next_check_number'] == '1004'
    with pytest.raises(BookflowError) as again:
        books['run']('check print-confirm', {'run': first['id']}, reason='Twice')
    assert again.value.code == 'E_VALIDATION'
    # A spoiled number is never printed again: starting the run on 1003 is refused by name.
    with pytest.raises(BookflowError) as spoiled:
        books['run']('check print', {'account': books['bank'], 'first_number': '1003'}, reason='Print')
    assert spoiled.value.code == 'E_DUPLICATE_NUMBER' and spoiled.value.details['number'] == '1003'

    second = books['run']('check print', {'account': books['bank']}, reason='Print the rest')
    assert [(i['check_number'], i['transaction_id']) for i in second['items']] == [('1004', b['id']), ('1005', payment['id'])]
    books['run']('check print-confirm', {'run': second['id']}, reason='Fine')

    report = books['run']('report missing-checks', {'as_of': AS_OF})
    assert report['totals']['gaps'] == 0 and report['totals']['spoiled_numbers'] == 2
    spoiled_rows = [(row['spoiled_number'], row['spoiled_reason'], row['printed_for']) for row in report['rows']
                    if row['kind'] == 'spoiled']
    assert spoiled_rows == [(1002, 'misprinted', b['id']), (1003, 'misprinted', payment['id'])]


def test_cancel_releases_numbers_without_spoiling_them(books):
    _check(books, '10.00')
    run = books['run']('check print', {'account': books['bank'], 'first_number': '2001'}, reason='Print')
    cancelled = books['run']('check print-cancel', {'run': run['id']}, reason='Wrong paper')
    assert cancelled['status'] == 'cancelled' and cancelled['spoiled_numbers'] == []
    assert _queue(books)['next_check_number'] == '2001'
    again = books['run']('check print', {'account': books['bank'], 'first_number': '2001'}, reason='Print')
    assert again['items'][0]['check_number'] == '2001'


# ---------------------------------------------------------------- reprints


def test_reprint_one_check_with_the_same_number_and_with_a_new_one(books):
    a = _check(books, '50.00')
    run = books['run']('check print', {'account': books['bank'], 'first_number': '3001'}, reason='Print')
    books['run']('check print-confirm', {'run': run['id']}, reason='OK')

    same = books['run']('check reprint', {'check': a['id']}, reason='Customer lost it before mailing')
    assert same['kind'] == 'reprint' and same['items'][0]['check_number'] == '3001' and same['spoiled_numbers'] == []
    assert books['run']('check show', {'check': a['id']})['document']['check_number'] == '3001'

    fresh = books['run']('check reprint', {'check': a['id'], 'new_number': '3002'}, reason='Printer jammed')
    assert fresh['items'][0]['check_number'] == '3002' and fresh['spoiled_numbers'] == ['3001']
    assert books['run']('check show', {'check': a['id']})['document']['check_number'] == '3002'
    assert books['run']('check show', {'check': '3002'})['id'] == a['id']
    report = books['run']('report missing-checks', {'as_of': AS_OF})
    assert [(row['kind'], row['spoiled_number'], row['spoiled_reason']) for row in report['rows']] == [
        ('spoiled', 3001, 'reprinted')]
    # 3001 is spoiled: the allocator never hands it out again.
    assert _check(books, '1.00', to_be_printed=False)['document']['check_number'] == '3003'

    # A closed period refuses a new number for a check dated in it; the same number is still fine.
    books['run']('company update', {'closing_date': '2026-03-31'}, reason='Close March')
    with pytest.raises(BookflowError) as closed:
        books['run']('check reprint', {'check': a['id'], 'new_number': '3010'}, reason='Again')
    assert closed.value.code == 'E_PERIOD_CLOSED'
    books['run']('check reprint', {'check': a['id']}, reason='Copy for the file')


def test_printing_a_closed_period_check_is_refused(books):
    a = _check(books, '20.00')
    books['run']('company update', {'closing_date': '2026-03-31'}, reason='Close March')
    with pytest.raises(BookflowError) as closed:
        books['run']('check print', {'account': books['bank'], 'first_number': '100'}, reason='Print')
    # A bare run leaves it waiting and, with nothing else to print, says so.
    assert closed.value.code == 'E_PERIOD_CLOSED' and closed.value.details['skipped'] == [a['id']]


def test_a_printed_check_keeps_its_amount_and_payee_but_its_memo_can_change(books):
    a = _check(books, '75.00')
    run = books['run']('check print', {'account': books['bank'], 'first_number': '4001'}, reason='Print')
    books['run']('check print-confirm', {'run': run['id']}, reason='OK')
    with pytest.raises(BookflowError) as amount:
        books['run']('check update', {'check': a['id'], 'amount': '80.00',
                                      'expenses': [{'account': books['expense'], 'amount': '80.00'}]}, reason='More')
    assert amount.value.code == 'E_VALIDATION' and amount.value.details['printed'] is True
    other = books['client'].vendor.create(name='Corner Hardware', company=books['company'])['id']
    with pytest.raises(BookflowError) as payee:
        books['run']('check update', {'check': a['id'], 'pay_to': {'name_type': 'vendor', 'name_id': other}}, reason='Who')
    assert payee.value.code == 'E_VALIDATION'
    corrected = books['run']('check update', {'check': a['id'], 'memo': 'Paper for March'}, reason='Memo')
    assert corrected['document']['check_number'] == '4001'
    # Voiding is how a printed check is changed: the number stays used.
    books['run']('check void', {'check': a['id']}, reason='Wrong amount; rewriting')


def test_a_numbered_check_cannot_be_queued_again_and_false_numbers_a_queued_one(books):
    queued = _check(books, '5.00')
    numbered = books['run']('check update', {'check': queued['id'], 'to_be_printed': False}, reason='Hand-write it')
    assert numbered['document']['check_number'] == '1'
    assert _queue(books)['checks'] == []
    with pytest.raises(BookflowError) as again:
        books['run']('check update', {'check': queued['id'], 'to_be_printed': True}, reason='Queue')
    assert again.value.code == 'E_VALIDATION'
    with pytest.raises(BookflowError):
        _check(books, '5.00', number='77')


# ---------------------------------------------------------------- what goes on paper


@pytest.mark.parametrize('units, words', [
    (5, 'Zero and 05/100'),
    (100_000_000, 'One million and 00/100'),
    (123_456, 'One thousand two hundred thirty-four and 56/100'),
    (100, 'One and 00/100'),
    (1_100_011, 'Eleven thousand and 11/100'),
    (987_654_321_099, 'Nine billion eight hundred seventy-six million five hundred forty-three thousand two hundred ten and 99/100'),
])
def test_amount_in_words(units, words):
    assert check_pdf.amount_in_words(units, 'USD') == words


def test_voucher_stubs_list_the_bills_paid(books):
    bill, payment = _bill_payment(books, '300.00')
    run = books['run']('check print', {'account': books['bank'], 'first_number': '5001', 'style': 'voucher'}, reason='Print')
    reader = _pdf(books['run']('check print-pdf', {'run': run['id']}))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    assert text.count('INV-77') == 2  # both stubs
    assert text.count('300.00') >= 3 and 'Three hundred and 00/100' in text
    assert 'No. 5001' in text and 'Northside Supply' in text and '1 Main St' in text and 'Springfield, IL 62701' in text


def test_alignment_offsets_move_every_field_in_the_pdf(books):
    _check(books, '12.34')
    books['run']('check print-setup', {'account': books['bank'], 'style': 'standard'}, reason='Printer setup')
    run = books['run']('check print', {'account': books['bank'], 'first_number': '6001'}, reason='Print')
    plain = _placed(_pdf(books['run']('check print-pdf', {'run': run['id']})))
    books['run']('check print-cancel', {'run': run['id']}, reason='Realign')
    books['run']('check print-setup', {'account': books['bank'], 'offset_x_hundredths': 10,
                                       'offset_y_hundredths': -5}, reason='Fine alignment')
    run = books['run']('check print', {'account': books['bank'], 'first_number': '6001'}, reason='Print')
    assert (run['offset_x_hundredths'], run['offset_y_hundredths'], run['style']) == (10, -5, 'standard')
    moved = _placed(_pdf(books['run']('check print-pdf', {'run': run['id']})))
    expected = check_pdf.positions('standard', 0)
    figures = next(p for p in plain if p[0] == '**12.34')
    assert figures[1:] == expected['amount']
    shifted = next(p for p in moved if p[0] == '**12.34')
    # 10 hundredths right is 7.2 points; 5 hundredths down is 3.6 points.
    assert shifted[1:] == (round(expected['amount'][0] + 7.2, 2), round(expected['amount'][1] - 3.6, 2))
    sample = _placed(_pdf(books['run']('check print-alignment', {'account': books['bank'], 'offset_x_hundredths': 0})))
    assert next(p for p in sample if p[0] == '**1,234.56')[1:] == (expected['amount'][0], round(expected['amount'][1] - 3.6, 2))


def test_a_partial_first_sheet_takes_the_first_slots_and_later_pages_are_full(books):
    for amount in ('1.00', '2.00', '3.00', '4.00'):
        _check(books, amount)
    run = books['run']('check print', {'account': books['bank'], 'first_number': '7001', 'style': 'standard',
                                       'checks_on_first_page': 1}, reason='Leftover sheet')
    out = books['run']('check print-pdf', {'run': run['id']})
    assert out['pages'] == 2 and out['checks'] == 4
    reader = _pdf(out)
    first = [p for p in _placed(reader, 0) if p[0].startswith('**')]
    second = [p for p in _placed(reader, 1) if p[0].startswith('**')]
    assert [p[0] for p in first] == ['**1.00'] and [p[0] for p in second] == ['**2.00', '**3.00', '**4.00']
    assert [p[1:] for p in second] == [check_pdf.positions('standard', slot)['amount'] for slot in range(3)]
    with pytest.raises(BookflowError):
        books['run']('check print', {'account': books['bank'], 'style': 'voucher', 'checks_on_first_page': 2},
                     reason='No partial voucher')


def test_layout_arranges_partial_sheets():
    checks = [object()] * 5
    placed = check_pdf.arrange(checks, 'wallet', 2).placed
    assert [(p.page, p.slot) for p in placed] == [(0, 0), (0, 1), (1, 0), (1, 1), (1, 2)]
    assert [(p.page, p.slot) for p in check_pdf.arrange(checks, 'voucher').placed] == [(i, 0) for i in range(5)]


# ---------------------------------------------------------------- review fixes (R152 review)


def _print_all(books, first):
    run = books['run']('check print', {'account': books['bank'], 'first_number': first}, reason='Print')
    books['run']('check print-confirm', {'run': run['id']}, reason='OK')
    return run


def test_statement_import_matches_a_reprinted_check_on_its_new_number_only(books):
    a = _check(books, '50.00')
    _print_all(books, '1001')
    books['run']('check reprint', {'check': a['id'], 'new_number': '1020'}, reason='Jammed')

    def imported(number):
        csv = f'Date,Description,Amount,Check\n2026-03-10,CHK,-50.00,{number}\n'
        out = books['run']('reconcile import', dict(account=books['bank'], content=csv), reason='Statement')
        return out['lines'][0]
    new, old = imported('1020'), imported('1001')
    assert (new['status'], new['reason'], new['movement']['transaction_id']) == (
        'matched', 'same amount and check number', a['id'])
    assert old['status'] == 'unmatched' and old['movement'] is None


def test_bill_payment_show_and_query_carry_the_printed_number(books):
    _, payment = _bill_payment(books)
    _print_all(books, '1001')
    assert books['run']('bill payment show', {'payment': payment['id']})['check_number'] == '1001'
    assert [(row['id'], row['check_number']) for row in books['run']('bill payment query', {})['items']] == [
        (payment['id'], '1001')]
    assert [row['id'] for row in books['run']('bill payment query', {'check_number': '1001'})['items']] == [payment['id']]
    books['run']('check reprint', {'check': payment['id'], 'new_number': '1005'}, reason='Jammed')
    assert books['run']('bill payment query', {})['items'][0]['check_number'] == '1005'
    assert books['run']('bill payment show', {'payment': payment['id']})['check_number'] == '1005'
    assert [row['id'] for row in books['run']('bill payment query', {'check_number': '1005'})['items']] == [payment['id']]
    assert books['run']('bill payment query', {'check_number': '1001'})['items'] == []


def test_the_voucher_stub_pays_the_bill_less_its_discount_and_adds_up_to_the_face(books):
    books['run']('account create', {'name': 'Discounts Taken', 'type': 'income'}, reason='Discount account')
    bill = books['run']('bill post', dict(vendor=books['vendor'], date='2026-03-01', number='D-1',
                                          expenses=[{'account': books['expense'], 'amount': '1000.00'}]), reason='Bill')
    books['run']('bill pay', dict(date='2026-03-05', bills=[{'bill': bill['id'], 'amount': '980.00', 'discount': '20.00'}],
                                  funding_account=books['bank'], method=books['methods']['check'], to_be_printed=True),
                 reason='Pay with discount')
    big = _check(books, '1234.56')
    run = books['run']('check print', {'account': books['bank'], 'first_number': '1001', 'style': 'voucher'}, reason='Print')
    reader = _pdf(books['run']('check print-pdf', {'run': run['id']}))
    texts = [page.extract_text() for page in reader.pages]
    stub = next(t for t in texts if 'D-1' in t)
    assert '980.00' in stub and '20.00' in stub and '1,000.00' in stub and 'Total 980.00' in stub
    assert not any(line.strip().endswith('1,000.00') and line.count('1,000.00') == 2 for line in stub.splitlines())
    assert 'Total 1,234.56' in next(t for t in texts if 'One thousand two hundred thirty-four' in t)
    assert big['id']


def test_a_check_without_a_payee_is_refused_at_print_by_name(books):
    nameless = books['run']('check post', dict(account=books['bank'], date='2026-03-04', amount='15.00', to_be_printed=True,
                                               expenses=[{'account': books['expense'], 'amount': '15.00'}]), reason='No payee')
    named = _check(books, '20.00')
    with pytest.raises(BookflowError) as refused:
        books['run']('check print', {'account': books['bank'], 'first_number': '1001'}, reason='Print')
    assert refused.value.code == 'E_VALIDATION' and refused.value.details['transaction_id'] == nameless['id']
    assert 'pay_to' in refused.value.details['fields'][0]['problem']
    run = books['run']('check print', {'account': books['bank'], 'first_number': '1001', 'checks': [named['id']]},
                       reason='Print the named one')
    assert [i['transaction_id'] for i in run['items']] == [named['id']]


def test_a_printed_check_cannot_be_renumbered_by_update(books):
    a = _check(books, '30.00')
    _print_all(books, '1001')
    with pytest.raises(BookflowError) as refused:
        books['run']('check update', {'check': a['id'], 'number': '1009'}, reason='Renumber')
    assert refused.value.code == 'E_VALIDATION' and 'check reprint --new-number 1009' in str(refused.value.details)
    assert books['run']('check show', {'check': a['id']})['document']['check_number'] == '1001'


def test_a_bare_run_skips_closed_period_checks_and_says_why(books):
    old = _check(books, '2.00')
    new = _check(books, '3.00', date='2026-05-01')
    run = books['run']('check print', {'account': books['bank'], 'first_number': '1001'}, reason='Print')
    books['run']('company update', {'closing_date': '2026-03-31'}, reason='Close March')
    books['run']('check print-confirm', {'run': run['id'], 'failed': ['1001', '1002']}, reason='Both jammed')
    bare = books['run']('check print', {'account': books['bank'], 'first_number': '1003'}, reason='Print again')
    assert [i['transaction_id'] for i in bare['items']] == [new['id']]
    assert [(row['transaction_id'], 'closing date' in row['reason']) for row in bare['skipped']] == [(old['id'], True)]
    books['run']('check print-cancel', {'run': bare['id']}, reason='Not now')
    with pytest.raises(BookflowError) as named:
        books['run']('check print', {'account': books['bank'], 'first_number': '1003', 'checks': [old['id']]}, reason='P')
    assert named.value.code == 'E_PERIOD_CLOSED'


def test_a_payment_in_an_unanswered_run_cannot_be_unapplied_until_answered(books):
    bill, payment = _bill_payment(books)
    run = books['run']('check print', {'account': books['bank'], 'first_number': '1001'}, reason='Print')
    with pytest.raises(BookflowError) as refused:
        books['run']('bill payment unapply', {'payment': payment['id']}, reason='Undo')
    assert refused.value.code == 'E_VALIDATION' and refused.value.details['run'] == run['id']
    with pytest.raises(BookflowError) as held:
        books['run']('bill void', {'bill': bill['id']}, reason='Wrong bill')
    assert held.value.code == 'E_HAS_APPLICATIONS'
    books['run']('check print-confirm', {'run': run['id']}, reason='OK')
    books['run']('bill payment unapply', {'payment': payment['id']}, reason='Printed: allowed as before')
