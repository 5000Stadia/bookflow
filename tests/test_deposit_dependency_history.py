"""Owned immutable history witnesses; no production corruption or live data."""
import pytest
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


def test_noop_receipt_does_not_invent_a_header_change(client, sale, driver, monkeypatch):
    import sqlite3
    from tests.test_deposit_dependency_binding import observe
    from tests.test_deposit_lifecycle import replacement
    from tests.test_row8_journal import database_path
    from bookflow.company import deposit_dependency_history as history
    from bookflow.company.deposit_dependency_models import InspectionRoot
    from bookflow.core.publication import OSBinding
    document = additional_document(client, sale)
    posted = driver.run('post', dict(operation_key='history-noop-post', document=document))
    root = InspectionRoot(kind='deposit', id=posted.current.id)
    def issue(s):
        binding = OSBinding.from_session(s)
        recipe, facts = history.capture(s, root, binding)
        return history.issue(s, recipe, facts, binding)
    guard = observe(client, monkeypatch, issue)
    result = driver.run('update', dict(operation_key='history-noop', deposit=posted.current.id,
        expected_version=1, document=replacement(posted, document)), reason='Confirm captured deposit without change')
    assert not result.changed and result.current.version == 1 and result.effect.headers == ()
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute("SELECT count(*) FROM audit_entries WHERE event_id=? AND record_type='deposit_operation'", (result.effect.audit_event_id,)).fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM audit_entries WHERE event_id=? AND record_type='transaction'", (result.effect.audit_event_id,)).fetchone()[0] == 0
    before = driver.dump()
    def unchanged(s):
        result = history.compare(s, guard, root, OSBinding.from_session(s))
        assert result.matches and result.changes == () and not result.unknown_history
        assert result.baseline.endpoint == result.current.endpoint
    observe(client, monkeypatch, unchanged)
    assert driver.dump() == before


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'wrong_version', 'wrong_owner', 'array', 'malformed'])
def test_missing_or_invalid_owned_images_are_distinct_from_programming_errors(client, sale, driver, damage):
    import copy
    from bookflow.core import audit
    from bookflow.company.deposit_dependency_history import MissingHistory
    posted = driver.run('post', dict(operation_key='decoder-negative', document=additional_document(client, sale)))
    before = driver.dump()
    with driver.session() as s:
        # Mutate only an owned in-memory decoder input, never SQL/source/guards.
        reader = History(s); reader.load('transaction')
        entries = copy.deepcopy(reader.entries['transaction'][posted.current.id])
        value = audit.decode_snapshot(entries[0]['after'])
        if damage == 'missing': entries = []
        elif damage == 'duplicate': entries.append(copy.deepcopy(entries[0]))
        elif damage == 'wrong_version':
            entries[0]['after'] = audit.encode_snapshot(dict(value, version=99))
        elif damage == 'wrong_owner':
            entries[0]['after'] = audit.encode_snapshot(dict(value, id='foreign-owner'))
        elif damage == 'array': entries[0]['after'] = audit.RAW + b'[]'
        else: entries[0]['after'] = audit.RAW + b'{'
        reader.entries['transaction'][posted.current.id] = entries
        assert reader.take('transaction', posted.current.id) is None
        assert reader.unknown == {'transaction:' + posted.current.id}
        with pytest.raises(KeyError):
            reader.take('unimplemented_kind', posted.current.id)
    assert driver.dump() == before


def test_root_type_missing_and_changed_recipe_never_become_unknown(client,sale,driver,monkeypatch):
    from tests.test_deposit_dependency_binding import observe
    from bookflow.company import deposit_dependency_history as history
    from bookflow.company.deposit_dependency_models import InspectionRoot
    from bookflow.core.publication import OSBinding
    from bookflow.core.errors import BookflowError
    posted=driver.run('post',dict(operation_key='root-types',document=additional_document(client,sale)))
    before=driver.dump()
    def check(s):
        binding=OSBinding.from_session(s)
        for root in (InspectionRoot(kind='payment',id=posted.current.id),InspectionRoot(kind='deposit',id='00000000000000000000000000')):
            with pytest.raises(BookflowError) as error:history.capture(s,root,binding)
            assert error.value.code=='E_RECORD_NOT_FOUND' and not error.value.details
        root=InspectionRoot(kind='deposit',id=posted.current.id)
        recipe,facts=history.capture(s,root,binding)
        token=history.issue(s,recipe,facts,binding)
        with pytest.raises(BookflowError) as error:history.compare(s,token,InspectionRoot(kind='payment',id=posted.current.id),binding)
        assert error.value.code=='E_VALIDATION'
        with pytest.raises(BookflowError) as error:history.issue(s,recipe,facts.model_copy(update={'digest':'0'*64}),binding)
        assert error.value.code=='E_VALIDATION'
        with pytest.raises(BookflowError):history.issue(s,recipe,facts.model_dump(),binding)
    observe(client,monkeypatch,check)
    assert driver.dump()==before


def test_earlier_and_latest_writer_are_attributed_to_their_own_events(client,sale,driver,monkeypatch,root):
    import json
    from dataclasses import replace
    from tests.conftest import make_actor,as_user
    from tests.test_deposit_dependency_binding import observe
    from tests.test_deposit_lifecycle import replacement
    from tests.test_service_sales_lifecycle import COMPANY
    from bookflow.company import deposit_dependency_history as history,deposit_lifecycle as lifecycle,deposit_persistence as persistence
    from bookflow.company.deposit_dependency_models import InspectionRoot
    from bookflow.core.context import Context,Interface
    from bookflow.core.publication import OSBinding
    document=additional_document(client,sale)
    first=driver.run('post',dict(operation_key='authors-original',document=document))
    target=InspectionRoot(kind='deposit',id=first.current.id)
    def issue(s):
        binding=OSBinding.from_session(s);recipe,facts=history.capture(s,target,binding)
        return history.issue(s,recipe,facts,binding),s.company_row['id'],s.actor.id
    guard,company,owner=observe(client,monkeypatch,issue)
    other=make_actor(root,'deposit-other-editor',company_role=(company,'standard'))
    admitted=observe(as_user(root,'deposit-other-editor'),monkeypatch,lambda s:s,company)
    ctx=Context.new(Interface.python,'earlier writer',reason='Earlier editor correction')
    with driver.session() as base:
        s=replace(base,actor=admitted.actor,os_login=admitted.os_login,memberships=admitted.memberships)
        inp=lifecycle.INPUTS['update'].model_validate(dict(operation_key='authors-earlier',deposit=first.current.id,expected_version=1,
            document=dict(replacement(first,document),memo='Earlier editor')))
        plan=lifecycle.prepare(s,ctx,inp,'update')
        inp=inp.model_copy(update={'dependency_guard':plan.dependency_guard})
        earlier=persistence.execute(s,ctx,lifecycle.prepare(s,ctx,inp,'update'))
    latest=driver.run('update',dict(operation_key='authors-latest',deposit=first.current.id,expected_version=2,
        document=dict(replacement(earlier,document),memo='Latest editor')),reason='Latest editor correction')
    before=driver.dump()
    def check(s):
        result=history.compare(s,guard,target,OSBinding.from_session(s))
        assert not result.matches and not result.unknown_history
        headers=[x for x in result.changes if x.kind=='transaction' and x.record_id==first.current.id]
        assert [(x.event_id,x.actor_id,x.actor_kind,x.on_behalf_of,x.interface,x.version_before,x.version_after) for x in headers]==[
            (earlier.effect.audit_event_id,other,'human',None,'python',1,2),
            (latest.effect.audit_event_id,owner,'human',None,'python',2,3)]
        assert all('commercial.memo' in x.fields for x in headers)
    observe(client,monkeypatch,check)
    assert driver.dump()==before


def test_work_order_billing_keeps_the_full_immutable_parent_link(client,sale,monkeypatch):
    import sqlite3
    from tests.test_work_billing_lifecycle import accepted,bill
    from tests.test_customer_work_lifecycle import run
    from tests.test_payment_receipts import method
    from tests.test_row8_journal import database_path
    from tests.test_deposit_dependency_binding import observe
    from tests.test_service_sales_lifecycle import COMPANY
    from bookflow.company import deposit_dependency_history as history
    from bookflow.company.deposit_dependency_models import InspectionRoot
    from bookflow.core.publication import OSBinding
    source=accepted(client,sale)
    order=run(client,'estimate','work-order',estimate=source['id'],expected_version=source['version'],conversion_key='history-ancestry',date='2026-01-13')
    invoice=bill(client,order,noun='work-order')
    paid=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='1',payment_method=method(client),
        operation_key='ancestry-payment',applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='1')])),company=COMPANY)
    with sqlite3.connect(database_path(client)) as db:
        expected={x[0] for x in db.execute('SELECT id FROM work_links WHERE destination_document_id=?',(order['id'],))}
        before=tuple(db.iterdump())
    assert len(expected)==1
    def check(s):
        binding=OSBinding.from_session(s);root=InspectionRoot(kind='payment',id=paid['id'])
        recipe,facts=history.capture(s,root,binding)
        assert not facts.unknown,facts.unknown
        assert {x.id for x in facts.records if x.kind=='work_link'}==expected
        assert {x.id for x in facts.records if x.kind=='work_document'}=={source['id'],order['id']}
        token=history.issue(s,recipe,facts,binding)
        assert history.compare(s,token,root,binding).matches
    observe(client,monkeypatch,check)
    with sqlite3.connect(database_path(client)) as db:assert tuple(db.iterdump())==before
