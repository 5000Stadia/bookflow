"""Owned immutable history witnesses; no production corruption or live data."""
from tests.test_deposit_lifecycle import driver, additional_document
from tests.test_service_sales_lifecycle import sale
from bookflow.company.deposit_dependency_history import History


def test_actual_additional_history_rows_are_decodable(client, sale, driver):
    document = additional_document(client, sale)
    posted = driver.run('post', {'operation_key': 'history-codecs', 'document': document})
    with driver.session() as s:
        history = History(s)
        header = history.take('transaction', posted.current.id)
        assert header is not None, history.unknown
        for kind in ('transaction_revision', 'deposit_profile', 'deposit_row_key',
                     'deposit_component_key', 'deposit_component', 'deposit_cash_cell',
                     'bank_effect_key', 'bank_effect_version', 'document_line_identity',
                     'document_line', 'posting_batch', 'posting_line', 'posting_line_source'):
            history.load(kind)
            expected = {identity for identity, row in history.raw[kind].items()
                        if row.get('transaction_id') == posted.current.id}
            assert expected, kind
            actual = {identity for identity in expected if history.take(kind, identity) is not None}
            assert actual == expected, (kind, history.unknown)


def test_actual_memo_change_has_immutable_event_attribution(client, sale, driver, monkeypatch):
    from tests.test_deposit_dependency_binding import observe
    from tests.test_deposit_lifecycle import replacement
    from bookflow.company import deposit_dependency_history as history
    from bookflow.company.deposit_dependency_models import InspectionRoot, PageInput
    from bookflow.company.deposit_dependency_pages import changes_page
    from bookflow.core.publication import OSBinding
    document = additional_document(client, sale)
    posted = driver.run('post', {'operation_key': 'history-before', 'document': document})
    root = InspectionRoot(kind='deposit', id=posted.current.id)
    def issue(s):
        binding = OSBinding.from_session(s)
        recipe, facts = history.capture(s, root, binding)
        assert not facts.unknown, facts.unknown
        return history.issue(s, recipe, facts, binding)
    guard = observe(client, monkeypatch, issue)
    updated = replacement(posted, document); updated['memo'] = 'Explicit correction'
    edited = driver.run('update', dict(operation_key='history-after', deposit=posted.current.id,
        expected_version=1, document=updated), reason='Explain original correction')
    before = driver.dump()
    def compare(s):
        binding = OSBinding.from_session(s)
        result = history.compare(s, guard, root, binding)
        assert not result.matches and not result.unknown_history, result.unknown_records
        header = [row for row in result.changes if row.kind == 'transaction' and row.record_id == posted.current.id]
        assert len(header) == 1
        assert header[0].event_id == edited.effect.audit_event_id
        assert header[0].actor_id == s.actor.id and header[0].actor_kind == 'human'
        assert header[0].on_behalf_of is None and header[0].interface == 'python'
        assert (header[0].version_before, header[0].version_after) == (1, 2)
        assert 'commercial.memo' in header[0].fields
        items = []; cursor = None
        while True:
            page = changes_page(s, guard, root, PageInput(limit=1, cursor=cursor), binding)
            assert page.total_count == len(result.changes)
            items.extend(page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert [(r.event_id, r.kind, r.record_id, r.fields) for r in items] == [(r.event_id, r.kind, r.record_id, r.fields) for r in result.changes]
    observe(client, monkeypatch, compare)
    assert driver.dump() == before


def test_actual_payment_invoice_and_sales_receipt_history(client, sale, monkeypatch):
    from tests.test_deposit_dependency_binding import observe
    from tests.test_payment_receipts import posted, method
    from tests.test_deposit_sources import uf
    from tests.test_service_sales_lifecycle import COMPANY
    from bookflow.company import deposit_dependency_history as history
    from bookflow.company.deposit_dependency_models import InspectionRoot
    from bookflow.core.publication import OSBinding
    invoice = posted(client, sale['customer'], sale['item'], '12.00', 'HISTORY-INVOICE')
    payment_method = method(client)
    payment = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='12.00',
        payment_method=payment_method, operation_key='history-source-payment', applications=dict(mode='inline', items=[
            dict(invoice=invoice['id'], expected_version=1, amount='12.00')])), company=COMPANY)
    receipt = client.run('sales-receipt post', dict(customer=sale['customer'], date='2026-06-02', deposit_to=uf(client),
        payment_method=payment_method, lines=[dict(item=sale['item'], quantity='1')]), company=COMPANY)
    def check(s):
        binding = OSBinding.from_session(s)
        for kind, identity, expected in [('payment', payment['id'], {payment['id'], invoice['id']}),
                                         ('sales_receipt', receipt['id'], {receipt['id']})]:
            root = InspectionRoot(kind=kind, id=identity)
            recipe, facts = history.capture(s, root, binding)
            assert set(facts.transactions) == expected
            assert not facts.unknown, facts.unknown
            token = history.issue(s, recipe, facts, binding)
            result = history.compare(s, token, root, binding)
            assert result.matches and not result.unknown_history
    observe(client, monkeypatch, check)
