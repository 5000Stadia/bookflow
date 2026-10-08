"""R167: a bank statement file comes in for reconciliation.

`reconcile import` reads OFX, QFX and CSV statements against the demo's Checking account. The
sample files under tests/fixtures/statements/ are one statement (2026-01-01 to 2026-09-30,
ending balance 6,236.95) written three ways. Against the demo books, eight lines match an entry,
one deposit the bank posted nine days late is only suggested, a 12.00 service fee is not in the
books, and the OFX and QFX repeat one FITID.

The end-to-end case is the bookkeeper's month: import with `start`, enter the missing fee,
import again, settle the suggestion, finish -- and hold a certificate whose difference is zero.
"""
import anyio
from pathlib import Path

import pytest

from bookflow.company import statement_files as files
from bookflow.core.ids import new_id
from tests.test_service_sales_lifecycle import COMPANY

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('reconcile import',))
SAMPLES = Path(__file__).parent / 'fixtures' / 'statements'
EXPECTED = dict(matched=8, suggested=1, unmatched=1, reconciled=0)


def sample(name):
    return (SAMPLES / name).read_text()


def run(client, name, data, **ctx):
    from bookflow.core import registry
    if registry.get(name).is_write:
        ctx.setdefault('reason', 'R167 statement import')
    return client.run(name, data, company=COMPANY, **ctx)


def opening(client):
    """Checking has never been reconciled: adopt it at zero before its first movement."""
    return run(client, 'reconcile opening start', dict(
        operation_key=new_id(), account='Checking', opening_date='2025-12-31', entered_balance='0.00',
        evidence=dict(format=1, statement_reference=None, entered_text='First statement'),
        references=[]))


def by_status(document):
    found = {}
    for line in document['lines']:
        found.setdefault(line['status'], []).append(line)
    return found


@pytest.mark.parametrize('name,fmt,duplicates', [
    ('checking-2026-09.ofx', 'ofx', 1), ('checking-2026-09.qfx', 'qfx', 1), ('checking-2026-09.csv', 'csv', 0)])
def test_each_format_imports_and_reports_every_line(client, name, fmt, duplicates):
    document = run(client, 'reconcile import', dict(account='Checking', content=sample(name)))
    assert document['format'] == fmt
    assert (document['statement_date'], document['ending_balance']) == ('2026-09-30', 623695)
    counts = document['counts']
    assert {k: counts[k] for k in EXPECTED} == EXPECTED
    assert counts['duplicate'] == duplicates and counts['lines'] == 10 + duplicates
    # Review only: no draft, nothing ticked, nothing posted.
    assert document['draft'] is None and counts['newly_marked'] == 0
    lines = by_status(document)
    assert [(v['date'], v['amount']) for v in lines['unmatched']] == [('2026-09-30', -1200)]
    assert 'enter it' in lines['unmatched'][0]['reason']
    suggestion, = lines['suggested']
    assert (suggestion['date'], suggestion['amount']) == ('2026-04-24', 20000)
    assert [(v['date'], v['amount']) for v in suggestion['suggestions']] == [('2026-04-15', 20000)]
    for line in lines['matched']:
        assert line['movement'] and line['group_fingerprint'] and not line['marked']
    if fmt != 'csv':
        assert all(v['line_id'].startswith('fitid:') for v in document['lines'])
        assert lines['duplicate'][0]['fitid'] == '202602010001'


def test_ofx_sgml_and_xml_read_the_same_lines():
    sgml = files.parse(sample('checking-2026-09.ofx'), 'auto', 2)
    xml = files.parse(sample('checking-2026-09.qfx'), 'auto', 2)
    assert sgml.lines == xml.lines and sgml.duplicates == xml.duplicates
    assert (sgml.format, xml.format) == ('ofx', 'qfx')
    assert sgml.lines[7].amount == 1595 and sgml.lines[7].memo == 'Foreign receipt'
    assert sgml.currency == 'USD' and sgml.ending_balance == 623695


def test_csv_split_columns_mapping_and_exact_money():
    content = ('Date;Payee;Withdrawal;Deposit;Ref\n'
               '30/09/2026;Fee;12,00;;A1\n'      # comma decimal, day-first
               '01/10/2026;Client;;1.234,50;A2\n')
    with pytest.raises(Exception):
        files.parse(content, 'csv', 2, dict(date='Date', debit='Withdrawal', credit='Deposit'))
    parsed = files.parse('Date,Payee,Withdrawal,Deposit,Ref\n30/09/2026,Fee,12.00,,A1\n'
                         '01/10/2026,Client,,"1,234.50",A2\n', 'csv', 2,
                         dict(date='Date', debit='Withdrawal', credit='Deposit', fitid='Ref',
                              date_format='DD/MM/YYYY'))
    assert [(v.date, v.amount, v.line_id) for v in parsed.lines] == [
        ('2026-09-30', -1200, 'fitid:A1'), ('2026-10-01', 123450, 'fitid:A2')]
    with pytest.raises(Exception) as raised:
        files.parse('Date,Amount\n2026-09-30,1.005\n', 'csv', 2)
    assert 'E_AMOUNT_PRECISION' in str(raised.value)
    # Two identical lines are two lines, and read again they keep the same ids.
    twice = 'Date,Amount,Description\n2026-09-30,-4.50,Coffee\n2026-09-30,-4.50,Coffee\n'
    first, again = files.parse(twice, 'csv', 2), files.parse(twice, 'csv', 2)
    assert len(first.lines) == 2 and first.lines[0].line_id != first.lines[1].line_id
    assert [v.line_id for v in first.lines] == [v.line_id for v in again.lines]


def test_check_numbers_decide_and_ties_are_left_to_a_person():
    def candidate(name, day, number=''):
        return files.Candidate(ref=name, date=day, amount=-5000, number=number)
    books = [candidate('check 101', '2026-09-01', '101'), candidate('check 102', '2026-09-01', '102'),
             candidate('a', '2026-09-20'), candidate('b', '2026-09-20')]
    lines = [files.Line('l1', '2026-09-12', -5000, number='102'),  # cleared eleven days later
             files.Line('l2', '2026-09-21', -5000)]
    one, two = files.match(lines, books)
    assert (one.status, one.match.ref) == ('matched', 'check 102')
    # Two entries of 50.00 one day away: neither is chosen for the person.
    assert two.status == 'suggested' and {v.ref for v in two.suggestions} >= {'a', 'b'}
    # A check number that disagrees never matches, however close the date.
    wrong, = files.match([files.Line('l3', '2026-09-01', -5000, number='999')], books[:2])
    assert wrong.status == 'unmatched'


def test_import_mark_finish_reaches_a_certificate_with_no_difference(client):
    content = sample('checking-2026-09.ofx')
    opening(client)
    preview = run(client, 'reconcile import', dict(account='Checking', content=content, start=True),
                  dry_run=True)
    assert preview['draft_started'] and preview['counts']['newly_marked'] == 8
    assert run(client, 'reconcile import', dict(account='Checking', content=content))['draft'] is None, \
        'a dry run writes nothing'

    first = run(client, 'reconcile import', dict(account='Checking', content=content, start=True))
    draft = first['draft']
    assert first['draft_started'] and first['counts']['newly_marked'] == 8
    assert draft['header']['statement_date'] == '2026-09-30' and draft['header']['entered_balance'] == 623695
    # Eight movements; the demo's sales receipt is one movement of two components.
    assert len(draft['selections']) == 9

    # Again, unchanged: the same draft, nothing new ticked, nothing duplicated.
    again = run(client, 'reconcile import', dict(account='Checking', content=content, start=True))
    assert not again['draft_started'] and again['draft']['id'] == draft['id']
    assert again['draft']['version'] == draft['version'] and again['counts']['newly_marked'] == 0
    assert sum(v['already_marked'] for v in again['lines']) == 8

    # The fee the bank charged goes into the books; the next import finds and ticks it.
    run(client, 'journal post', dict(date='2026-09-30', memo='Monthly service fee', lines=[
        dict(account='Bank Fees', side='debit', amount='12.00'),
        dict(account='Checking', side='credit', amount='12.00')]))
    third = run(client, 'reconcile import', dict(account='Checking', content=content, draft=draft['id']))
    assert third['counts']['unmatched'] == 0 and third['counts']['newly_marked'] == 1
    fee, = [v for v in third['lines'] if v['amount'] == -1200]
    assert fee['status'] == 'matched' and fee['marked']

    # The late deposit was only suggested: the person ticks it.
    late, = [v for v in third['lines'] if v['status'] == 'suggested']
    choice = late['suggestions'][0]
    marked = run(client, 'reconcile mark', dict(
        operation_key=new_id(), draft=draft['id'], expected_version=third['draft']['version'],
        entries=[dict(movement=choice['movement'], group_fingerprint=choice['group_fingerprint'],
                      action='mark')]))
    version = marked['draft']['version']
    guards = run(client, 'reconcile preview', dict(draft=draft['id'], expected_version=version))
    assert guards['balanced'] and guards['totals']['difference'] == 0
    done = run(client, 'reconcile finish', dict(
        operation_key=new_id(), draft=draft['id'], expected_version=version,
        expected_facts_fingerprint=guards['expected_facts_fingerprint'],
        dependency_guard=guards['dependency_guard']))
    assert done['certificate_id'] and done['totals']['difference'] == 0
    assert done['totals']['ending_balance'] == done['totals']['cleared_balance'] == 623695

    # Imported once more after the statement is certified, nothing reads as new: every line that
    # matched reads as reconciled, and the late deposit is still only a suggestion, saying so.
    after = run(client, 'reconcile import', dict(account='Checking', content=content))
    assert after['counts']['reconciled'] == 9 and after['counts']['unmatched'] == 0
    late, = [v for v in after['lines'] if v['status'] == 'suggested']
    assert 'already cleared' in late['reason']


def test_refusals_are_named(client):
    from bookflow import BookflowError
    with pytest.raises(BookflowError) as raised:
        run(client, 'reconcile import', dict(account='Checking', content=sample('checking-2026-09.csv'),
                                             start=True))
    # No opening yet: the refusal says the first step, as `reconcile start` does.
    assert 'reconcile opening start' in str(raised.value.to_dict())
    with pytest.raises(BookflowError) as raised:
        run(client, 'reconcile import', dict(account='Checking', content='<OFX><BANKMSGSRSV1>'
                                             '<STMTTRN><TRNAMT>1.00</STMTTRN></BANKMSGSRSV1></OFX>'))
    assert raised.value.to_dict()['code'] == 'E_VALIDATION'


@pytest.mark.timeout(900)
def test_the_same_import_through_python_cli_http_and_mcp(root, tmp_path):
    """One statement imported with `start` on four copies of the demo, one per surface."""
    from tests.mcp_matrix_support import Matrix
    import bookflow
    content = sample('checking-2026-09.csv')
    # The opening is set up once, before the four copies are made; the import is what is compared.
    opening(bookflow.connect(data_root=str(root)))

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                results[surface] = await matrix.call(surface, 'reconcile import', dict(
                    account='Checking', content=content, start=True))
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)

    def comparable(document):
        draft = document['draft']
        return dict(counts=document['counts'], ending=document['ending_balance'],
                    statuses=[(v['line_id'], v['status'], v['marked']) for v in document['lines']],
                    selections=len(draft['selections']), version=draft['version'])
    python = comparable(results['python'])
    assert python['counts']['newly_marked'] == 8 and python['selections'] == 9
    for surface in ('cli', 'http', 'mcp'):
        assert comparable(results[surface]) == python, surface


def test_a_ticked_entry_no_statement_line_shows_is_flagged(client):
    """Truth, not only consistency: an entry ticked to make a statement tie is named, not hidden."""
    content = sample('checking-2026-09.ofx')
    opening(client)
    first = run(client, 'reconcile import', dict(account='Checking', content=content, start=True))
    draft = first['draft']
    assert first['counts']['cleared_without_line'] == 0 and first['cleared_without_line'] == []

    # The person settles the late deposit's suggestion; read again, that line is matched to it.
    late, = [v for v in first['lines'] if v['status'] == 'suggested']
    choice = late['suggestions'][0]
    version = run(client, 'reconcile mark', dict(
        operation_key=new_id(), draft=draft['id'], expected_version=draft['version'],
        entries=[dict(movement=choice['movement'], group_fingerprint=choice['group_fingerprint'],
                      action='mark')]))['draft']['version']

    # An entry the bank never showed, posted and ticked.
    run(client, 'journal post', dict(date='2026-09-15', memo='Invented deposit', lines=[
        dict(account='Checking', side='debit', amount='25.00'),
        dict(account='Bank Fees', side='credit', amount='25.00')]))
    page = run(client, 'reconcile candidates', dict(draft=draft['id'], limit=50,
                                                    filters=dict(amount=2500)))
    invented, = page['items']
    run(client, 'reconcile mark', dict(
        operation_key=new_id(), draft=draft['id'], expected_version=version,
        entries=[dict(movement=invented['movement'], group_fingerprint=invented['group_fingerprint'],
                      action='mark')]))

    again = run(client, 'reconcile import', dict(account='Checking', content=content, draft=draft['id']))
    assert again['counts']['suggested'] == 0
    assert [v['reason'] for v in again['lines'] if v['date'] == '2026-04-24'] == [
        'same amount, 9 days apart; ticked by a person']
    flagged, = again['cleared_without_line']
    assert (flagged['date'], flagged['amount']) == ('2026-09-15', 2500)
    assert again['counts']['cleared_without_line'] == 1
    assert 'cleared without a statement line' in again['next_step']
    # Flagged, not refused: the import wrote nothing and the draft is as the person left it.
    assert again['counts']['newly_marked'] == 0
