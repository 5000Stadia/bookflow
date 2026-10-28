"""Kabe's import rules (2026-10-27), retrofitted onto list imports, and reading .xlsx (design/bulk-import.md §2, §3).

- `import check` reads the file once and reports every problem at once, grouped by column, with row numbers and the
  exact cell text, running no command; `preview` and `run` start with it.
- All or nothing by default; `partial` is the opt-in.
- Text keeps its words: a character no text field holds is dropped and reported by row, column and position; what a
  field can hold (accents, scripts, symbols, emoji) is kept. A NUL in a cell no longer refuses the file; a file that is
  not text still does.
- Junk in money, numbers, dates, choices and references refuses the run, never repaired.
- Empty is allowed unless truly needed: the import's required fields are exactly the single command's.
"""
import io

import pytest

from bookflow import BookflowError
from bookflow.company import import_kinds, tabular
from bookflow.core import registry
from tests.test_imports import active_customers, books, company_db, run_all  # noqa: F401


def refusal(fn):
    with pytest.raises(BookflowError) as caught:
        fn()
    return caught.value


def counts_of(books):
    with company_db(books) as db:
        return {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0]
                for t in ('customers', 'import_runs', 'import_rows', 'batches', 'audit_events')}


def test_the_check_lists_every_problem_at_once_and_a_refused_preview_changes_nothing(books):
    text = ('Customer,Credit Limit,Terms,Job Start,Phone\n'
            'Ann,10.00,Net 30,,1\n'
            'Ben,$599.♎56,Net 30,2026-01-06,2\n'
            'Cy,12x,Net 99,2026-13-45,3\n'
            'Di,$599.♎56,Net 30,,' + '9' * 80 + '\n'
            ',5,Net 30,,\n'
            'Ann,1,Net 30,,\n')
    before = counts_of(books)
    out = books.run('import check', {'kind': 'customer', 'content': text})
    assert out['ok'] is False and out['rows'] == 6 and out['rows_with_problems'] == 5
    groups = {(g['column'], g['code'], g['value']): g['rows'] for g in out['problems']}
    assert groups == {('Customer', 'no_key', None): [5], ('Customer', 'duplicate_in_file', None): [6],
                      ('Credit Limit', 'bad_value', '$599.♎56'): [2, 4], ('Credit Limit', 'bad_value', '12x'): [3],
                      ('Job Start', 'bad_value', '2026-13-45'): [3], ('Phone', 'E_VALIDATION', '9' * 80): [4]}
    assert "Credit Limit, rows 2, 4: not an amount: '$599.♎56'" in out['summary']
    # Columns in file order; references are looked up and the unknown one asks, once per value.
    assert [g['column'] for g in out['problems']][:3] == ['Customer', 'Customer', 'Credit Limit']
    assert [(u['value'], u['rows']) for u in out['unknown_references']] == [('Net 99', [3])]  # on a refused row too
    assert counts_of(books) == before  # a check changes nothing
    error = refusal(lambda: books.run('import preview', {'kind': 'customer', 'content': text}))
    assert error.code == 'E_VALIDATION' and error.details['check']['problems_total'] == 7
    assert "not an amount: '$599.♎56'" in error.message
    assert counts_of(books) == before  # a refused preview stores no run
    partial = books.run('import preview', {'kind': 'customer', 'content': text, 'partial': True})
    assert partial['counts'] == {'create': 1, 'refused': 5}  # the Net 99 row is refused for its date first


def test_an_incomplete_mapping_is_refused_even_with_partial(books):
    out = books.run('import check', {'kind': 'term', 'content': 'Terms,Net due in days\nNet 45,45\n'})
    assert [(g['code'], g['rows']) for g in out['problems']] == [('unmapped_required', [])]
    assert 'kind' in out['problems'][0]['message']
    error = refusal(lambda: books.run('import preview', {'kind': 'term', 'content': 'Terms\nNet 45\n',
                                                         'partial': True}))
    assert error.code == 'E_VALIDATION' and 'No column maps to kind' in error.message


def test_all_or_nothing_is_the_default_and_partial_the_opt_in(books):
    text = 'Name,Credit Limit\nAlpha,10\nBravo,20\n'
    books.run('customer create', {'name': 'Bravo', 'phone': '1'})
    preview = books.run('import preview', {'kind': 'customer', 'content': text, 'mode': 'upsert',
                                           'constants': {'phone': '9' * 70}})
    assert preview['run']['atomic'] is True and preview['counts'] == {'refused': 2}
    error = refusal(lambda: books.run('import run', {'run': preview['run']['id']}))
    assert error.code == 'E_VALIDATION' and 'refused no rows' in error.message
    assert active_customers(books) == {'Bravo'}
    ok = books.run('import preview', {'kind': 'customer', 'content': text, 'source': 'other'})
    assert ok['run']['atomic'] is True
    assert run_all(books, ok['run']['id'])['counts'] == {'created': 1, 'differs': 1, 'not_run': 0}


def test_text_drops_only_what_it_cannot_hold_and_says_where(books):
    text = ('Name,Notes,Company\n'
            '"Johnson, Beck \x00♎️","line one\x07\nline two\tend",Café 東京\x1b\n'
            'Plain,,\n')
    out = books.run('import check', {'kind': 'customer', 'content': text})
    assert out['ok'] is True and out['dropped_total'] == 3
    assert [(d['row'], d['column'], d['position'], d['name']) for d in out['dropped']] == [
        (1, 'Name', 15, 'NUL'), (1, 'Notes', 9, 'BEL'), (1, 'Company', 8, 'ESCAPE')]
    preview = books.run('import preview', {'kind': 'customer', 'content': text})
    assert preview['dropped_total'] == 3 and 'dropped' in ' '.join(preview['notes'])
    run_all(books, preview['run']['id'])
    johnson = books.run('customer show', {'customer': 'Johnson, Beck ♎️'})
    assert johnson['notes'] == 'line one\nline two\tend'  # long text keeps tabs and line breaks
    assert johnson['company_name'] == 'Café 東京'  # accents and other scripts kept
    shown = books.run('import show', {'run': preview['run']['id']})
    assert shown['dropped_total'] == 3 and shown['dropped'][0]['character'] == 'U+0000'
    # A tab is not held by a name: dropped and reported; a NUL in a reference is junk.
    tab = books.run('import check', {'kind': 'customer', 'content': 'Name,Terms\n"Tab\tCo",Net\x0030\n'})
    assert [(d['column'], d['name']) for d in tab['dropped']] == [('Name', 'TAB')]
    assert [(g['column'], g['code']) for g in tab['problems']] == [('Terms', 'bad_value')]


def test_a_file_that_is_not_text_is_refused_and_a_nul_in_a_cell_is_not():
    table = tabular.read(b'Name\nA\x00B\n')
    assert table.rows[0][1] == ['A\x00B']
    for raw in (bytes(range(256)) * 20, b'\x00\x01\x02\x03' * 400, b'%PDF-1.7 ...', b'\xd0\xcf\x11\xe0rest'):
        with pytest.raises(BookflowError) as caught:
            tabular.read(raw)
        assert caught.value.details['problem'] in ('not_text', 'encoding')


def test_blank_cells_and_absent_columns_leave_fields_empty(books):
    text = 'Name,Email,Phone,Credit Limit,Terms\nOnly Name,,,,\nSome,s@x.test,,,Net 30\n'
    assert books.run('import check', {'kind': 'customer', 'content': text})['ok'] is True
    run_all(books, books.run('import preview', {'kind': 'customer', 'content': text})['run']['id'])
    typed = books.run('customer create', {'name': 'Typed'})
    imported = books.run('customer show', {'customer': 'Only Name'})
    volatile = {'id', 'name', 'full_name', 'created_at', 'updated_at', 'created_by', 'updated_by', 'created_via',
                'updated_via', 'version', 'path'}
    same = lambda shown: {k: v for k, v in shown.items() if k not in volatile and not k.endswith('_source_id')}
    assert same(imported) == same(books.run('customer show', {'customer': typed['id']}))
    # An item imports with its name, type and income account (a service item's own rule), the price left blank.
    items = books.run('import preview', {'kind': 'item', 'content': 'Item,Type,Sales Price,Income Account\n'
                                                                  'Bare,service,,Service Income\n'})
    assert items['counts'] == {'create': 1}, items['refused']


@pytest.mark.parametrize('kind', sorted(import_kinds.KINDS))
def test_the_import_requires_exactly_what_the_command_requires(kind):
    model = registry.get(import_kinds.kind(kind).create).input_model
    command = {name for name, info in model.model_fields.items() if info.is_required()}
    assert set(import_kinds.required(kind)) == command
    listed = {k['kind']: k for k in __import__('bookflow.company.imports', fromlist=['x']).kinds_list()['kinds']}
    assert set(listed[kind]['required']) == command


def test_a_reference_gone_since_the_preview_refuses_the_all_or_nothing_run(books):
    books.run('term create', {'name': 'Net 21', 'kind': 'standard', 'due_days': 21})
    preview = books.run('import preview', {'kind': 'customer', 'content': 'Name,Terms\nA,Net 21\nB,Net 30\n'})
    books.run('term update', {'term': 'Net 21', 'name': 'Net 22'})
    before = counts_of(books)
    error = refusal(lambda: books.run('import run', {'run': preview['run']['id']}))
    assert error.code == 'E_VALIDATION' and "'Net 21'" in error.message and error.details['check'][0]['rows'] == [1]
    assert counts_of(books) == before
    assert books.run('import show', {'run': preview['run']['id']})['run']['state'] == 'previewed'


# ------------------------------------------------------------------ .xlsx

def _workbook(rows, *, sheet='Customers', extra=None, formula_without_value=False):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for row in rows:
        ws.append(row)
    if extra:
        other = wb.create_sheet(extra)
        other.append(['Name'])
        other.append(['From the second sheet'])
    if formula_without_value:
        ws['B3'] = '=1+1'
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_an_xlsx_sheet_reads_values_never_formulas(tmp_path):
    from datetime import date
    raw = _workbook([['Customer', 'Credit Limit', 'Since', 'Phone', 'Active'],
                     ['Acme', 599.56, date(2026, 1, 31), '00123', True], ['Bolt', 1250, None, None, False]],
                    extra='Other')
    table = tabular.read(raw)
    assert table.headers == ['Customer', 'Credit Limit', 'Since', 'Phone', 'Active'] and table.sheet == 'Customers'
    assert [cells for _, cells in table.rows] == [['Acme', '599.56', '2026-01-31', '00123', 'true'],
                                                   ['Bolt', '1250', '', '', 'false']]
    assert [line for line, _ in table.rows] == [2, 3] and table.encoding == 'xlsx'
    assert tabular.read(raw, sheet='Other').rows[0][1] == ['From the second sheet']
    with pytest.raises(BookflowError) as caught:
        tabular.read(raw, sheet='Missing')
    assert caught.value.details['sheets'] == ['Customers', 'Other']
    with pytest.raises(BookflowError) as caught:
        tabular.read(_workbook([['Name', 'Amount'], ['A', 1]], formula_without_value=True))
    assert caught.value.details['problem'] == 'unsaved_formula' and caught.value.details['cells'] == ['B3']
    with pytest.raises(BookflowError) as caught:
        tabular.read(b'PK\x03\x04not really a zip')
    assert caught.value.details['problem'] == 'not_xlsx'


def test_an_xlsx_attachment_imports(books, tmp_path):
    import openpyxl.xml
    assert openpyxl.xml.DEFUSEDXML  # the hardened XML parser is what reads every workbook
    company = books.run('company show')
    added = books.run('attachment add', dict(
        record_type='company_info', record_id=company['company_id'], original_filename='customers.xlsx',
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
        input_stream=io.BytesIO(_workbook([['Customer', 'Credit Limit'], ['Sheet Co', 10.5], ['Second Co', 20]])))
    preview = books.run('import preview', {'kind': 'customer', 'attachment': added['attachment']['id']})
    assert preview['counts'] == {'create': 2} and preview['run']['encoding'] == 'xlsx'
    run_all(books, preview['run']['id'])
    assert books.run('customer show', {'customer': 'Sheet Co'})['credit_limit']['amount'] == '10.50'
