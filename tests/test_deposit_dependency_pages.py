"""Complete owned graphs and independent page/attribution oracles."""
import sqlite3
import pytest
from bookflow.company import deposit_dependency_history as history
from bookflow.company.deposit_dependency_models import InspectionRoot, PageInput
from bookflow.company.deposit_dependency_pages import changes_page
from bookflow.core.publication import OSBinding
from tests.test_deposit_dependency_binding import observe
from tests.test_deposit_lifecycle import driver
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import posted, method
from tests.test_work_billing_lifecycle import accepted, bill
from tests.test_row8_journal import database_path


@pytest.mark.timeout(600)
@pytest.mark.parametrize('count', [3, 201, 403])
def test_complete_mixed_settlement_graph_has_all_owned_ids(client, sale, monkeypatch, count):
    work = accepted(client, sale)
    first = bill(client, work)
    invoices = [first] + [posted(client, sale['customer'], sale['item'], '1', f'HISTORY-GRAPH-{i}') for i in range(count-1)]
    amount = f'{count//100}.{count%100:02d}'
    selection = client.run('payment selection create', dict(mode='new_receipt', customer=sale['customer'],
        date='2026-06-02', amount=amount), company=COMPANY)
    for offset in range(0, count, 200):
        selection = client.run('payment selection update', dict(selection=selection['id'], expected_version=selection['version'],
            set_items=[dict(invoice=row['id'], expected_version=1, amount='0.01') for row in invoices[offset:offset+200]]), company=COMPANY)
    paid = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount=amount,
        payment_method=method(client), operation_key='history-complete', applications=dict(mode='selection',
            selection=selection['id'], expected_version=selection['version'])), company=COMPANY)
    path = database_path(client)
    with sqlite3.connect(path) as db:
        before = tuple(db.iterdump())
        apps = {row[0] for row in db.execute('SELECT id FROM applications WHERE paying_transaction_id=?', (paid['id'],))}
        allocations = {row[0] for row in db.execute('SELECT id FROM application_allocations WHERE source_transaction_id=?', (paid['id'],))}
    assert len(apps) == len(allocations) == count
    expected_transactions = {paid['id']} | {row['id'] for row in invoices}
    def check(s):
        binding = OSBinding.from_session(s)
        root = InspectionRoot(kind='payment', id=paid['id'])
        recipe, facts = history.capture(s, root, binding)
        assert not facts.unknown, facts.unknown
        assert set(facts.transactions) == expected_transactions
        assert {row.id for row in facts.records if row.kind == 'application'} == apps
        assert {row.id for row in facts.records if row.kind == 'application_allocation'} == allocations
        assert work['id'] in {row.id for row in facts.records if row.kind == 'work_document'}
        assert {row.owner_id for row in facts.relations if row.kind == 'transaction_revision'} == expected_transactions
        token = history.issue(s, recipe, facts, binding)
        result = history.compare(s, token, root, binding)
        assert result.matches and result.baseline == result.current and result.changes == ()
        page = changes_page(s, token, root, PageInput(limit=200), binding)
        assert page.items == () and page.total_count == 0 and page.next_cursor is None and not page.unknown_history
    observe(client, monkeypatch, check)
    with sqlite3.connect(path) as db:
        assert tuple(db.iterdump()) == before


@pytest.mark.timeout(600)
def test_201_attributed_changes_all_page_sizes_unrelated_and_age_stability(client, sale, monkeypatch, driver):
    from datetime import date, timedelta
    from bookflow.core import clock
    from bookflow.core.errors import BookflowError
    from tests.test_deposit_lifecycle import additional_document
    posted_deposit = driver.run('post', dict(operation_key='page-policy', document=additional_document(client, sale)))
    root = InspectionRoot(kind='deposit', id=posted_deposit.current.id)
    def issue(s):
        binding = OSBinding.from_session(s)
        recipe, facts = history.capture(s, root, binding)
        return history.issue(s, recipe, facts, binding)
    guard = observe(client, monkeypatch, issue)
    path = database_path(client)
    with sqlite3.connect(path) as db:
        old_events = {row[0] for row in db.execute('SELECT id FROM audit_events')}
    for index in range(201):
        day = (date(2025, 1, 1) + timedelta(days=index)).isoformat()
        client.run('company update', dict(closing_date=day), company=COMPANY)
    with sqlite3.connect(path) as db:
        expected = [(row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7]) for row in db.execute(
            "SELECT e.id,a.record_id,e.actor_id,e.actor_kind,e.on_behalf_of,e.interface,a.version_before,a.version_after FROM audit_events e JOIN audit_entries a ON a.event_id=e.id WHERE e.command='company update' AND a.record_type='company_info' ORDER BY e.seq") if row[0] not in old_events]
        before = tuple(db.iterdump())
    assert len(expected) == 201
    def collect(s):
        binding = OSBinding.from_session(s)
        for limit in (1, 50, 200):
            cursor = None; actual = []
            while True:
                page = changes_page(s, guard, root, PageInput(limit=limit, cursor=cursor), binding)
                assert page.total_count == 201 and not page.unknown_history
                assert len(page.items) <= limit
                for item in page.items:
                    assert item.kind == 'company_info' and item.fields == ('closing_date',)
                    actual.append((item.event_id, item.record_id, item.actor_id, item.actor_kind, item.on_behalf_of,
                                   item.interface, item.version_before, item.version_after))
                cursor = page.next_cursor
                if cursor is None: break
            assert actual == expected
            assert len({row[0] for row in actual}) == 201
        return changes_page(s, guard, root, PageInput(limit=50), binding)
    first = observe(client, monkeypatch, collect)
    with sqlite3.connect(path) as db: assert tuple(db.iterdump()) == before
    client.run('company update', dict(fax='Unrelated display field'), company=COMPANY)
    now = clock.now()
    with monkeypatch.context() as patch:
        patch.setattr(clock, 'now', lambda: now + timedelta(seconds=123))
        next_page = observe(client, monkeypatch, lambda s: changes_page(s, guard, root,
            PageInput(limit=50, cursor=first.next_cursor), OSBinding.from_session(s)))
    assert [row.event_id for row in next_page.items] == [row[0] for row in expected[50:100]]
    client.run('company update', dict(closing_date='2025-12-31'), company=COMPANY)
    with pytest.raises(BookflowError) as caught:
        observe(client, monkeypatch, lambda s: changes_page(s, guard, root,
            PageInput(limit=50, cursor=first.next_cursor), OSBinding.from_session(s)))
    assert caught.value.code == 'E_PREVIEW_STALE'
