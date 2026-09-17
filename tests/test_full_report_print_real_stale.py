"""A real write invalidates full-print pagination; the rendered report starts over."""
import pytest
from fastapi.testclient import TestClient

from bookflow.core.errors import BookflowError
from tests.payment_raw_evidence import database
from tests.test_full_report_print import PERIOD, Print, print_site as _seed_print_site
from tests.test_report_export import _shown
from tests.test_report_print import _Document, matches


class _RenderedReport(_Document):
    def handle_data(self, text):
        for node in self.stack:
            node['text'] = node.get('text', '') + text

    def select(self, selector):
        return [node for node in self.nodes if matches(node, selector)]


@pytest.fixture
def changing_print_site(tmp_path_factory):
    # Own a separate company: this write must not alter other tests' 230-posting fixture.
    yield from _seed_print_site.__wrapped__(tmp_path_factory)


def test_full_print_restarts_after_real_hosted_write(changing_print_site, monkeypatch):
    site = changing_print_site
    path = next((site.directory / 'root').rglob('company.db'))
    complete = Print.complete
    trace, first_pages, stale_errors, writes = [], [], [], []
    observed = {}

    with TestClient(site.handle.app) as writer, TestClient(site.handle.app) as browser:
        from tests.test_row5_browser_acceptance import PASSWORD
        assert browser.post('/login', json={'username': site.login, 'password': PASSWORD}).status_code == 200

        def during_print(read, company, cmd, raw):
            def timed_read(name, inputs, selected_company):
                cursor = inputs.get('cursor')
                trace.append('continuation' if cursor else 'first')
                if cursor and not writes:
                    assert cursor == first_pages[0]['next_cursor']
                    # Earlier than the fixture's September 15/16 postings, within the same filter.
                    response = writer.post(f'/companies/{site.company}/commands/journal.post',
                        headers={'Authorization': 'Bearer ' + site.secret,
                                 'X-Bookflow-Client-Name': 'print-stale-witness',
                                 'X-Bookflow-Reason': 'Change the books during full printing'},
                        json={'date': '2026-09-14', 'number': 'MID-TRAVERSAL', 'lines': [
                            {'account': site.bank, 'side': 'debit', 'amount': '2.50',
                             'description': 'MID-TRAVERSAL'},
                            {'account': 'Excluded counterpart', 'side': 'credit', 'amount': '2.50',
                             'description': 'EXCLUDED-ACCOUNT'}]})
                    assert response.status_code == 200, response.text
                    writes.append(response.json())
                    observed['after_write'] = database(path)
                try:
                    page = read(name, inputs, selected_company)
                except BookflowError as error:
                    # Record the real report owner's refusal, then let the real traversal handle it.
                    stale_errors.append(error.code)
                    raise
                if not cursor:
                    first_pages.append(page)
                return page

            result = complete(timed_read, company, cmd, raw)
            observed['fresh'] = complete(read, company, cmd, raw)
            return result

        monkeypatch.setattr(Print, 'complete', during_print)
        response = browser.get(Print.print_url(site.company, 'general-ledger',
                                              {**PERIOD, 'account': site.bank}))

    assert response.status_code == 200, response.text
    assert 'data-report-full="1"' in response.text and 'All 233 matching rows' in response.text
    result = _shown(response)
    assert trace == ['first', 'continuation', 'first', 'continuation']
    assert stale_errors == ['E_QUERY_STALE'] and len(writes) == 1
    assert len(first_pages) == 2 and len(first_pages[0]['rows']) == 200
    old_watermark, new_watermark = [page['metadata']['audit_watermark'] for page in first_pages]
    assert new_watermark == old_watermark + 1
    assert result['metadata']['audit_watermark'] == new_watermark
    assert first_pages[0]['totals']['period_debits']['amount'] == '123457018.01'

    # Compare every rendered field with a fresh real traversal; only wall-clock generation differs.
    def without_generation(value):
        return {**value, 'metadata': {key: item for key, item in value['metadata'].items()
                                     if key != 'generation_time'}}
    assert without_generation(result) == without_generation(observed['fresh'])
    assert len(result['rows']) == result['count'] == 233 and result['next_cursor'] is None
    postings = [row['description'].split()[0] for row in result['rows'] if row['kind'] == 'posting']
    assert postings == ['MID-TRAVERSAL', *[f'PRINT-ROW-{i:03d}' for i in range(230)]]
    assert result['rows'][0]['signed_balance']['amount'] == '10.00'
    assert result['rows'][-1]['signed_balance']['amount'] == '123457030.51'
    assert result['totals']['period_debits']['amount'] == '123457020.51'
    assert result['totals']['period_credits']['amount'] == '0.00'
    # Check the printable table itself, independently of the hidden structured-data panel.
    rendered = _RenderedReport()
    rendered.feed(response.text)
    assert len(rendered.select('#report-lines tbody tr[data-account]')) == 233
    descriptions = rendered.select('#report-lines tr[data-kind=posting] .report-description')
    assert [node['text'].split()[0] for node in descriptions] == postings
    assert [node['text'].strip() for node in descriptions] == [
        row['description'].strip() for row in result['rows'] if row['kind'] == 'posting']
    for key in ('period_debits', 'period_credits'):
        assert [node['text'].strip() for node in rendered.select(f'[data-total={key}] dd')] == [
            result['totals'][key]['amount'] + ' USD']
    balances = rendered.select('#report-lines [data-value=balance]')
    assert [node['text'].strip() for node in balances] == [
        'Running balance' + row['signed_balance']['amount'] for row in result['rows']]
    scope = rendered.select('#report-print-scope')
    assert len(scope) == 1 and f'Audit watermark {new_watermark}.' in scope[0]['text']
    assert 'EXCLUDED-DATE' not in response.text and 'EXCLUDED-ACCOUNT' not in response.text
    assert database(path) == observed['after_write'], 'Printing/restart/fresh reads wrote to the company'
