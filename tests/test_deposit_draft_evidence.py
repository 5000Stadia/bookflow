"""Pure retained graph collection through real draft and selection producers."""
import pytest
from bookflow.company.deposit_draft_evidence import collect, DraftGraph
from bookflow.core.errors import BookflowError
from tests.test_deposit_drafts import cash, driver, run, sale


def test_removed_sources_and_abandoned_child_remain_in_graph(cash, driver, run):
    draft = run('create', {})
    with driver.session() as s:
        assert collect(s.company, draft=draft.id) == DraftGraph((), (draft.id,), ())
    draft = run('update', dict(draft=draft.id, expected_version=draft.version, set_sources=[cash]))
    child = run('create', dict(draft=draft.id, expected_version=draft.version), True)
    child = run('clear', dict(selection=child.id, expected_version=child.version), True)
    child = run('abandon', dict(selection=child.id, expected_version=child.version), True)
    draft = run('clear', dict(draft=draft.id, expected_version=draft.version))
    assert draft.summary.source_count == child.source_count == 0
    with driver.session() as s:
        before = tuple(s.company.raw.iterdump())
        expected = DraftGraph((cash['source'],), (draft.id,), (child.id,))
        assert collect(s.company, draft=draft.id) == expected
        assert collect(s.company, selection=child.id) == expected
        assert tuple(s.company.raw.iterdump()) == before
        with pytest.raises(BookflowError) as caught:
            collect(s.company, draft='00000000000000000000000000')
        assert caught.value.code == 'E_RECORD_NOT_FOUND'


@pytest.fixture(scope='module')
def audit_world(_seeded_template, tmp_path_factory):
    from pathlib import Path
    from tests.test_audit_projection_activity import world
    from tests.test_service_sales_lifecycle import sale, COMPANY
    from tests.test_deposit_drafts import cash
    from tests.test_deposit_draft_financial import run_private, financial
    from tests.test_work_billing_lifecycle import accepted, bill
    from bookflow.company import deposit_drafts as drafts, deposit_selection as selections
    generator = world.__wrapped__(_seeded_template, tmp_path_factory)
    w = next(generator)
    try:
        client = w['client']; sales = sale.__wrapped__(client)
        ordinary = cash.__wrapped__(client, sales)
        work = accepted(client, sales)
        method = client.run('payment-method create', dict(name='Graph work cash', kind='cash'), company=COMPANY)['id']
        from tests.test_deposit_sources import uf
        receipt = bill(client, work, key='graph-work-receipt', verb='sales-receipt',
                       deposit_to=uf(client), payment_method=method, amount_received='24.68')
        linked = dict(source=receipt['id'], source_type='sales_receipt', expected_version=receipt['version'])
        bank = client.account.create(name='Evidence bank', type='bank', company=COMPANY)['id']
        with pytest.MonkeyPatch.context() as patch:
            private = run_private.__wrapped__(client, patch)
            def command(owner, verb, **body):
                return private(lambda s, ctx: owner.run(s, ctx, owner.INPUTS[verb].model_validate(body), verb))
            empty = command(drafts, 'create')
            draft = command(drafts, 'create', header=dict(deposit_to=bank, date='2026-06-03'))
            draft = command(drafts, 'update', draft=draft.id, expected_version=draft.version,
                set_sources=[ordinary], set_additional=[dict(received_from=dict(kind='customer', id=sales['customer']),
                    from_account=sales['income'], amount='1.25')])
            child = command(selections, 'create', draft=draft.id, expected_version=draft.version)
            child = command(selections, 'update', selection=child.id, expected_version=child.version,
                            set_sources=[dict(ordinary, memo_override='Retained selected memo')])
            selected = command(selections, 'accept', selection=child.id, expected_version=child.version,
                               draft=draft.id, expected_draft_version=draft.version)
            draft = selected.draft
            posted = financial(private, dict(operation_key='graph-consumption',
                document=dict(mode='draft', draft=draft.id, expected_version=draft.version)))
            assert posted.current.revision_bank_total == 6125
            edit = command(drafts, 'create', from_deposit=posted.current.id, expected_version=posted.current.version)
            historical = command(drafts, 'create')
            historical = command(drafts, 'update', draft=historical.id, expected_version=historical.version, set_sources=[linked])
            abandoned = command(selections, 'create', draft=historical.id, expected_version=historical.version)
            abandoned = command(selections, 'clear', selection=abandoned.id, expected_version=abandoned.version)
            abandoned = command(selections, 'abandon', selection=abandoned.id, expected_version=abandoned.version)
            historical = command(drafts, 'clear', draft=historical.id, expected_version=historical.version)
            assert historical.summary.source_count == abandoned.source_count == 0
        info = client.company.show(company=COMPANY)
        yield dict(root=w['root'], path=Path(info['path'])/'company.db', cid=info['company_id'], empty=empty.id,
            draft=draft.id, edit=edit.id, selection=child.id, operation=posted.operation_id, deposit=posted.current.id,
            source=ordinary['source'], historical=historical.id, abandoned=abandoned.id, linked=linked['source'])
    finally:
        generator.close()


def test_all_nine_scalar_batch_roots_and_empty_authority(audit_world):
    import sqlalchemy as sa
    from bookflow.storage.engine import open_database
    from bookflow.company import deposit_draft_evidence as evidence, payment_authority as pa, schema
    from bookflow.hub import audit_projection_deposit_drafts as codec
    w = audit_world
    assert set(evidence.AUDIT_ROOTS) == set(codec.MODELS) == set(codec.TABLES) == set(pa._DRAFT_TARGETS)
    assert {k:(v[0],v[2]) for k,v in evidence.AUDIT_ROOTS.items()} == codec.TABLES
    with open_database(w['path'], writable=False) as db:
        before = tuple(db.raw.iterdump())
        for kind, (table, field, _) in evidence.AUDIT_ROOTS.items():
            ids = list(db.conn.execute(sa.select(schema.metadata.tables[table].c[field]).distinct()).scalars())
            assert ids
            grouped = evidence.audit_roots(db, kind, ids)
            for identifier in ids:
                actual = pa.record_transactions(db, kind, identifier)
                assert grouped[identifier] == actual
                assert isinstance(actual, set)
        assert pa.record_transactions(db, 'deposit_draft', w['empty']) == set()
        assert pa.record_transactions(db, 'deposit_draft', w['draft']) == {w['source'], w['deposit']}
        assert pa.record_transactions(db, 'deposit_draft', w['edit']) == {w['source'], w['deposit']}
        assert pa.record_transactions(db, 'deposit_draft', w['historical']) == {w['linked']}
        assert pa.record_transactions(db, 'deposit_selection', w['abandoned']) == {w['linked']}
        events = [r[0] for r in db.raw.execute("SELECT DISTINCT event_id FROM audit_entries WHERE record_type LIKE 'deposit_draft%' OR record_type LIKE 'deposit_selection%'")]
        cohort = pa._EventCohort(db, events)
        for event in events:
            expected = pa.event_requirements(db, event)
            assert expected == cohort.requirements(event)
            assert ('ledger.read', 'member') in expected
        empty_event = db.raw.execute('SELECT audit_event_id FROM deposit_drafts WHERE id=?', (w['empty'],)).fetchone()[0]
        assert pa.event_requirements(db, empty_event) == (('ledger.read', 'member'),)
        assert cohort.requirements(empty_event) == (('ledger.read', 'member'),)
        for kind, owner in (('deposit_draft', w['historical']), ('deposit_selection', w['abandoned'])):
            event = db.raw.execute('SELECT event_id FROM audit_entries WHERE record_type=? AND record_id=? ORDER BY rowid LIMIT 1', (kind, owner)).fetchone()[0]
            assert pa.event_requirements(db, event) == cohort.requirements(event) == (('ledger.read', 'member'), ('customer-work', 'member'))
        assert pa.record_transactions(db, 'customer', 'legitimate-nontransaction') == set()
        assert pa.record_transactions(db, 'unknown-kind', 'unknown') == set()
        assert tuple(db.raw.iterdump()) == before


@pytest.mark.parametrize('kind', ('deposit_draft', 'deposit_selection', 'deposit_draft_revision',
    'deposit_selection_revision', 'deposit_draft_row_key', 'deposit_draft_source', 'deposit_selection_source',
    'deposit_draft_additional', 'deposit_draft_consumption'))
def test_missing_known_roots_fail_closed_without_identity_codes(audit_world, kind):
    from bookflow.storage.engine import open_database
    from bookflow.company import payment_authority as pa
    with open_database(audit_world['path'], writable=False) as db:
        with pytest.raises(BookflowError) as caught:
            pa.record_transactions(db, kind, '00000000000000000000000000')
        assert caught.value.code == 'E_PERMISSION'
        assert caught.value.details == {'reason':'unresolved_payment_evidence'}
        cohort = pa._EventCohort(db, [])
        cohort._load(kind, ['00000000000000000000000000'])
        with pytest.raises(BookflowError) as batch:
            cohort._walk((kind, '00000000000000000000000000'))
        assert batch.value.code == caught.value.code and batch.value.details == caught.value.details


@pytest.mark.parametrize('fault', ('revision_owner', 'key_ordinal', 'source_revision', 'consumption_head', 'row_kind'))
def test_required_relations_fail_closed_in_both_walkers(audit_world, monkeypatch, fault):
    from bookflow.storage.engine import open_database
    from bookflow.company import deposit_draft_evidence as evidence, payment_authority as pa
    w = audit_world
    with open_database(w['path'], writable=False) as db:
        rows = db.raw.execute('SELECT row_id,revision_id FROM deposit_draft_sources WHERE draft_id=? ORDER BY revision_id', (w['draft'],)).fetchall()
        row_id = rows[0][0]
        assert sum(r[0] == row_id for r in rows) > 1
        # Corrupt the last retained occurrence in the loader's detached facts;
        # never change storage or install a fake Session/identity/policy.
        revision = [r[1] for r in rows if r[0] == row_id][-1]
        original = evidence._AuditEvidence.get
        def damaged(self, table, field, ids):
            values = {k:[dict(r) for r in rs] for k,rs in original(self, table, field, ids).items()}
            for group in values.values():
                for row in group:
                    if fault == 'revision_owner' and table == 'deposit_draft_sources' and row['revision_id'] == revision:
                        row['draft_id'] = w['empty']
                    elif fault == 'key_ordinal' and table == 'deposit_draft_row_keys' and row['id'] == row_id:
                        row['ordinal'] += 100
                    elif fault == 'row_kind' and table == 'deposit_draft_sources' and row['revision_id'] == revision:
                        row['kind'] = 'additional'
                    elif fault == 'source_revision' and table == 'deposit_draft_sources' and row['revision_id'] == revision:
                        row['source_revision_id'] = w['empty']
                    elif fault == 'consumption_head' and table == 'deposit_drafts' and row['id'] == w['draft']:
                        row['consumed_revision_id'] = w['empty']
            return values
        monkeypatch.setattr(evidence._AuditEvidence, 'get', damaged)
        with pytest.raises(BookflowError) as caught:
            pa.record_transactions(db, 'deposit_draft_source', row_id)
        assert caught.value.code == 'E_PERMISSION' and caught.value.details == {'reason':'unresolved_payment_evidence'}
        cohort = pa._EventCohort(db, [])
        cohort._load('deposit_draft_source', [row_id])
        with pytest.raises(BookflowError) as caught:
            cohort._walk(('deposit_draft_source', row_id))
        assert caught.value.code == 'E_PERMISSION' and caught.value.details == {'reason':'unresolved_payment_evidence'}


def test_batched_adapter_queries_are_keyed_and_capped(audit_world):
    import sqlalchemy as sa
    from bookflow.storage.engine import open_database
    from bookflow.company import deposit_draft_evidence as evidence
    with open_database(audit_world['path'], writable=False) as db:
        statements = []
        def observe(conn, cursor, statement, parameters, context, many):
            statements.append((statement, parameters))
        sa.event.listen(db.conn, 'before_cursor_execute', observe)
        try:
            ids = [audit_world['empty'], *(f'{i:026d}' for i in range(201))]
            result = evidence.audit_roots(db, 'deposit_draft', ids)
        finally:
            sa.event.remove(db.conn, 'before_cursor_execute', observe)
        assert result[audit_world['empty']] == set()
        assert all(isinstance(result[k], BookflowError) for k in ids[1:])
        assert len(statements[0][1]) == 200 and len(statements[1][1]) == 2
        assert all(' WHERE ' in sql.replace('\n', ' ') and len(values) <= 200 for sql, values in statements)
        statements.clear()
        roots = [audit_world[k] for k in ('empty', 'draft', 'historical', 'edit')]
        sa.event.listen(db.conn, 'before_cursor_execute', observe)
        try:
            result = evidence.audit_roots(db, 'deposit_draft', roots)
        finally:
            sa.event.remove(db.conn, 'before_cursor_execute', observe)
        owner_reads = [(sql, values) for sql, values in statements if 'WHERE deposit_draft_sources.draft_id IN' in sql]
        assert len(owner_reads) == 1 and set(owner_reads[0][1]) == set(roots)
        assert result[audit_world['empty']] == set()
        assert result[audit_world['historical']] == {audit_world['linked']}
        assert all(len(values) <= 200 for _, values in statements)



def test_historical_work_denial_uses_current_governed_reader(audit_world):
    from bookflow.core.host import Host
    from bookflow.core.publication import OSBinding
    from bookflow.core.context import Context, client_version
    from bookflow.core.config import Config, os_login
    from bookflow.core import identity_admin_binding as binding
    from bookflow.core.publication_audit import open_selected
    from bookflow.hub import audit_projection as projection, identity_admin as admin
    from bookflow.hub.permission_catalog import ScopeKey
    from bookflow.company import payment_authority as pa
    from bookflow.storage.engine import open_database
    from tests.permission_admin_support import CONTEXT
    w = audit_world
    with open_database(w['path'], writable=False) as db:
        event = db.raw.execute("SELECT event_id FROM audit_entries WHERE record_type='deposit_draft' AND record_id=? ORDER BY rowid LIMIT 1", (w['historical'],)).fetchone()[0]
        before = tuple(db.raw.iterdump())
    host = Host(w['root'], version=client_version()); host.start()
    try:
        def check(denied):
            ctx = Context.new('http', 'Historical draft root authority')
            with binding.hosted_reader(host, OSBinding.capture(host, os_login()), request_id=ctx.request_id) as reader:
                open_selected(reader, projection.HistorySelection(mode='show', company=w['cid'], event=event), ctx)
                audience = projection.make_audience(reader)
                scalar = pa.event_requirements(reader.session.company, event)
                batch = pa._EventCohort(reader.session.company, [event]).requirements(event)
                assert scalar == batch == (('ledger.read', 'member'), ('customer-work', 'member'))
                for requirements in (scalar, batch):
                    if denied:
                        with pytest.raises(BookflowError) as caught:
                            audience.require(w['cid'], requirements)
                        assert caught.value.code == 'E_PERMISSION'
                    else:
                        audience.require(w['cid'], requirements)
        check(False)
        uid = Config.load(w['root']/'config.toml').user_table(os_login())['user_id']
        def update(denies):
            with host._commit_hooks.operation('dispatch.apply', host._hub):
                with binding.hosted_operation(host, OSBinding.capture(host, os_login()), request_id=CONTEXT.request_id, purpose='apply') as operation:
                    version = host._hub.raw.execute("SELECT version FROM memberships WHERE user_id=? AND scope_type='company' AND scope_id=?", (uid,w['cid'])).fetchone()[0]
                    operation.apply(admin.PutMembership(uid, ScopeKey('company', w['cid']), admin.Version(version), 'owner', denies=denies), audit=CONTEXT)
                    host._commit_hooks.commit(host._hub, 'dispatch.apply')
        host.submit(lambda: update(('customer-work',)))
        try:
            check(True)
        finally:
            host.submit(lambda: update(()))
        assert host._readers_attached == 0
    finally:
        host.stop()
    with open_database(w['path'], writable=False) as db:
        assert tuple(db.raw.iterdump()) == before
