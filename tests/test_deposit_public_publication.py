"""The sealed public deposit proof, and what release does and does not refuse.

The load-bearing property here is negative: a change this reader could never see
must not produce a release refusal, because a refusal is observable and would
disclose the hidden change through the release path. Every witness below runs
against real produced deposits, real membership denies and real authenticated
readers; nothing is simulated.
"""
import contextlib
import copy
import dataclasses
import json

import pytest

from bookflow.company import deposit_public_authority as pa
from bookflow.company import deposit_public_reads as reads
from bookflow.company import deposit_queries as q
from bookflow.company import deposit_read_models as m
from bookflow.core import publication_deposit as owner
from bookflow.core import registry
from bookflow.core.context import Context
from bookflow.core.deposit_request import DepositRequest
from bookflow.core.errors import BookflowError
from bookflow.core.publication import OSBinding, PublicationPermit
from tests import deposit_public_support as support
from tests.deposit_public_support import COMPANY, denying, set_denies

FIXED = '2026-06-09T12:00:00+00:00'


@pytest.fixture(scope='module')
def world(public_deposit_world):
    support.start(public_deposit_world, serving=True)
    try:
        yield public_deposit_world
    finally:
        support.stop(public_deposit_world)


def request_for(world, command, **raw):
    return DepositRequest.capture(command, world['cid'],
                                  registry.REGISTRY[command].input_model.model_validate(raw))


class Prepared:
    """One executed request plus the permit that will publish it."""

    def __init__(self, world, command, at=FIXED, **raw):
        from bookflow.core import identity_admin_binding as ib
        self.ctx = Context.new('python', 'Public deposit publication witness')
        self.command = registry.REGISTRY[command]
        from bookflow.core.config import os_login
        self.binding = OSBinding.capture(world['host'], os_login())
        self.request = request_for(world, command, **raw)
        with ib.hosted_reader(world['host'], self.binding, request_id=self.ctx.request_id) as reader:
            identity = reader.authenticate()
            self.result, self.proof = owner.execute_detail(reader, self.request, self.binding,
                                                           ctx=self.ctx, at=at)
            self.permit = PublicationPermit(self.command, None, self.ctx,
                                            (identity.actor, identity.actor_kind, identity.hub_admin),
                                            frozenset(), None, None)
            self.permit.finish(reader.session, succeeded=self.proof.failure is None,
                               result=self.result, deposit_proof=self.proof)

    def release(self, world):
        self.permit.check(world['host'], self.binding)


# ------------------------------------------------------------------ the proof


def test_execution_returns_the_document_and_a_sealed_closed_proof(world):
    prepared = Prepared(world, 'deposit show', deposit=world['deposit'])
    proof = prepared.proof
    assert prepared.result['deposit_id'] == world['deposit']
    assert prepared.result['current_observed_at'] == FIXED
    assert registry.REGISTRY['deposit show'].output_model.model_validate_json(json.dumps(prepared.result))
    assert proof.failure is None and proof.matches(prepared.result)
    assert proof.observed_at == FIXED
    assert proof.pin == (world['deposit'], prepared.result['selected']['pin']['revision_id'], 1)
    assert proof.request is prepared.request and proof.request.command == 'deposit show'
    # A proof is a value: no reader, session, host, credential or callback.
    for field in dataclasses.fields(proof):
        value = getattr(proof, field.name)
        assert not hasattr(value, 'session') and not callable(value), field.name
    assert copy.deepcopy(proof) is proof and copy.deepcopy(proof.request) is proof.request
    # Only execution may build one.
    with pytest.raises(TypeError):
        owner.DepositProof(proof.identity, proof.request, prepared.result,
                           _seal=object(), observed_at=FIXED)
    with pytest.raises(TypeError):
        owner.DepositProof(proof.identity, proof.request, None, _seal=owner._SEAL, observed_at=FIXED)


def test_the_retained_proof_carries_no_private_guard_or_readset(world):
    prepared = Prepared(world, 'deposit show', deposit=world['deposit'])
    with support.reading(world) as (session, audience, binding):
        private = q.show(session, m.ShowInput(deposit=world['deposit']), binding=binding)
    text = repr(dataclasses.asdict(prepared.proof))
    assert private.dependencies.guard and private.dependencies.guard not in text
    assert not any(value in text for value in private.fingerprints.values())


# --------------------------------------------- the hidden-change release witness


def rename(world, noun, key, identity, name):
    """Rename one master through the running host, over HTTP."""
    version = world.setdefault(noun + '_version', 1)
    support.api(world)(noun + '.update', {key: identity, 'expected_version': version, 'name': name},
                       company=world['cid'], headers={'X-Bookflow-Reason': 'Public detail rename'})
    world[noun + '_version'] = version + 1


@pytest.mark.parametrize('command,raw', [
    ('deposit show', {}),
    ('deposit items', {'kind': 'additional'}),
    ('deposit items', {'kind': 'sources'}),
])
def test_a_hidden_only_change_never_refuses_release(world, command, raw):
    """The row's hardest constraint, as a behaviour.

    Prepare under a class deny, change that class, release. The private
    inspection guard moves - it is derived from readset relation anchors - so a
    proof that compared the readset, the guard, or the connected closure would
    deny here. The public document is unchanged, so release is silent.
    """
    denied = ('class',)
    set_denies(world, denied)
    try:
        with support.reading(world) as (session, audience, binding):
            before_guard = q.show(session, m.ShowInput(deposit=world['deposit']),
                                  binding=binding).dependencies.guard
        prepared = Prepared(world, command, deposit=world['deposit'], **raw)
        assert prepared.proof.failure is None
        set_denies(world, ())
        rename(world, 'class', 'class', world['grouping'],
               'Public detail class renamed ' + command.replace(' ', '-') + '-' + raw.get('kind', 'show'))
        set_denies(world, denied)
        with support.reading(world) as (session, audience, binding):
            after_guard = q.show(session, m.ShowInput(deposit=world['deposit']),
                                 binding=binding).dependencies.guard
            after = owner._produce(session, prepared.request, audience, FIXED)
        # The hidden change is real: the private guard bytes moved.
        assert before_guard and after_guard and before_guard != after_guard
        # The disclosed document did not, byte for byte, at the same instant.
        assert after.model_dump(mode='json') == prepared.result
        # And the release path stays silent.
        prepared.release(world)
    finally:
        set_denies(world, ())


def test_a_disclosed_change_does_refuse_release(world):
    """The same mutation shape, on a master this reader is admitted to.

    The pair matters more than either half: renaming the denied class and
    renaming the admitted bank account are the same kind of change to the same
    kind of record. Only admission differs, and only the admitted one refuses.
    """
    prepared = Prepared(world, 'deposit show', deposit=world['deposit'])
    named = {row['id']: row['label'] for row in prepared.result['current_references']}
    assert world['bank'] in named
    prepared.release(world)
    rename(world, 'account', 'account', world['bank'], 'Public detail bank renamed')
    with pytest.raises(BookflowError) as caught:
        prepared.release(world)
    assert caught.value.code == 'E_PERMISSION'
    assert caught.value.details == {'stage': 'publication', 'reason': 'authority_changed',
                                    'outcome': 'unknown'}
    # And the refusal really is about the disclosed label, not the clock.
    with support.reading(world) as (session, audience, binding):
        after = owner._produce(session, prepared.request, audience, prepared.proof.observed_at)
    changed = {row.id: row.label for row in after.current_references}
    assert changed[world['bank']] == 'Public detail bank renamed' != named[world['bank']]


def test_revocation_between_prepare_and_release_refuses(world):
    prepared = Prepared(world, 'deposit show', deposit=world['deposit'])
    prepared.release(world)
    with denying(world, ('ledger.read',)):
        with pytest.raises(BookflowError) as caught:
            prepared.release(world)
        assert caught.value.code == 'E_PERMISSION'
    prepared.release(world)


def test_a_retained_denial_releases_and_a_regained_capability_refuses(world):
    """A failure proof is held to the same standard as a success proof."""
    with denying(world, ('customer-work',)):
        prepared = Prepared(world, 'deposit show', deposit=world['work'])
        assert prepared.proof.failure is not None
        assert prepared.result == dict(code='E_RECORD_NOT_FOUND',
                                       message=prepared.result['message'], details={})
        assert prepared.proof.failure.code == 'E_RECORD_NOT_FOUND'
        assert prepared.proof.failure.details == ()
        prepared.release(world)
    with pytest.raises(BookflowError) as caught:
        prepared.release(world)
    assert caught.value.code == 'E_PERMISSION'


# ------------------------------------------------- unknown history, both ways


@contextlib.contextmanager
def damaged(world, record_type, record_id):
    """Corrupt one audit entry's payload, then put it back exactly as it was.

    Real byte-level damage in the copied fixture, applied with the host down and
    restored before the module ends, so no witness inherits another's damage.
    """
    from tests.test_deposit_show_history_failure import fault_storage
    from tests.test_row8_journal import database_path
    support.stop(world)
    path = database_path(world['client'])
    with fault_storage(path, 'audit_entries') as db:
        row = db.execute('SELECT id,"after" FROM audit_entries WHERE record_type=? AND record_id=? '
                         'ORDER BY id DESC LIMIT 1', (record_type, record_id)).fetchone()
        assert row is not None, (record_type, record_id)
        saved, identifier = row['after'], row['id']
        assert db.execute('UPDATE audit_entries SET "after"=? WHERE id=?',
                          (b'\x00{', identifier)).rowcount == 1
    support.start(world, serving=True)
    try:
        yield world
    finally:
        support.stop(world)
        with fault_storage(path, 'audit_entries') as db:
            db.execute('UPDATE audit_entries SET "after"=? WHERE id=?', (saved, identifier))
        support.start(world, serving=True)


def test_unknown_history_from_a_denied_reference_is_audience_safe(world):
    """Denied damage does not move the public status; admitted damage does.

    Enforcement, not declaration: one byte-level corruption, read by two readers
    who differ only in a single membership deny.
    """
    with damaged(world, 'class', world['grouping']):
        with support.reading(world) as (session, audience, binding):
            private = q.show(session, m.ShowInput(deposit=world['deposit']), binding=binding)
            admitted = reads.show(session, m.ShowInput(deposit=world['deposit']), audience=audience)
        # The damage is real: the private reader cannot issue its guard at all.
        assert private.dependencies.guard is None
        assert private.dependencies.history == 'unknown_history'
        assert admitted.inspection.history == 'unknown_history'
        with denying(world, ('class',)):
            with support.reading(world) as (session, audience, binding):
                denied = reads.show(session, m.ShowInput(deposit=world['deposit']), audience=audience)
                still_private = q.show(session, m.ShowInput(deposit=world['deposit']), binding=binding)
            assert still_private.dependencies.history == 'unknown_history'
            assert denied.inspection.history == 'complete'
            # A reader denied that class also releases a proof taken over the damage.
            prepared = Prepared(world, 'deposit show', deposit=world['deposit'])
            assert prepared.result['inspection']['history'] == 'complete'
            prepared.release(world)
    with support.reading(world) as (session, audience, binding):
        repaired = reads.show(session, m.ShowInput(deposit=world['deposit']), audience=audience)
    assert repaired.inspection.history == 'complete'


def test_damage_outside_the_reference_groups_is_never_hidden(world):
    """The catch-all is not audience-shaped: it is only reference groups that redact."""
    with damaged(world, 'transaction', world['source']):
        for denies in ((), ('class',), ('account', 'customer', 'class', 'payment-method',
                                        'custom-field', 'company')):
            with denying(world, denies):
                with support.reading(world) as (session, audience, binding):
                    detail = reads.show(session, m.ShowInput(deposit=world['deposit']), audience=audience)
                assert detail.inspection.history == 'unknown_history', denies


def test_a_repaired_history_refuses_release(world, monkeypatch):
    """Prepared unknown and unguarded, repaired complete: release must refuse."""
    from bookflow.company import deposit_dependency_history as history_owner
    with support.reading(world) as (session, audience, binding):
        assert reads.show(session, m.ShowInput(deposit=world['deposit']),
                          audience=audience).inspection.history == 'complete'
    with monkeypatch.context() as patch:
        patch.setattr(history_owner, 'capture',
                      lambda *args: (_ for _ in ()).throw(history_owner.MissingHistory('witness')))
        prepared = Prepared(world, 'deposit show', deposit=world['deposit'])
        assert prepared.result['inspection']['history'] == 'unknown_history'
        prepared.release(world)
    with pytest.raises(BookflowError) as caught:
        prepared.release(world)
    assert caught.value.code == 'E_PERMISSION'


# --------------------------------------------------------------- permit family


def test_the_permit_refuses_mixed_and_mismatched_proofs(world):
    prepared = Prepared(world, 'deposit show', deposit=world['deposit'])
    other = Prepared(world, 'deposit items', deposit=world['deposit'], kind='additional')
    permit = PublicationPermit(registry.REGISTRY['deposit show'], None, prepared.ctx,
                               prepared.permit.actor, frozenset(), None, None)
    with pytest.raises(BookflowError):
        permit.finish(None, result=prepared.result, deposit_proof=other.proof)
    with pytest.raises(BookflowError):
        permit.finish(None, result={'not': 'the document'}, deposit_proof=prepared.proof)
    with pytest.raises(BookflowError):
        permit.finish(None, succeeded=False, result=prepared.result, deposit_proof=prepared.proof)
    with pytest.raises(BookflowError):
        permit.finish(None, result=prepared.result, deposit_proof=object())
    audit_permit = PublicationPermit(registry.REGISTRY['audit list'], None, prepared.ctx,
                                     prepared.permit.actor, frozenset(), None, None)
    with pytest.raises(BookflowError):
        audit_permit.finish(None, result=prepared.result, deposit_proof=prepared.proof)


def test_a_retained_permit_round_trips_and_rejects_a_mixed_or_raw_state(world):
    prepared = Prepared(world, 'deposit items', deposit=world['deposit'], kind='sources')
    state = prepared.permit.retained()
    assert state['command'] == 'deposit items' and state['input'] is None
    assert state['deposit_proof'] is prepared.proof and state['audit_proof'] is None
    restored = PublicationPermit.from_retained(state)
    assert restored.inp is None and restored.deposit_proof is prepared.proof
    with pytest.raises(BookflowError):
        PublicationPermit.from_retained(dict(state, input={'deposit': world['deposit'],
                                                           'kind': 'sources'}))
    with pytest.raises(BookflowError):
        PublicationPermit.from_retained(dict(state, command='deposit show'))
    show = Prepared(world, 'deposit show', deposit=world['deposit'])
    with pytest.raises(BookflowError):
        PublicationPermit.from_retained(dict(state, audit_proof=show.proof))
    with pytest.raises(BookflowError):
        PublicationPermit.from_retained(dict(state, deposit_proof=object()))


def test_publication_coverage_is_explicit_for_the_deposit_family():
    from bookflow.core import publication_inventory
    registry.load_all()
    rows = {row['command']: row for row in publication_inventory.inventory()}
    for name in ('deposit show', 'deposit items'):
        assert rows[name]['policy'] == 'reader_bound_public_detail_proof'
        assert rows[name]['policy'] != 'selected_company_and_current_resources'

    class Unregistered:
        # A third deposit command planned by the same module: coverage must fail
        # rather than fall back to the generic company policy.
        name = 'deposit query'
        plan = registry.REGISTRY['deposit show'].plan
        local_only = standalone = False
        scope = 'company'

    with pytest.raises(RuntimeError, match='publication dependency inventory'):
        publication_inventory.policy(Unregistered)
    assert publication_inventory.policy(registry.REGISTRY['payment show']) == \
        'selected_company_and_current_resources'
