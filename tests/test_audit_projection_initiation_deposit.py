"""Real no-effect deposit consumption and owner denial for direct initiation."""
import bookflow
import pytest

from bookflow.company import deposit_drafts as drafts
from bookflow.company import deposit_draft_models as models
from tests.test_audit_projection_draft_owners import draft_history
from tests.test_audit_projection_coordinate_events import hosted, denied
from tests.test_audit_projection_draft_disclosure import project, complete
from tests.test_audit_projection_initiation import events, read, direct_only, ordinary
from tests.test_audit_projection_activity import world
from tests.test_deposit_draft_financial import run_private, financial
from bookflow.storage.engine import open_database


@pytest.fixture(scope='module')
def no_effect(draft_history):
    c = draft_history
    with open_database(c['path'], writable=False) as db:
        original = db.raw.execute("SELECT transaction_id FROM deposit_operations WHERE operation_key='codec-consume'").fetchone()[0]
        edit = db.raw.execute('SELECT id,version FROM deposit_drafts WHERE edit_transaction_id=?', (original,)).fetchone()
    with pytest.MonkeyPatch.context() as patch:
        private = run_private.__wrapped__(bookflow.connect(data_root=str(c['root'])), patch)
        body = dict(deposit=original, expected_version=1, operation_key='initiation-deposit-no-effect',
                    document=dict(mode='draft', draft=edit[0], expected_version=edit[1]))
        result = financial(private, body, 'update')
        assert result.changed is False and result.current.revision_bank_total == 6025
        before = events(c, 'deposit update')
        replay = financial(private, body, 'update')
        assert replay.idempotent_replay and replay.effect == result.effect
        assert events(c, 'deposit update') == before
        # Cover the missing direct draft lifecycle verbs on an independent empty draft.
        draft = private(lambda s,ctx: drafts.run(s,ctx,models.DraftCreate(),'create'))
        private(lambda s,ctx: drafts.run(s,ctx,models.DraftRef(draft=draft.id,expected_version=draft.version),'abandon'))
    return c


def test_no_effect_consumed_deposit_receipt_is_direct(no_effect):
    c = no_effect
    with hosted(c) as host:
        value = read(c, host, events(c, 'deposit update')[-1], 'deposit update')
        assert not any(e.identity.kind == 'transaction' for e in value.entries)
        receipt = next(e.after for e in value.entries if e.identity.kind == 'deposit_operation')
        assert receipt.effect_snapshot.changed is False
        assert receipt.effect_snapshot.current.revision_bank_total == 6025
        assert direct_only(value, 'deposit update')


def test_abandoned_draft_retains_direct_command(no_effect):
    c = no_effect
    with hosted(c) as host:
        value = read(c, host, events(c, 'deposit draft abandon')[-1], 'deposit draft abandon')
        direct_only(value, 'deposit draft abandon')


# Same governed B2 restoration measured ~75s in the frozen party-undo witness.
@pytest.mark.timeout(120)
@pytest.mark.parametrize('command', ('deposit draft abandon', 'deposit update'))
def test_current_ledger_denial_hides_draft_and_consumed_receipt(no_effect, command):
    # One release owner per case: the combined two-event case measured135.77s.
    c = no_effect; event = events(c, command)[-1]
    with hosted(c) as host:
        read(c, host, event, command, bounded=True)
        with denied(c, host, ('ledger.read',)):
            read(c, host, event, command, visible=False, bounded=True)


@pytest.fixture(scope='module')
def more_ordinary(ordinary):
    from tests.test_service_sales_lifecycle import sale
    from tests.test_payment_receipts import posted
    client = bookflow.connect(data_root=str(ordinary['root']))
    data = sale.__wrapped__(client)
    invoice = posted(client, data['customer'], data['item'], '1.50', 'INITIATION-INVOICE')
    args = dict(invoice=invoice['id'], expected_version=1, operation_key='initiation-invoice-no-effect')
    result = client.run('invoice update', args, reason='Retain invoice', company='Demo Plumbing Co')
    assert result['changed'] is False and result['settlement']['changed'] is False
    before = events(ordinary, 'invoice update')
    assert client.run('invoice update', args, reason='Retain invoice', company='Demo Plumbing Co')['idempotent_replay']
    assert events(ordinary, 'invoice update') == before
    return ordinary


def test_invoice_no_effect_receipt_is_direct(more_ordinary):
    from bookflow.hub import audit_projection_initiation as initiation
    c = more_ordinary
    with hosted(c) as host:
        value = read(c, host, events(c, 'invoice update')[-1], 'invoice update')
        assert {e.identity.kind for e in value.entries} == {'payment_operation'}
        assert initiation.direct_command('invoice update', value.entries) == 'invoice update'
        assert initiation.direct_command('payment update', value.entries) is None


# Bound actual B2 change/restoration only; global/default reader tests stay60s.
@pytest.mark.timeout(120)
@pytest.mark.parametrize('command,capability', (('directive add', 'directive'), ('attachment add', 'attachment'),
                                              ('company compact', 'attachment'), ('rate set', 'ledger.read')))
def test_annotation_owner_denial_removes_filtered_event(ordinary, command, capability):
    c = ordinary; event = events(c, command)[-1]
    with hosted(c) as host:
        # Full positive command-list equality already belongs to the ordinary
        # producer cases; do not repeat that scan around each B2 transition.
        allowed = complete(c, project(c, host, event))
        assert allowed.command == command
        with denied(c, host, (capability,)):
            read(c, host, event, command, visible=False, bounded=True)
        assert project(c, host, event) == allowed
