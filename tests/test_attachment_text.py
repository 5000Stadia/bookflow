"""Reading a small text attachment, and a long statement in pages (R170 blind July).

The blind agent kept Harbor Electric's July over MCP alone. It attached the Visa statement's summary box
(tests/fixtures/fakeco/handed-over/2026-07/visa-2026-07-summary.txt: closing date 07/31/2026, new balance
1,481.65) and had no way to read it: `reconcile import` refused it as neither CSV nor OFX, and nothing else
returned a text file's content without an output directory. `attachment text` returns it, and the import's
refusal of a text file that is not a statement now names that command. It also saw only 21 of 39 checking
statement lines, because the result was over the MCP size budget and the import took no limit; it now takes
limit and cursor and says when more lines follow.
"""
import io
from pathlib import Path

import anyio
import pytest

import bookflow
from bookflow import BookflowError
from tests.test_service_sales_lifecycle import COMPANY

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('attachment text',))
JULY = Path(__file__).parent / 'fixtures' / 'fakeco' / 'handed-over' / '2026-07'
SUMMARY = (JULY / 'visa-2026-07-summary.txt').read_bytes()
CHECKING = (JULY / 'checking-2026-07.ofx').read_bytes()


def _add(client, name, body, media_type='text/plain'):
    company_id = client.run('company show', {}, company=COMPANY)['company_id']
    return client.run('attachment add', dict(record_type='company_info', record_id=company_id, original_filename=name,
                                             media_type=media_type, caption=name),
                      company=COMPANY, reason='statement from the input folder', input_stream=io.BytesIO(body))['attachment']


def _refused(client, name, data):
    with pytest.raises(BookflowError) as raised:
        client.run(name, data, company=COMPANY)
    return raised.value.to_dict()


def test_a_text_attachment_reads_whole_and_anything_else_is_refused_by_name(client):
    summary = _add(client, 'visa-2026-07-summary.txt', SUMMARY)
    read = client.run('attachment text', dict(attachment=summary['id']), company=COMPANY)
    assert read['text'] == SUMMARY.decode('ascii') and read['encoding'] == 'utf-8'
    assert 'New balance' in read['text'] and '1,481.65' in read['text'] and read['line_count'] == 11
    assert (read['id'], read['original_filename'], read['size_bytes']) == (summary['id'], 'visa-2026-07-summary.txt', len(SUMMARY))

    # Windows-1252, as the desktop product writes its exports, reads as itself.
    legacy = _add(client, 'note.txt', 'Caf\xe9 \xb7 Checking\r\n'.encode('cp1252'))
    assert client.run('attachment text', dict(attachment=legacy['id']), company=COMPANY)['text'] == 'Caf\xe9 \xb7 Checking\r\n'
    # A CSV sent as octet-stream is still text by its name.
    csv = _add(client, 'visa-2026-07.csv', (JULY / 'visa-2026-07.csv').read_bytes(), 'application/octet-stream')
    assert client.run('attachment text', dict(attachment=csv['id']), company=COMPANY)['text'].startswith('Transaction Date,')

    picture = _add(client, 'receipt.png', b'\x89PNG\r\n\x1a\n\x00\x00', 'image/png')
    error = _refused(client, 'attachment text', dict(attachment=picture['id']))
    assert error['code'] == 'E_VALIDATION' and 'attachment get' in error['message']
    binary = _add(client, 'odd.txt', b'abc\x00def')
    assert _refused(client, 'attachment text', dict(attachment=binary['id']))['code'] == 'E_VALIDATION'
    large = _add(client, 'big.txt', b'x' * 65537)
    error = _refused(client, 'attachment text', dict(attachment=large['id']))
    assert error['code'] == 'E_VALUE_RANGE' and error['details']['limit'] == 65536
    assert _refused(client, 'attachment text', dict(attachment='01M4KNT651C88V99AMGH5FTQY6'))['code'] == 'E_RECORD_NOT_FOUND'

    # The statement import refuses the summary box as a statement, and says how to read it.
    for given in (dict(), dict(format='csv'), dict(format='ofx')):
        error = _refused(client, 'reconcile import', dict(account='Checking', attachment=summary['id'], **given))
        assert error['code'] == 'E_VALIDATION', error
        assert f"attachment text {summary['id']}" in error['message'], error
        assert error['details']['read_with'] == f"attachment text {summary['id']}"


def test_a_statement_comes_back_in_pages_when_asked(client):
    checking = _add(client, 'checking-2026-07.ofx', CHECKING)
    whole = client.run('reconcile import', dict(account='Checking', attachment=checking['id']), company=COMPANY)
    total = whole['counts']['lines']
    assert total == len(whole['lines']) == 39 and not whole['has_more'] and whole['next_cursor'] is None
    pages, cursor = [], None
    while True:
        page = client.run('reconcile import', dict(account='Checking', attachment=checking['id'], limit=15,
                                                   **({'cursor': cursor} if cursor else {})), company=COMPANY)
        assert page['counts'] == whole['counts']
        pages.append(page)
        cursor = page['next_cursor']
        assert page['has_more'] == (cursor is not None)
        if not cursor:
            break
    assert [len(p['lines']) for p in pages] == [15, 15, 9]
    assert [line for p in pages for line in p['lines']] == whole['lines']
    assert 'lines 1 to 15 of 39' in pages[0]['next_step'] and 'lines 31 to 39 of 39.' in pages[2]['next_step']
    # A cursor belongs to its file and account.
    other = _add(client, 'checking-copy.ofx', CHECKING + b'\r\n')
    for data in (dict(attachment=other['id'], cursor=pages[0]['next_cursor']),
                 dict(attachment=checking['id'], cursor='not-a-cursor')):
        error = _refused(client, 'reconcile import', dict(account='Checking', **data))
        assert error['code'] == 'E_VALIDATION' and error['details']['fields'][0]['field'] == 'cursor', error


@pytest.mark.timeout(900)
def test_the_same_text_attachment_through_python_cli_http_and_mcp(root, tmp_path):
    from tests.mcp_matrix_support import Matrix
    seed = bookflow.connect(data_root=str(root))
    summary = _add(seed, 'visa-2026-07-summary.txt', SUMMARY)
    picture = _add(seed, 'receipt.png', b'\x89PNG\r\n\x1a\n\x00\x00', 'image/png')
    checking = _add(seed, 'checking-2026-07.ofx', CHECKING)

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                read = await matrix.call(surface, 'attachment text', dict(attachment=summary['id']))
                refused = await matrix.call(surface, 'attachment text', dict(attachment=picture['id']), rejected=True)
                results[surface] = (read, refused)
            # Over MCP a long statement is read in pages, each within the result budget.
            pages, cursor = [], None
            while True:
                page = await matrix.call('mcp', 'reconcile import', dict(account='Checking', attachment=checking['id'], limit=20,
                                                                         **({'cursor': cursor} if cursor else {})))
                pages.append(page)
                cursor = page['next_cursor']
                if not cursor:
                    break
            return results, pages, matrix.fetched
        finally:
            await matrix.close()

    results, pages, fetched = anyio.run(witness)
    for surface, (read, refused) in results.items():
        assert read['text'] == SUMMARY.decode('ascii') and read['encoding'] == 'utf-8', surface
        assert refused['code'] == 'E_VALIDATION' and 'attachment get' in refused['message'], surface
    assert len({tuple(sorted((k, v) for k, v in read.items() if not isinstance(v, dict))) for read, _ in results.values()}) == 1
    assert [len(p['lines']) for p in pages] == [20, 19] and pages[0]['has_more'] and not pages[1]['has_more']
    assert fetched == 0  # no page needed the full-result file: each fit the budget
