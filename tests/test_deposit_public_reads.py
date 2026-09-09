"""Public deposit show/items over real produced deposits and real permissions.

Everything here runs through the accepted private financial owners: the deposit
is posted by the genuine lifecycle producer, the reader is a real authenticated
BoundReader, and denials are real membership denies applied through the
identity administration owner.
"""
from contextlib import contextmanager

import pytest

from bookflow.company import deposit_public_authority as pa
from bookflow.company import deposit_public_reads as reads
from bookflow.company import deposit_queries as q
from bookflow.company import deposit_read_authority as private_authority
from bookflow.company import deposit_read_facts as private_facts
from bookflow.company import deposit_read_models as m
from bookflow.company import deposit_read_pages as pages
from bookflow.core.errors import BookflowError

COMPANY = 'Demo Plumbing Co'
ABSENT = '00000000000000000000000000'


@pytest.fixture(scope='module')
def public_world(_seeded_template, tmp_path_factory):
    from bookflow.core.context import client_version
    from bookflow.core.host import Host
    from tests.test_audit_projection_activity import world
    from tests.test_deposit_draft_financial import financial, run_private
    from tests.test_deposit_drafts import cash
    from tests.test_service_sales_lifecycle import sale
    generator = world.__wrapped__(_seeded_template, tmp_path_factory)
    w = next(generator)
    data = {}
    try:
        client = w['client']
        sales = sale.__wrapped__(client)
        receipt = cash.__wrapped__(client, sales)
        bank = client.account.create(name='Public detail bank', type='bank', company=COMPANY)['id']
        till = client.account.create(name='Public detail till', type='other_current_asset', company=COMPANY)['id']
        method = client.run('payment-method create', dict(name='Public detail cash', kind='cash'),
                            company=COMPANY)['id']
        grouping = client.run('class create', dict(name='Public detail class'), company=COMPANY)['id']
        definition = client.run('custom-field create', dict(name='Public detail label', kind='text',
                                                            scopes=['deposit'], default='Captured default'),
                                company=COMPANY)['id']
        document = dict(mode='inline', deposit_to=bank, date='2026-06-03', memo='Public detail memo',
                        sources=[receipt],
                        additional=[
                            dict(received_from=dict(kind='customer', id=sales['customer']),
                                 from_account=sales['income'], amount='10.00', memo='Extra cash',
                                 check_number='2291', payment_method=method, **{'class': grouping}),
                            dict(received_from=dict(kind='customer', id=sales['customer']),
                                 from_account=sales['income'], amount='-2.00', memo='Returned cash')],
                        cash_back=dict(account=till, amount='5.00', memo='Till cash'))
        with pytest.MonkeyPatch.context() as patch:
            private = run_private.__wrapped__(client, patch)
            posted = financial(private, dict(operation_key='public-detail-post', document=document))
        assert posted.current.revision_bank_total == 6300
        note = client.note.add(record_type='transaction', record_id=posted.current.id,
                               body='Deposit annotation', company=COMPANY)['note']
        info = client.company.show(company=COMPANY)
        data.update(root=w['root'], client=client, cid=info['company_id'],
                    deposit=posted.current.id, source=receipt['source'], bank=bank, till=till,
                    grouping=grouping, method=method, definition=definition, note=note['id'],
                    customer=sales['customer'])
        data['host'] = Host(w['root'], version=client_version())
        data['host'].start()
        yield data
    finally:
        if data.get('host') is not None:
            data['host'].stop()
        generator.close()


@contextmanager
def without_host(world):
    """The host owns the data-root lock; ordinary client writes need it released."""
    from bookflow.core.context import client_version
    from bookflow.core.host import Host
    world['host'].stop()
    world['host'] = None
    try:
        yield
    finally:
        world['host'] = Host(world['root'], version=client_version())
        world['host'].start()


@contextmanager
def reading(world):
    """One authenticated reader with its own binding and a sealed audience."""
    from bookflow.core import identity_admin_binding as ib
    from bookflow.core.config import os_login
    from bookflow.core.context import Context
    from bookflow.core.publication import OSBinding
    ctx = Context.new('python', 'Public deposit detail witness')
    admitted = OSBinding.capture(world['host'], os_login())
    with ib.hosted_reader(world['host'], admitted, request_id=ctx.request_id) as reader:
        audience = pa.audience(reader, admitted)
        reads.open_selected(reader, audience, world['cid'], ctx)
        yield reader.session, audience, admitted


def set_denies(world, denies):
    """Real current membership denies through the identity administration owner."""
    from bookflow.core import identity_admin_binding as ib
    from bookflow.core.config import Config, os_login
    from bookflow.core.publication import OSBinding
    from bookflow.hub import identity_admin as admin
    from bookflow.hub.permission_catalog import ScopeKey
    from tests.permission_admin_support import CONTEXT
    host = world['host']
    uid = Config.load(world['root'] / 'config.toml').user_table(os_login())['user_id']

    def update():
        with host._commit_hooks.operation('dispatch.apply', host._hub):
            with ib.hosted_operation(host, OSBinding.capture(host, os_login()),
                                     request_id=CONTEXT.request_id, purpose='apply') as operation:
                version = host._hub.raw.execute(
                    "SELECT version FROM memberships WHERE user_id=? AND scope_type='company' AND scope_id=?",
                    (uid, world['cid'])).fetchone()[0]
                operation.apply(admin.PutMembership(uid, ScopeKey('company', world['cid']),
                                                    admin.Version(version), 'owner', denies=denies),
                                audit=CONTEXT)
                host._commit_hooks.commit(host._hub, 'dispatch.apply')
    host.submit(update)


@contextmanager
def denying(world, denies):
    set_denies(world, denies)
    try:
        yield
    finally:
        set_denies(world, ())


def collect(session, audience, deposit, kind, limit=1):
    rows, cursor = [], None
    while True:
        page = reads.items(session, m.ItemsInput(deposit=deposit, kind=kind,
                                                 page=m.PageInput(limit=limit, cursor=cursor)),
                           audience=audience)
        rows.extend(page.items)
        cursor = page.next_cursor
        if cursor is None:
            return tuple(rows), page


def test_show_projects_the_disclosed_relation_and_never_the_private_guard(public_world):
    world = public_world
    with reading(world) as (session, audience, binding):
        detail = reads.show(session, m.ShowInput(deposit=world['deposit']), audience=audience)
        private = q.show(session, m.ShowInput(deposit=world['deposit']), binding=binding)
    payload = detail.model_dump_json()
    assert detail.company_id == world['cid'] and detail.deposit_id == world['deposit']
    assert detail.currency == private.currency and detail.selected_is_current
    assert detail.totals.bank_total.minor_units == 6300
    assert detail.totals.source_total.minor_units == 6000
    assert detail.totals.positive_additional_total.minor_units == 1000
    assert detail.totals.negative_additional_total.minor_units == -200
    assert detail.totals.cash_back.minor_units == 500
    assert detail.counts.sources == 1 and detail.counts.additional == 2
    assert detail.current.effective_bank_total.minor_units == 6300
    # The private reader issues a real inspection guard; the public wire has none.
    assert private.dependencies.guard and private.dependencies.history == 'complete'
    assert 'guard' not in payload and private.dependencies.guard not in payload
    assert detail.inspection.history == 'complete'
    assert detail.inspection.source_count == len(private.dependencies.source_ids) == 1
    # Private-domain fingerprints and identities never travel.
    assert not any(value in payload for value in private.fingerprints.values())
    assert detail.selected.deposit_to.disclosed and detail.selected.deposit_to.id == world['bank']
    assert detail.selected.issuer.disclosed and detail.selected.issuer.home_currency
    custom = detail.selected.custom_fields
    assert len(custom) == 1 and custom[0].disclosed and custom[0].definition_id == world['definition']
    assert custom[0].canonical_text == 'Captured default'
    assert detail.annotations.notes == 'available' and detail.annotations.attachments == 'available'
    assert [link.id for link in detail.links if link.kind == 'note'] == [world['note']]
    assert [(row.revision_number, row.selected, row.current) for row in detail.revisions] == [(1, True, True)]
    assert detail.current.active_source_count == 1
    # A bounded summary names only its own masters: the bank and the cash-back account.
    assert {row.group for row in detail.current_references} == {'account'}
    assert {row.id for row in detail.current_references} == {world['bank'], world['till']}


def test_items_page_every_kind_over_the_public_continuation_domain(public_world):
    world = public_world
    with reading(world) as (session, audience, binding):
        sources, source_page = collect(session, audience, world['deposit'], 'sources')
        additional, additional_page = collect(session, audience, world['deposit'], 'additional')
        cells, cell_page = collect(session, audience, world['deposit'], 'cash_allocations')
        one = reads.items(session, m.ItemsInput(deposit=world['deposit'], kind='additional',
                                                page=m.PageInput(limit=1)), audience=audience)
        # A public cursor is not a private-domain token and cannot be replayed as one.
        with pytest.raises(BookflowError) as caught:
            pages.decode(session, binding, 'items', one.next_cursor)
        assert caught.value.code == 'E_VALIDATION'
        private = q.items(session, m.ItemsInput(deposit=world['deposit'], kind='additional',
                                                page=m.PageInput(limit=1)), binding=binding)
        assert private.next_cursor != one.next_cursor
    assert len(sources) == source_page.total_count == 1
    assert len(additional) == additional_page.total_count == 2
    assert len(cells) == cell_page.total_count >= 2
    row = sources[0]
    assert row.row == 'source' and row.amount.minor_units == 6000
    assert row.source.source_type == 'sales_receipt' and row.source.transaction_id == world['source']
    assert row.source.number and row.receipt_date == '2026-06-02'
    assert row.payer.disclosed and row.payer.group == 'customer' and row.payer.id == world['customer']
    assert row.from_account.disclosed and row.from_account.id
    assert row.payment_method is not None and row.payment_method.disclosed
    assert [c.ordinal for c in row.components] == sorted(c.ordinal for c in row.components)
    assert row.current.transaction_id == world['source'] and row.current.claimed
    assert row.current.claimed_by_this_deposit
    amounts = sorted(r.amount.minor_units for r in additional)
    assert amounts == [-200, 1000]
    positive = next(r for r in additional if r.amount.minor_units == 1000)
    assert positive.check_number == '2291' and positive.memo == 'Extra cash'
    assert positive.account.disclosed and positive.party.disclosed
    assert positive.class_reference is not None and positive.class_reference.id == world['grouping']
    assert positive.payment_method is not None and positive.payment_method.id == world['method']
    buckets = {r.bucket for r in cells}
    assert buckets <= {'main_bank', 'cash_back', 'additional'} and 'main_bank' in buckets
    for cell in cells:
        assert (cell.additional_row_id is None) == (cell.bucket != 'additional')
        assert cell.amount.minor_units > 0
    assert sum(r.amount.minor_units for r in cells if r.bucket == 'main_bank') == 6300


def test_a_denied_reference_group_redacts_without_dropping_its_amount(public_world):
    world = public_world
    with reading(world) as (session, audience, binding):
        open_detail = reads.show(session, m.ShowInput(deposit=world['deposit']), audience=audience)
        open_rows, _ = collect(session, audience, world['deposit'], 'additional', limit=25)
    with denying(world, ('class', 'payment-method', 'customer', 'account', 'custom-field',
                         'company', 'note', 'attachment')):
        with reading(world) as (session, audience, binding):
            closed_detail = reads.show(session, m.ShowInput(deposit=world['deposit']), audience=audience)
            closed_rows, _ = collect(session, audience, world['deposit'], 'additional', limit=25)
            closed_sources, _ = collect(session, audience, world['deposit'], 'sources', limit=25)
    assert closed_detail.totals == open_detail.totals and closed_detail.counts == open_detail.counts
    assert closed_detail.current == open_detail.current
    assert not closed_detail.selected.deposit_to.disclosed
    assert closed_detail.selected.deposit_to.id is None and closed_detail.selected.deposit_to.name is None
    assert closed_detail.selected.cash_back.amount == open_detail.selected.cash_back.amount
    assert not closed_detail.selected.issuer.disclosed and closed_detail.selected.issuer.display_name is None
    assert [v.disclosed for v in closed_detail.selected.custom_fields] == [False]
    assert closed_detail.selected.custom_fields[0].canonical_text is None
    assert closed_detail.annotations.notes == 'unavailable'
    assert closed_detail.annotations.attachments == 'unavailable'
    assert closed_detail.links == ()
    assert closed_detail.current_references == ()
    assert closed_detail.revisions == open_detail.revisions
    assert [r.amount for r in closed_rows] == [r.amount for r in open_rows]
    assert [r.row_id for r in closed_rows] == [r.row_id for r in open_rows]
    assert all(not r.account.disclosed and r.account.id is None for r in closed_rows)
    assert all(r.party is not None and not r.party.disclosed for r in closed_rows)
    assert closed_sources[0].amount.minor_units == 6000
    assert not closed_sources[0].payer.disclosed and closed_sources[0].payer.id is None
    assert not closed_sources[0].from_account.disclosed
    assert closed_sources[0].current.payment_method_type is None
    assert closed_sources[0].current.claimed and closed_sources[0].current.claimed_by_this_deposit


def test_a_denied_class_rename_moves_the_private_guard_but_not_the_public_output(public_world):
    world = public_world
    denied = ('class',)
    set_denies(world, denied)
    try:
        with reading(world) as (session, audience, binding):
            before = reads.show(session, m.ShowInput(deposit=world['deposit']), audience=audience)
            before_page = reads.items(session, m.ItemsInput(deposit=world['deposit'], kind='additional',
                                                           page=m.PageInput(limit=1)), audience=audience)
            before_guard = q.show(session, m.ShowInput(deposit=world['deposit']),
                                  binding=binding).dependencies.guard
        set_denies(world, ())
        with without_host(world):
            world['client'].run('class update', dict(**{'class': world['grouping']}, expected_version=1,
                                                     name='Public detail class renamed'), company=COMPANY)
        set_denies(world, denied)
        with reading(world) as (session, audience, binding):
            after = reads.show(session, m.ShowInput(deposit=world['deposit']), audience=audience)
            after_page = reads.items(session, m.ItemsInput(deposit=world['deposit'], kind='additional',
                                                          page=m.PageInput(limit=1)), audience=audience)
            after_guard = q.show(session, m.ShowInput(deposit=world['deposit']),
                                 binding=binding).dependencies.guard
    finally:
        set_denies(world, ())
    assert before_guard and after_guard and before_guard != after_guard
    assert before.model_dump(exclude={'current_observed_at'}) == after.model_dump(exclude={'current_observed_at'})
    assert before_page.fingerprint == after_page.fingerprint
    assert before_page.next_cursor == after_page.next_cursor
    assert before.inspection.history == after.inspection.history == 'complete'


def test_an_absent_deposit_and_a_denied_company_capability_stay_non_disclosing(public_world):
    world = public_world
    with reading(world) as (session, audience, binding):
        with pytest.raises(BookflowError) as caught:
            reads.show(session, m.ShowInput(deposit=ABSENT), audience=audience)
        assert caught.value.code == 'E_RECORD_NOT_FOUND' and not caught.value.details
        with pytest.raises(BookflowError) as caught:
            reads.items(session, m.ItemsInput(deposit=ABSENT, kind='sources'), audience=audience)
        assert caught.value.code == 'E_RECORD_NOT_FOUND'
    with denying(world, ('ledger.read',)):
        from bookflow.core import identity_admin_binding as ib
        from bookflow.core.config import os_login
        from bookflow.core.context import Context
        from bookflow.core.publication import OSBinding
        ctx = Context.new('python', 'Denied ledger read witness')
        admitted = OSBinding.capture(world['host'], os_login())
        with ib.hosted_reader(world['host'], admitted, request_id=ctx.request_id) as reader:
            audience = pa.audience(reader, admitted)
            with pytest.raises(BookflowError) as caught:
                reads.open_selected(reader, audience, world['cid'], ctx)
            assert caught.value.code == 'E_PERMISSION'


def test_the_audience_requires_its_own_reader_and_matching_binding(public_world):
    from pathlib import Path
    from bookflow.core import identity_admin_binding as ib
    from bookflow.core.config import os_login
    from bookflow.core.context import Context
    from bookflow.core.publication import OSBinding
    world = public_world
    ctx = Context.new('python', 'Sealed audience witness')
    admitted = OSBinding.capture(world['host'], os_login())
    with ib.hosted_reader(world['host'], admitted, request_id=ctx.request_id) as reader:
        with pytest.raises(BookflowError) as caught:
            pa.DepositAudience(reader, admitted)
        assert caught.value.code == 'E_UNAUTHENTICATED'
        for bad in (None, object(), reader):
            with pytest.raises(BookflowError) as caught:
                pa.audience(reader, bad)
            assert caught.value.code == 'E_UNAUTHENTICATED'
        with pytest.raises(BookflowError):
            pa.audience(object(), admitted)
        foreign = OSBinding(Path('/nonexistent-root'), admitted.user_id, admitted.login,
                            admitted.actor_kind, admitted.hub_admin, None, None)
        with pytest.raises(BookflowError) as caught:
            pa.audience(reader, foreign)
        assert caught.value.code == 'E_UNAUTHENTICATED'
        other = OSBinding(admitted.root, 'someone-else', admitted.login, admitted.actor_kind,
                          admitted.hub_admin, None, None)
        with pytest.raises(BookflowError):
            pa.audience(reader, other)


# ---------------------------------------------------------------- enforcement


@pytest.fixture(scope='module')
def work_linked(public_world):
    """A deposit whose connected graph carries a real customer-work requirement."""
    from tests.test_deposit_draft_financial import financial, run_private
    from tests.test_work_billing_lifecycle import accepted, bill
    world = public_world
    with without_host(world):
        client = world['client']
        sales = {'customer': world['customer'], 'item': 'Sale witness service'}
        invoice = bill(client, accepted(client, sales), key='enforcement-work')
        payment = client.run('payment receive', dict(
            customer=world['customer'], date='2026-06-02', amount='1.00',
            payment_method=world['method'], operation_key='enforcement-work-pay',
            applications=dict(mode='inline', items=[dict(invoice=invoice['id'], expected_version=1,
                                                         amount='1.00')])), company=COMPANY)
        with pytest.MonkeyPatch.context() as patch:
            private = run_private.__wrapped__(client, patch)
            posted = financial(private, dict(operation_key='enforcement-work-deposit', document=dict(
                mode='inline', deposit_to=world['bank'], date='2026-06-03',
                sources=[dict(source_type='payment', source=payment['id'], expected_version=1)])))
    return dict(deposit=posted.current.id, payment=payment['id'])


def test_a_denied_connected_work_capability_is_enforced_before_any_decode(public_world, work_linked):
    """Reviewer BLOCK 1: legacy require_resource never applies granular denies."""
    from bookflow.company import payment_authority
    world = public_world
    deposit = work_linked['deposit']
    with reading(world) as (session, audience, binding):
        allowed = reads.show(session, m.ShowInput(deposit=deposit), audience=audience)
        assert allowed.deposit_id == deposit
        required = payment_authority.requirements(session.company, [work_linked['payment']])
        assert ('customer-work', 'member') in required
    with denying(world, ('customer-work',)):
        with reading(world) as (session, audience, binding):
            # The retained legacy check still admits; the public boundary must not.
            assert not audience.admits('customer-work')
            assert payment_authority.requirements(session.company, [work_linked['payment']]) == required
            assert ('customer-work', 'member') in reads.graph_requirements(
                session, private_authority.selected(session, deposit, binding=binding))
            for call in (lambda: reads.show(session, m.ShowInput(deposit=deposit), audience=audience),
                         lambda: reads.items(session, m.ItemsInput(deposit=deposit, kind='sources'),
                                             audience=audience),
                         lambda: reads.items(session, m.ItemsInput(deposit=deposit, kind='cash_allocations'),
                                             audience=audience)):
                with pytest.raises(BookflowError) as caught:
                    call()
                # Exact-read non-disclosure: identical to an absent record.
                assert caught.value.code == 'E_RECORD_NOT_FOUND' and not caught.value.details
            # A deposit with no work-linked dependency stays readable for the same reader.
            assert reads.show(session, m.ShowInput(deposit=world['deposit']),
                              audience=audience).deposit_id == world['deposit']


def test_denied_annotation_kinds_are_never_acquired(public_world, monkeypatch):
    """Reviewer BLOCK 2: the capability decision precedes association access."""
    from bookflow.company import deposit_read_facts
    world = public_world
    seen = []
    original = deposit_read_facts._associations

    def observed(s, identity, links, kinds=('note', 'attachment')):
        seen.append(tuple(kinds))
        return original(s, identity, links, kinds)
    monkeypatch.setattr(deposit_read_facts, '_associations', observed)
    with denying(world, ('note', 'attachment')):
        with reading(world) as (session, audience, binding):
            detail = reads.show(session, m.ShowInput(deposit=world['deposit']), audience=audience)
            assert detail.links == ()
            assert detail.annotations.notes == detail.annotations.attachments == 'unavailable'
    assert seen == [], 'Denied annotation associations were acquired before the access decision'
    with denying(world, ('attachment',)):
        with reading(world) as (session, audience, binding):
            detail = reads.show(session, m.ShowInput(deposit=world['deposit']), audience=audience)
            assert detail.annotations.notes == 'available'
            assert detail.annotations.attachments == 'unavailable'
            assert [link.kind for link in detail.links] == ['note']
    assert seen == [('note',)], seen
    seen.clear()
    with reading(world) as (session, audience, binding):
        reads.items(session, m.ItemsInput(deposit=world['deposit'], kind='sources'), audience=audience)
    assert seen == [], 'Item pages emit no annotation link and must acquire none'


REFERENCE_CASES = (
    ('account', 'accounts'),
    ('customer', 'customers'),
    ('class', 'classes'),
    ('payment-method', 'payment_methods'),
)


def _groups_disclosed(detail, additional, sources):
    row = next(r for r in additional if r.amount.minor_units == 1000)
    return {
        'accounts': detail.selected.deposit_to.disclosed and row.account.disclosed
                    and sources[0].from_account.disclosed,
        'customers': row.party.disclosed and sources[0].payer.disclosed,
        'classes': row.class_reference.disclosed,
        'payment_methods': row.payment_method.disclosed and sources[0].payment_method.disclosed,
    }


def test_the_projector_enforces_each_declared_reference_group_independently(public_world):
    """Enforcement, not declaration: one denied group redacts only its own fields."""
    world = public_world
    with reading(world) as (session, audience, binding):
        detail = reads.show(session, m.ShowInput(deposit=world['deposit']), audience=audience)
        additional, _ = collect(session, audience, world['deposit'], 'additional', limit=25)
        sources, _ = collect(session, audience, world['deposit'], 'sources', limit=25)
    assert all(_groups_disclosed(detail, additional, sources).values())
    for capability, table in REFERENCE_CASES:
        with denying(world, (capability,)):
            with reading(world) as (session, audience, binding):
                assert not audience.reference_admitted(table)
                detail = reads.show(session, m.ShowInput(deposit=world['deposit']), audience=audience)
                additional, page = collect(session, audience, world['deposit'], 'additional', limit=25)
                sources, _ = collect(session, audience, world['deposit'], 'sources', limit=25)
        state = _groups_disclosed(detail, additional, sources)
        assert state[table] is False, (capability, state)
        assert all(value for name, value in state.items() if name != table), (capability, state)
        assert all(row.group != pa.REFERENCE_GROUPS[table] for row in detail.current_references)
        assert all(row.group != pa.REFERENCE_GROUPS[table] for row in page.current_references)
        if capability == 'payment-method':
            assert sources[0].current.payment_method_type is None
    with denying(world, ('custom-field',)):
        with reading(world) as (session, audience, binding):
            assert not reads.show(session, m.ShowInput(deposit=world['deposit']),
                                  audience=audience).selected.custom_fields[0].disclosed
    with denying(world, ('company',)):
        with reading(world) as (session, audience, binding):
            assert not reads.show(session, m.ShowInput(deposit=world['deposit']),
                                  audience=audience).selected.issuer.disclosed


def test_the_summary_and_each_page_carry_only_the_references_they_name(public_world):
    """Neither surface grows with the connected graph."""
    world = public_world
    with reading(world) as (session, audience, binding):
        detail = reads.show(session, m.ShowInput(deposit=world['deposit']), audience=audience)
        whole = reads.items(session, m.ItemsInput(deposit=world['deposit'], kind='additional',
                                                  page=m.PageInput(limit=25)), audience=audience)
        first = reads.items(session, m.ItemsInput(deposit=world['deposit'], kind='additional',
                                                  page=m.PageInput(limit=1)), audience=audience)
        second = reads.items(session, m.ItemsInput(
            deposit=world['deposit'], kind='additional',
            page=m.PageInput(limit=1, cursor=first.next_cursor)), audience=audience)
        sources = reads.items(session, m.ItemsInput(deposit=world['deposit'], kind='sources',
                                                    page=m.PageInput(limit=25)), audience=audience)
        cells = reads.items(session, m.ItemsInput(deposit=world['deposit'], kind='cash_allocations',
                                                  page=m.PageInput(limit=25)), audience=audience)
        complete = private_facts.load_complete(session, [world['deposit']], binding=binding,
                                               annotations=())[0]
        graph = len({(row.kind, row.id) for row in complete.references})
    # The show summary names only the bank and cash-back accounts, not the whole graph.
    assert {row.id for row in detail.current_references} == {world['bank'], world['till']}
    assert len(detail.current_references) < graph
    # Allocation rows name no master at all.
    assert cells.current_references == ()
    # A page names only its own rows' masters, and the whole collection still
    # names fewer than the connected graph.
    assert len(whole.current_references) < graph and len(sources.current_references) < graph
    assert {row.id for row in first.current_references} <= {row.id for row in whole.current_references}
    assert len(second.current_references) < len(whole.current_references)
    assert ({row.id for row in first.current_references} | {row.id for row in second.current_references}
            == {row.id for row in whole.current_references})
    # The class and method belong to the positive row only; the negative row omits them.
    assert world['grouping'] in {row.id for row in first.current_references}
    assert world['grouping'] not in {row.id for row in second.current_references}
    assert world['method'] not in {row.id for row in second.current_references}
    # The deposit bank account is a summary master, never a source-row master.
    assert world['bank'] not in {row.id for row in sources.current_references}
