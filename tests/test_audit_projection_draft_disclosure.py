"""Real retained draft events through fresh governed private history readers."""
import pytest
from bookflow.core import identity_admin_binding as binding
from bookflow.core.config import os_login
from bookflow.core.context import Context
from bookflow.core.publication import OSBinding
from bookflow.core.publication_audit import open_selected
from bookflow.hub import audit_projection as projection
from bookflow.storage.engine import open_database
from tests.test_deposit_draft_evidence import audit_world
from tests.test_audit_projection_coordinate_events import hosted, denied
from tests.test_audit_projection_deposit_inline_masking import captures
from tests.test_audit_projection_activity import world


def project(c, host, event):
    ctx = Context.new('http', 'Draft disclosure witness')
    with binding.hosted_reader(host, OSBinding.capture(host, os_login()), request_id=ctx.request_id) as reader:
        open_selected(reader, projection.HistorySelection(mode='show', company=c['cid'], event=event), ctx)
        return projection.project_event(projection.make_audience(reader), event, company=c['cid'])


def listing(c, host, kind, identity):
    ctx = Context.new('http', 'Bounded draft history witness')
    selection = projection.HistorySelection(mode='list', company=c['cid'], record_type=kind,
                                           record_id=identity, limit=100)
    with binding.hosted_reader(host, OSBinding.capture(host, os_login()), request_id=ctx.request_id) as reader:
        open_selected(reader, selection, ctx)
        page = projection.project_history(projection.make_audience(reader), selection)
        assert len(page.events) <= 100 and not page.has_more
        return page.events


def entries(c, event):
    with open_database(c['path'], writable=False) as db:
        return set(db.raw.execute('SELECT id,record_type,record_id,action FROM audit_entries WHERE event_id=?', (event,)))


def complete(c, value):
    assert value is not None
    assert {(e.id, e.identity.kind, e.identity.id, e.action) for e in value.entries} == entries(c, value.id)
    return value


def first(c, kind, identity):
    with open_database(c['path'], writable=False) as db:
        return db.raw.execute('SELECT event_id FROM audit_entries WHERE record_type=? AND record_id=? ORDER BY rowid LIMIT 1', (kind, identity)).fetchone()[0]


def test_actual_retained_world_ready(audit_world):
    # Separate real producer cost from governed disclosure cases under default60s.
    assert len({audit_world[k] for k in ('empty', 'draft', 'edit', 'historical', 'abandoned')}) == 5


# Real governed B2 change and restoration exceeded60s in second.xml.
# Bound only these correctness cases; global/runtime deadlines stay unchanged.
@pytest.mark.timeout(120)
@pytest.mark.parametrize('denial,keys', [('ledger.read', ('empty','historical')), ('customer-work', ('historical','abandoned'))])
def test_current_denial_hides_complete_old_events(audit_world, denial, keys):
    c = audit_world
    events = [first(c, 'deposit_selection' if k == 'abandoned' else 'deposit_draft', c[k]) for k in keys]
    with hosted(c) as host:
        allowed = [complete(c, project(c, host, event)) for event in events]
        with denied(c, host, (denial,)):
            assert all(project(c, host, event) is None for event in events)
            if denial == 'customer-work':
                complete(c, project(c, host, first(c, 'deposit_draft', c['empty'])))
        assert [project(c, host, event) for event in events] == allowed


@pytest.mark.timeout(120)
@pytest.mark.parametrize('key,kind', [('historical','deposit_draft'), ('abandoned','deposit_selection')])
def test_removed_work_source_current_denial_in_actual_list(audit_world, key, kind):
    c = audit_world
    with hosted(c) as host:
        allowed = listing(c, host, kind, c[key])
        with open_database(c['path'], writable=False) as db:
            expected = {r[0] for r in db.raw.execute('SELECT DISTINCT event_id FROM audit_entries WHERE record_type=? AND record_id=?', (kind,c[key]))}
        assert expected and {e.id for e in allowed} == expected
        for event in allowed:
            complete(c, event)
        with denied(c, host, ('customer-work',)):
            assert listing(c, host, kind, c[key]) == ()


@pytest.fixture(scope='module')
def rich(captures, world):
    # Existing real inline post/update then edit-draft capture has populated bank,
    # cash back, selected vendor, payment method, and captured custom assertions.
    from tests.test_deposit_draft_financial import run_private
    from bookflow.company import deposit_selection as selections
    from bookflow.company.deposit_draft_models import SelectionCreate
    c = dict(captures)
    with open_database(c['path'], writable=False) as db:
        draft = db.raw.execute('SELECT id,version FROM deposit_drafts WHERE audit_event_id=?', (c['manifest_event'][0],)).fetchone()
    with pytest.MonkeyPatch.context() as patch:
        run = run_private.__wrapped__(world['client'], patch)
        selected = run(lambda s, ctx: selections.run(s, ctx, SelectionCreate(draft=draft[0], expected_version=draft[1]), 'create'))
    c['selection_event'] = first(c, 'deposit_selection', selected.id)
    return c


def test_actual_rich_edit_and_selection_ready(rich):
    assert rich['manifest']['additional'][0]['received_from']['kind'] == 'vendor'
    assert rich['manifest']['header']['custom_fields'][rich['custom']]['canonical_text'] == 'Replacement value'
    assert rich['selection_event'] != rich['manifest_event'][0]


def after(event, kind):
    values = [e.after.model_dump(mode='json', by_alias=True) for e in event.entries if e.identity.kind == kind and e.after is not None]
    assert values, kind
    return values


def test_real_edit_reference_and_custom_masks_preserve_money(rich):
    c = rich; event = c['manifest_event'][0]
    with hosted(c) as host:
        allowed = complete(c, project(c, host, event))
        with denied(c, host, ('account','payment-method','custom-field')):
            hidden = complete(c, project(c, host, event))
        yes = after(allowed, 'deposit_draft_revision')[0]['snapshot']
        no = after(hidden, 'deposit_draft_revision')[0]['snapshot']
        assert yes['header']['bank']['id'] == c['bank']
        assert yes['header']['cash_back']['account']['id'] == c['till']
        assert yes['additional'][0]['payment_method']['id'] == c['method']
        assert yes['header']['custom_fields'][c['custom']]['canonical_text'] == 'Replacement value'
        assert no['header']['bank'] is None and no['header']['cash_back']['account'] is None
        assert no['header']['custom_fields'] is None
        assert no['additional'][0]['account'] is None and no['additional'][0]['payment_method'] is None
        assert no['summary'] == yes['summary']
        assert no['summary']['bank_total'] == 6025 and no['summary']['source_total'] == 6000
        assert no['additional'][0]['units'] == yes['additional'][0]['units'] == 225
        assert no['header']['cash_back']['units'] == yes['header']['cash_back']['units'] == 200
        assert 'deposit_to' not in no['header']['origins'] and 'account' not in no['header']['cash_back']['origins']
        assert not {'from_account','payment_method'} & set(no['additional'][0]['origins'])
        extra = after(hidden, 'deposit_draft_additional')[0]
        assert extra['account_id'] is None and extra['payment_method_id'] is None and extra['amount_minor_units'] == 225
        assert after(allowed, 'deposit_draft_additional')[0]['payment_method_id'] == c['method']
        for kind in ('deposit_draft_source',):
            source = after(hidden, kind)[0]['snapshot']['source']
            original_source = after(allowed, kind)[0]['snapshot']['source']
            assert original_source['profile']['control_account']['id'] is not None
            assert original_source['profile']['payment_method']['id'] is not None
            assert source['profile']['control_account']['id'] is None
            assert source['profile']['payment_method'] is None
            assert source['uf_account'] is None and source['cash_minor_units'] == 6000


def test_selected_vendor_not_gated_by_unrelated_party_kinds(rich):
    c = rich; event = c['manifest_event'][0]
    with hosted(c) as host:
        allowed = complete(c, project(c, host, event))
        expected = after(allowed, 'deposit_draft_additional')[0]
        assert expected['party_kind'] == 'vendor' and expected['vendor_id'] is not None
        with denied(c, host, ('customer','employee','other-name')):
            visible = complete(c, project(c, host, event))
        actual = after(visible, 'deposit_draft_additional')[0]
        assert (actual['party_kind'],actual['vendor_id']) == ('vendor',expected['vendor_id'])
        assert actual['snapshot']['received_from'] == expected['snapshot']['received_from']
        assert actual['snapshot']['party_name'] == expected['snapshot']['party_name'] is not None


def test_selected_vendor_denial_masks_scalar_and_nested_party(rich):
    c = rich; event = c['manifest_event'][0]
    with hosted(c) as host:
        original = complete(c, project(c, host, event))
        assert after(original, 'deposit_draft_additional')[0]['party_kind'] == 'vendor'
        assert after(original, 'deposit_draft_revision')[0]['snapshot']['additional'][0]['received_from']['kind'] == 'vendor'
        with denied(c, host, ('vendor',)):
            hidden = complete(c, project(c, host, event))
        extra = after(hidden, 'deposit_draft_additional')[0]
        assert extra['party_kind'] is None and extra['vendor_id'] is None
        assert extra['snapshot']['received_from'] is None
        assert extra['snapshot']['party_name'] is None and extra['amount_minor_units'] == 225
        manifest = after(hidden, 'deposit_draft_revision')[0]['snapshot']
        assert manifest['additional'][0]['received_from'] is None
        assert manifest['additional'][0]['party_name'] is None
        assert 'received_from' not in manifest['additional'][0]['origins']


def test_real_selection_source_customer_and_account_masks(rich):
    c = rich; event = c['selection_event']
    with hosted(c) as host:
        allowed = complete(c, project(c, host, event))
        yes = after(allowed, 'deposit_selection_source')[0]['snapshot']['source']
        assert yes['uf_account'] is not None and yes['profile']['customer']['id'] is not None
        with denied(c, host, ('account','customer')):
            hidden = complete(c, project(c, host, event))
        no = after(hidden, 'deposit_selection_source')[0]['snapshot']['source']
        assert no['uf_account'] is None and no['profile']['customer']['id'] is None
        assert yes['cash_minor_units'] == no['cash_minor_units'] == 6000
        manifest = after(hidden, 'deposit_selection_revision')[0]['snapshot']
        assert manifest['sources'][0]['source']['profile']['customer']['id'] is None
        assert manifest['summary']['source_total'] == 6000
