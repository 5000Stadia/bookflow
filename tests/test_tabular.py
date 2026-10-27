"""The shared reader of import files (R107 increment 1, design/bulk-import.md §3): every fixture of §3, and its
agreement with the two older readers (`statement_files`, `cutover_sources`) on the money and dates all three read."""
import pytest

from bookflow import BookflowError
from bookflow.company import cutover_sources, statement_files, tabular


def test_text_encodings_and_byte_order_marks():
    utf8 = 'Name,City\nCafé Ñu,Zürich\n'
    for raw, encoding, guessed in ((utf8.encode('utf-8'), 'utf-8', False),
                                   (b'\xef\xbb\xbf' + utf8.encode('utf-8'), 'utf-8-sig', False),
                                   (utf8.encode('utf-16'), 'utf-16', False),
                                   (utf8.encode('cp1252'), 'cp1252', True)):
        table = tabular.read(raw)
        assert (table.encoding, table.encoding_guessed) == (encoding, guessed)
        assert table.headers == ['Name', 'City'] and table.rows == [(2, ['Café Ñu', 'Zürich'])]
    # A byte that decodes in neither refuses the file; nothing becomes '?'.
    with pytest.raises(BookflowError) as caught:
        tabular.read(b'Name\nA\x81B\n')
    assert caught.value.code == 'E_VALIDATION' and caught.value.details['problem'] == 'encoding'


def test_delimiters_quotes_blank_rows_and_widths():
    assert tabular.read('a;b;c\n1;2;3\n').delimiter == ';'
    pasted = tabular.read('Name\tPhone\nAcme\t0123\n')  # what a spreadsheet copies
    assert pasted.delimiter == '\t' and pasted.rows[0][1] == ['Acme', '0123']  # leading zero kept
    assert tabular.read('a|b\n1|2\n').delimiter == '|'
    quoted = tabular.read('Name,Notes\r\n"Acme, Inc.","line one\nline ""two"""\r\n\r\n,\r\nBeta,x\r\n')
    assert quoted.rows == [(2, ['Acme, Inc.', 'line one\nline "two"']), (6, ['Beta', 'x'])]  # the quoted newline spans line 3
    ragged = tabular.read('a,b\n1,2,3\n4,5\n')
    assert ragged.width_errors == {1: 3}
    headless = tabular.read('Acme,1\nBeta,2\n', header_row=False)
    assert headless.headers == ['Column A', 'Column B'] and len(headless.rows) == 2
    titled = tabular.read('Customer list\nName\nAcme\n', skip_rows=1)
    assert titled.headers == ['Name'] and titled.rows == [(3, ['Acme'])]


def test_money_forms_and_precision():
    read = tabular.parse_money
    assert read('1,234.56') == 123456 and read('(1,234.56)') == -123456 and read('1234.56-') == -123456
    assert read('$ 12') == 1200 and read('-$5.10') == -510 and read('') is None
    assert read('1.234,56', decimal=',', thousands='.') == 123456
    assert read('1 234,5', decimal=',', thousands=' ') == 123450
    assert read('1.50000', 2) == 150  # zeros past the currency's places are not precision
    with pytest.raises(tabular.CellError) as caught:
        read('1.005')
    assert caught.value.code == 'E_AMOUNT_PRECISION'  # never rounded
    with pytest.raises(tabular.CellError):
        read('12x')
    # The column decides the separators; 1.234 alone could be either, so it asks.
    assert tabular.number_format(['1.234,50', '7']) == (',', '.')
    assert tabular.number_format(['1,234.50']) == ('.', ',')
    assert tabular.number_format(['1.234']) is None
    assert tabular.number_format(['1.234', '2,5']) == (',', '.')


def test_dates_orders_pivots_and_serials():
    assert tabular.date_order(['01/02/2026', '13/02/2026']) == 'DMY'
    assert tabular.date_order(['01/02/2026', '02/13/2026']) == 'MDY'
    assert tabular.date_order(['01/02/2026', '03/04/2026']) is None  # all ambiguous: asked, never guessed
    with pytest.raises(tabular.CellError):
        tabular.date_order(['13/01/2026', '01/13/2026'])
    assert tabular.parse_date('31.12.2025', 'DMY') == '2025-12-31'
    assert tabular.parse_date('20260131', None) == '2026-01-31'
    assert tabular.parse_date('2026-01-31', None) == '2026-01-31'
    assert tabular.parse_date('1/5/26', 'MDY', current_year=2026) == '2026-01-05'
    assert tabular.parse_date('1/5/27', 'MDY', current_year=2026) == '1927-01-05'
    for bad, code in (('45000', 'bad_value'), ('02/30/2026', 'bad_value'), ('01/02/2026', 'ambiguous_date')):
        with pytest.raises(tabular.CellError) as caught:
            tabular.parse_date(bad, None if code == 'ambiguous_date' else 'MDY')
        assert caught.value.code == code


def test_booleans_and_formula_safety():
    assert [tabular.parse_bool(x) for x in ('Yes', 'n', '1', 'FALSE', '')] == [True, False, True, False, None]
    text = tabular.write_csv(['Name', 'Note'], [['=HYPERLINK("x")', '+1'], ['-2', '@SUM(A1)'], ['Acme', '0042']])
    assert text.splitlines() == ['Name,Note', '"\'=HYPERLINK(""x"")",\'+1', "'-2,'@SUM(A1)", 'Acme,0042']


@pytest.mark.parametrize('text', ['1,234.56', '(1,234.56)', '1234.56-', '$12', '-5.00', '0.10', '1000'])
def test_money_agrees_with_the_older_readers(text):
    ours = tabular.parse_money(text, 2)
    assert ours == cutover_sources.parse_amount(text, 2) == statement_files.amount(text, 2)


@pytest.mark.parametrize('text, order', [('2026-01-31', None), ('01/31/2026', 'MDY'), ('1/5/26', 'MDY'),
                                         ('12/31/2025', 'MDY')])
def test_dates_agree_with_the_older_readers(text, order):
    ours = tabular.parse_date(text, order, current_year=2026)
    assert ours == cutover_sources.parse_date(text) == statement_files.csv_date(text, 'auto')
