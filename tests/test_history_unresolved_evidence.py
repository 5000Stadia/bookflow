"""Actual governed audiences, financial producers and unresolved read evidence.

Initial users/credentials are explicit disposable policy fixtures. Faults alter
loaded rows only; they do not pretend malformed data has a public writer.
"""
from contextlib import contextmanager
import ast
import hashlib
from pathlib import Path

import pytest

from bookflow import BookflowError
from bookflow.adapters.http.app import Credential
from bookflow.adapters.http.execution import run_hosted
from bookflow.company import payment_authority as authority
from bookflow.core import identity_admin_binding as binding, registry
from bookflow.core.config import Config, os_login
from bookflow.core.context import Context, client_version
from bookflow.core.host import Host
from bookflow.core.publication import OSBinding
from bookflow.core.publication_audit import open_selected
from bookflow.hub import audit_projection as projection, credentials, identity_admin as admin
from bookflow.hub.permission_catalog import ScopeKey
from bookflow.storage.engine import open_database
from tests.conftest import make_actor
from tests.permission_admin_support import insert, OLD, CONTEXT
from tests.test_audit_projection_activity import world, storage
from tests.test_history_cohort_omission import financial, missing_note
from tests.test_history_hub_explanations import produce
from tests.test_service_sales_lifecycle import COMPANY


@pytest.fixture(scope='module')
def governed(financial):
    f = financial
    root, client, cid = f['world']['root'], f['client'], f['company']
    owner = Config.load(root/'config.toml').user_table(os_login())['user_id']
    people = {name: make_actor(root, 'unresolved-'+name, company_role=(cid, 'standard'))
        for name in ('entitled', 'work_denied', 'ledger_denied', 'vendor_denied', 'mutable')}
    agents = {name: make_actor(root, 'unresolved-'+name, company_role=(cid, 'standard'),
        kind='agent', owner_user_id=owner) for name in ('agent_ok', 'agent_denied', 'principal_denied')}
    principals = {'agent_ok': 'entitled', 'agent_denied': 'entitled', 'principal_denied': 'work_denied'}
    users = people | agents
    credentials_by_name = {}
    with open_database(root/'hub.db', writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE')
        for name, denied in [('work_denied', 'customer-work'), ('ledger_denied', 'ledger.read'),
                             ('vendor_denied', 'vendor'), ('agent_denied', 'customer-work')]:
            db.raw.execute('UPDATE memberships SET denies=? WHERE user_id=?', ('["'+denied+'"]', users[name]))
        for name, agent in agents.items():
            insert(db.raw, 'agent_authority', dict(agent_user_id=agent, epoch=1, version=1,
                authorized_at=OLD, authorized_by=owner, permitted_use_at=OLD))
            insert(db.raw, 'agent_principals', dict(agent_user_id=agent,
                principal_user_id=people[principals[name]], assigned_by=owner, assigned_at=OLD))
        for name, identifier in users.items():
            principal = people[principals[name]] if name in agents else None
            row, secret = credentials.issue_token(db, user_id=identifier, kind='bearer', label='Owned history test',
                days=None, via='python', actor_id=owner, on_behalf_of=principal)
            credentials_by_name[name] = Credential(identifier, row['id'], 'bearer', None,
                on_behalf_of=principal, actor_kind='agent' if name in agents else 'human', secret=secret)
        db.raw.execute('COMMIT')
    vendor = client.vendor.create(name='Unresolved hidden annotation target', company=COMPANY)
    hidden = client.note.add(record_type='vendor', record_id=vendor['id'], body='Hidden vendor note', company=COMPANY)['note']
    hidden_event = client.audit.list(record_type='note', record_id=hidden['id'], company=COMPANY)['items'][0]['id']
    empty_selection = client.run('payment selection create', dict(mode='new_receipt', customer=f['customer'],
        date='2026-06-02', amount='1', label='Empty envelope selection'), company=COMPANY)
    selection = client.run('payment selection create', dict(mode='new_receipt', customer=f['customer'],
        date='2026-06-02', amount='1', label='Envelope selection'), company=COMPANY)
    client.run('payment selection update', dict(selection=selection['id'], expected_version=1,
        set_items=[dict(invoice=f['invoice']['id'], expected_version=2, amount='1')]), company=COMPANY)
    selection_event = client.audit.list(record_type='payment_selection', record_id=selection['id'], company=COMPANY)['items'][0]['id']
    with open_database(f['path'], writable=False) as db:
        payment_event = db.raw.execute('SELECT audit_event_id FROM payment_operations WHERE id=?',
            (f['payment']['effect']['operation_id'],)).fetchone()[0]
    host = Host(root, version=client_version());host.start()
    try:
        yield dict(f, host=host, credentials=credentials_by_name, users=users,
            admin=OSBinding.capture(host, os_login()), hidden=hidden, hidden_event=hidden_event,
            selection=selection, empty_selection=empty_selection, selection_event=selection_event, payment_event=payment_event)
    finally:
        host.stop()


def context():return Context.new('http', 'Unresolved history').model_copy(update={'request_id':'REQUEST'})


def read(g, who, mode, **fields):
    return run_hosted(g['host'], registry.get('activity' if mode=='activity' else 'audit '+mode),
        fields, context(), g['credentials'][who], g['company'], 'option', False)


@contextmanager
def observed(g, who='entitled', mode='list'):
    ctx = context()
    selection = projection.HistorySelection(mode=mode, company=g['company'],
        **(dict(record_type='customer', record_id=g['customer']) if mode=='activity' else {}))
    from bookflow.adapters.http.execution import _reader_binding
    cred = _reader_binding(g['host'], g['credentials'][who], ctx.request_id)
    with binding.hosted_reader(g['host'], cred, request_id=ctx.request_id) as reader:
        open_selected(reader, selection, ctx)
        yield projection.make_audience(reader, read_capability='activity' if mode=='activity' else 'audit'), selection


def fixed_error(action):
    with pytest.raises(BookflowError) as caught:action()
    assert caught.value.code == 'E_VALIDATION' and caught.value.details == {'reason':'audit_format'}
    assert caught.value.message == BookflowError('E_VALIDATION').message
    return caught.value


@pytest.mark.parametrize('mode', ['list', 'activity'])
def test_same_event_entitled_error_restricted_usable_scan(governed, monkeypatch, mode):
    g = governed
    # Restrict the audit scan to the three actual customer-note producers.
    fields = dict(record_type='note', since=g['since'], until=read(g,'entitled','show',event=g['hidden_event'])['at']) if mode=='list' else dict(
        record_type='customer', record_id=g['customer'], kinds=['note'])
    baseline = read(g, 'work_denied', mode, **fields)
    assert baseline['count'] == 3
    identity = 'id' if mode=='list' else 'event_id'
    assert {row[identity] for row in baseline['items']} == set(g['events'])
    # Full timestamps are exclusive at until (_matches rejects event.at >= until).
    assert all(row[identity] != g['hidden_event'] for row in baseline['items'])
    missing_note(monkeypatch, g)
    before = storage(g['path'])
    for who in ('entitled',):
        error = fixed_error(lambda:read(g, who, mode, **fields))
        error.publication_document.check()
        fixed_error(lambda:read(g, who, 'show', event=g['events'][1]))
    for who in ('work_denied',):
        page = read(g, who, mode, **fields)
        assert page['count'] == 2
        identity = 'id' if mode=='list' else 'event_id'
        expected = [row for row in baseline['items'] if row[identity] != g['events'][1]]
        assert page['items'] == expected
        assert {row[identity] for row in expected} == {g['events'][0],g['events'][2]}
        token, arg = ('next_before','before') if mode=='list' else ('next_cursor','cursor')
        first = read(g, who, mode, limit=1, **fields)
        second = read(g, who, mode, limit=1, **fields, **{arg:first[token]})
        assert first['count']==second['count']==1 and first[token] and second[token] is None
        assert first['items']+second['items']==page['items']
        with pytest.raises(BookflowError) as caught:read(g, who, 'show', event=g['events'][1])
        assert caught.value.code=='E_EVENT_NOT_FOUND' and caught.value.details=={}
        page.check()
    # Command and since are applied by _matches after projection. Raw record
    # identity filters select candidates earlier and deliberately are not used here.
    if mode=='list':fixed_error(lambda:read(g,'entitled','list',**fields,command='customer create'))
    else:fixed_error(lambda:read(g,'entitled','activity',record_type='customer',record_id=g['customer'],since='2999-01-01'))
    assert storage(g['path'])==before and g['host']._readers_attached==0


@pytest.mark.parametrize('who', ['ledger_denied', 'agent_denied', 'principal_denied', 'agent_ok'])
def test_real_governed_actor_principal_envelope(governed, monkeypatch, who):
    g=governed;missing_note(monkeypatch,g)
    if who=='agent_ok':
        fixed_error(lambda:read(g,who,'show',event=g['events'][1])).publication_document.check()
    else:
        with pytest.raises(BookflowError) as caught:read(g,who,'show',event=g['events'][1])
        assert caught.value.code=='E_EVENT_NOT_FOUND' and caught.value.details=={}
    with observed(g,who) as (audience,selection):
        assert audience.identity.actor==g['users'][who]
        if who.startswith('agent') or who=='principal_denied':
            expected='work_denied' if who=='principal_denied' else 'entitled'
            assert audience.identity.principal==g['users'][expected]
        # Real unchanged graph-free notes remain visible to these same bindings.
        assert projection.project_event(audience,g['events'][0],company=g['company']) is not None


@pytest.mark.parametrize('fault', ['operation_ids', 'selection_context', 'missing_owned'])
def test_real_financial_graph_faults(governed, monkeypatch, fault):
    g = governed;original = authority._evidence_rows
    operation = g['payment']['effect']['operation_id']
    event = g['selection_event'] if fault=='selection_context' else g['payment_event']
    def loaded(db, table, field, value, cache):
        rows = original(db,table,field,value,cache)
        if table.name=='payment_operations' and value==operation:
            if fault=='missing_owned':return []
            if fault=='operation_ids':return [dict(row,request_snapshot='{"resolved_transaction_ids":[1]}') for row in rows]
        if fault=='selection_context' and table.name=='payment_selection_revisions' and value==g['selection']['id']:
            return [dict(row,context_snapshot='{}') for row in rows]
        return rows
    with pytest.raises(BookflowError) as caught:read(g,'work_denied','show',event=event)
    assert caught.value.code=='E_EVENT_NOT_FOUND'
    monkeypatch.setattr(authority,'_evidence_rows',loaded)
    if fault=='selection_context':
        # The cohort prefetches selection contexts directly. Fault the same
        # loaded relation there, so fresh scalar proof checks see identical facts.
        original_read=authority._EventCohort._read
        def prefetched(cohort,table,field,identifiers,columns):
            groups=original_read(cohort,table,field,identifiers,columns)
            if table.name=='payment_selection_revisions':
                return {key:[dict(row,context_snapshot='{}') if row.get('selection_id')==g['selection']['id']
                             else row for row in rows] for key,rows in groups.items()}
            return groups
        monkeypatch.setattr(authority._EventCohort,'_read',prefetched)
    with open_database(g['path'],writable=False) as db:
        cohort=authority._EventCohort(db,[event])
        for resolve in (lambda:authority.event_requirements(db,event),lambda:cohort.requirements(event)):
            with pytest.raises(BookflowError) as caught:resolve()
            assert caught.value.code=='E_PERMISSION' and caught.value.details=={'reason':'unresolved_payment_evidence'}
    fixed_error(lambda:read(g,'entitled','show',event=event)).publication_document.check()
    fixed_error(lambda:read(g,'entitled','list',command='customer create'))
    with pytest.raises(BookflowError) as caught:read(g,'work_denied','show',event=event)
    assert caught.value.code=='E_EVENT_NOT_FOUND'


def test_annotation_target_still_hides_pending_error(governed, monkeypatch):
    g=governed;original=authority._evidence_rows
    def loaded(db,table,field,value,cache):
        rows=original(db,table,field,value,cache)
        return [] if table.name=='notes' and value==g['hidden']['id'] else rows
    baseline=read(g,'vendor_denied','list',record_type='note',record_id=g['hidden']['id'])
    assert baseline['count']==0
    monkeypatch.setattr(authority,'_evidence_rows',loaded)
    with observed(g,'vendor_denied') as (audience,selection):
        audience.require(g['company'],authority.READ_REQUIREMENT_ENVELOPE)
        assert projection.project_event(audience,g['hidden_event'],company=g['company']) is None
    assert read(g,'vendor_denied','list',record_type='note',record_id=g['hidden']['id'])==baseline
    with pytest.raises(BookflowError) as caught:read(g,'vendor_denied','show',event=g['hidden_event'])
    assert caught.value.code=='E_EVENT_NOT_FOUND'
    fixed_error(lambda:read(g,'entitled','show',event=g['hidden_event']))


def test_retained_error_rechecks_after_real_permission_reduction(governed, monkeypatch):
    g=governed;missing_note(monkeypatch,g)
    error=fixed_error(lambda:read(g,'mutable','show',event=g['events'][1]))
    error.publication_document.check()
    produce(g['host'],g['admin'],admin.PutMembership(g['users']['mutable'],ScopeKey('company',g['company']),
        admin.Version(1),'standard',denies=('customer-work',)))
    with pytest.raises(BookflowError) as caught:error.publication_document.check()
    assert caught.value.code=='E_PERMISSION'
    with pytest.raises(BookflowError) as caught:read(g,'mutable','show',event=g['events'][1])
    assert caught.value.code=='E_EVENT_NOT_FOUND'


def test_exact_resolution_trigger_and_envelope_failure_boundary(governed, monkeypatch):
    g=governed
    with observed(g) as (audience,selection):
        event=g['events'][0]
        def unresolved(identifier):
            assert identifier==event
            raise BookflowError('E_PERMISSION',details={'reason':'unresolved_payment_evidence'})
        for error in [BookflowError('E_PERMISSION'),BookflowError('E_PERMISSION',details={'reason':'capability_not_activated'}),
                      BookflowError('E_COMPANY_NOT_FOUND'),OSError('owned envelope I/O')]:
            def fail(*args):raise error
            with monkeypatch.context() as patch:
                patch.setattr(authority,'require_resource',fail)
                if isinstance(error,OSError):
                    with pytest.raises(OSError) as caught:
                        projection.project_event(audience,event,company=g['company'],requirement_resolver=unresolved)
                    assert caught.value is error
                else:assert projection.project_event(audience,event,company=g['company'],requirement_resolver=unresolved) is None
        # Identical error from later authorization is ordinary omission, not fallback.
        def later(*args):raise BookflowError('E_PERMISSION',details={'reason':'unresolved_payment_evidence'})
        with monkeypatch.context() as patch:
            patch.setattr(authority,'authorize_event',later)
            assert projection.project_event(audience,event,company=g['company']) is None
        for details in ({}, {'reason':'unresolved_payment_evidence','extra':'unknown'}, {'reason':'unknown'}):
            def other(identifier):raise BookflowError('E_PERMISSION',details=details)
            assert projection.project_event(audience,event,company=g['company'],requirement_resolver=other) is None


def test_exact_read_envelope_union_including_selection_delegation(governed, monkeypatch):
    from bookflow.company import payment_recovery
    from bookflow.core.registry import EXPLICIT_GRANT_ONLY_CAPABILITIES
    g=governed
    with observed(g) as (audience,selection):
        db=audience.reader.session.company
        events=[r[0] for r in db.raw.execute('SELECT id FROM audit_events ORDER BY seq')]
        cohort=authority._EventCohort(db,events)
        scalar=[authority.event_requirements(db,event) for event in events]
        batch=[cohort.requirements(event) for event in events]
        assert scalar==batch and () in scalar
        outputs=scalar+batch+[authority.requirements(db,[]),authority.requirements(db,[g['payment']['id']])]
        for identifier in (g['selection']['id'],g['empty_selection']['id']):
            seen=[];original=authority.require_resource
            def enforce(session,capability,role):
                seen.append((capability,role))
                return original(session,capability,role)
            with monkeypatch.context() as patch:
                patch.setattr(authority,'require_resource',enforce)
                payment_recovery.authorize_selection(audience.reader.session,identifier,write=False)
            actual=authority._PublicationSelectionCohort(db,[identifier]).requirements(identifier,False)
            assert tuple(seen)==actual
            outputs.extend((tuple(seen),actual))
        assert set().union(*(set(x) for x in outputs))==set(authority.READ_REQUIREMENT_ENVELOPE)=={
            ('ledger.read','member'),('customer-work','member')}
        assert (('ledger.read','member'),) in outputs
        assert EXPLICIT_GRANT_ONLY_CAPABILITIES.isdisjoint(cap for cap,_ in authority.READ_REQUIREMENT_ENVELOPE)


# Reviewed read-producer closure: complete scalar/event/selection walkers and
# delegates, plus selection's scalar entrance. Adding a helper must reopen this
# inventory, not merely append its observed output to the behavioral union above.
ENVELOPE_SOURCES = {'company/payment_authority.py': (None, '60f79b6d63858217990ef4cef774776a92de35e4c95fc59182ecf3f9f0d595a6'), 'company/deposit_draft_evidence.py': (None, '5175a3ae0130ac138bdcb627bbb3137c53c67edee62de44ce89e237cfb16c505'), 'company/payment_recovery.py': (('authorize_selection',), 'a311ece970426ed2585ee7a57ccddba74d6f3cd1ce249603fa769f2b10916c01')}


def test_read_envelope_source_inventory():
    source=Path(__file__).resolve().parents[1]/'src/bookflow'
    for name,(selected,expected) in ENVELOPE_SOURCES.items():
        tree=ast.parse((source/name).read_text())
        if selected is not None:
            nodes=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in selected]
            assert {node.name for node in nodes}==set(selected)
            tree=ast.Module(body=nodes,type_ignores=[])
        assert hashlib.sha256(ast.dump(tree,include_attributes=False).encode()).hexdigest()==expected, name
