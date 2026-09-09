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
from datetime import timedelta

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

    def __init__(self, world, command, at=FIXED, credential=None, **raw):
        from bookflow.adapters.http.execution import _reader_binding
        from bookflow.core import identity_admin_binding as ib
        self.ctx = Context.new('python', 'Public deposit publication witness')
        self.command = registry.REGISTRY[command]
        from bookflow.core.config import os_login
        # An OS producer by default; a bearer credential where the witness needs
        # one, because only a token credential can expire.
        self.binding = credential or OSBinding.capture(world['host'], os_login())
        admitted = (self.binding if credential is None
                    else _reader_binding(world['host'], credential, self.ctx.request_id))
        self.request = request_for(world, command, **raw)
        with ib.hosted_reader(world['host'], admitted, request_id=self.ctx.request_id) as reader:
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
    proof that captured the readset, the guard, or the connected closure would
    deny here.

    Under the narrowed release check this is now true twice over: release
    compares no document at all. What the byte-identity assertion below still
    earns on its own is that the *projector* is audience-safe, which is what
    keeps a hidden change out of the response body as well as out of the
    release path.
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


def test_an_ordinary_same_company_edit_no_longer_refuses_release(world):
    """The narrowing, stated as the behaviour it changes.

    Renaming the deposit's own bank account is an ordinary same-company edit by
    someone who may make it, and the reader is admitted to that account, so the
    label it would read really did move. Before the narrowing this refused,
    because release reconstructed and compared the whole document. It no longer
    does: an already captured coherent read is not invalidated by a later
    same-company edit. The refusals that remain are authority refusals, and they
    are witnessed separately below.
    """
    prepared = Prepared(world, 'deposit show', deposit=world['deposit'])
    named = {row['id']: row['label'] for row in prepared.result['current_references']}
    assert world['bank'] in named
    prepared.release(world)
    rename(world, 'account', 'account', world['bank'], 'Public detail bank renamed')
    # The edit is real and this reader can see it.
    with support.reading(world) as (session, audience, binding):
        after = owner._produce(session, prepared.request, audience, prepared.proof.observed_at)
    changed = {row.id: row.label for row in after.current_references}
    assert changed[world['bank']] == 'Public detail bank renamed' != named[world['bank']]
    assert after.model_dump(mode='json') != prepared.result
    # And release is silent about it.
    prepared.release(world)


def test_release_performs_no_deposit_projection_at_all(world):
    """Enforcement, not declaration: one projection per request, at execution.

    The counter is the same one the surface measurements used. If release ever
    reconstructs the result again - for any reason, on any surface - this goes
    red rather than merely getting slower.
    """
    from bookflow.company import deposit_public_reads as reads_module
    from bookflow.core import publication_deposit
    tally = []
    originals = {}

    def wrap(module, name):
        originals[(module, name)] = getattr(module, name)

        def counting(*args, **rest):
            tally.append(name)
            return originals[(module, name)](*args, **rest)
        setattr(module, name, counting)

    wrap(reads_module, 'show')
    wrap(reads_module, 'items')
    wrap(publication_deposit, 'revalidate_proof')
    try:
        prepared = Prepared(world, 'deposit show', deposit=world['deposit'])
        assert tally.count('show') == 1, tally
        tally.clear()
        prepared.release(world)
        prepared.release(world)
        assert tally == ['revalidate_proof', 'revalidate_proof'], tally
    finally:
        for (module, name), original in originals.items():
            setattr(module, name, original)


def test_revocation_between_prepare_and_release_refuses(world):
    prepared = Prepared(world, 'deposit show', deposit=world['deposit'])
    prepared.release(world)
    with denying(world, ('ledger.read',)):
        with pytest.raises(BookflowError) as caught:
            prepared.release(world)
        assert caught.value.code == 'E_PERMISSION'
    prepared.release(world)


def test_a_retained_denial_still_releases_after_the_capability_returns(world):
    """A retained non-disclosing refusal is a coherent captured result.

    It is also the *more* restrictive answer, so releasing it to a reader who
    has since regained the connected capability discloses nothing. Before the
    narrowing this refused; the refusal was the expensive re-read, not a
    boundary.
    """
    with denying(world, ('customer-work',)):
        prepared = Prepared(world, 'deposit show', deposit=world['work'])
        assert prepared.proof.failure is not None
        assert prepared.result == dict(code='E_RECORD_NOT_FOUND',
                                       message=prepared.result['message'], details={})
        assert prepared.proof.failure.code == 'E_RECORD_NOT_FOUND'
        assert prepared.proof.failure.details == ()
        prepared.release(world)
    prepared.release(world)
    # Losing access to the company itself is a different question, and still refuses.
    with denying(world, ('ledger.read',)):
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


def test_a_repaired_history_no_longer_refuses_release(world, monkeypatch):
    """Prepared unknown and unguarded, repaired complete: release stays silent.

    The owning plan required this to refuse. The accepted narrowing supersedes
    that clause: a history repair is an ordinary same-company change, and the
    captured `unknown_history` answer was coherent when it was taken.
    """
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
    with support.reading(world) as (session, audience, binding):
        assert reads.show(session, m.ShowInput(deposit=world['deposit']),
                          audience=audience).inspection.history == 'complete'
    prepared.release(world)


# ------------------------------------------- what release still refuses


def bearer(world, *, days=1, label='Publication expiry bearer'):
    """A real bearer credential with a real expiry, issued with the host down."""
    from bookflow.adapters.http.app import Credential
    from bookflow.core.config import Config, os_login
    support.stop(world)
    issued = world['client'].token.issue(label=label, days=days)
    uid = Config.load(world['root'] / 'config.toml').user_table(os_login())['user_id']
    support.start(world, serving=True)
    return Credential(uid, issued['token_id'], 'bearer', issued['label'],
                      actor_kind='human', secret=issued['secret']), issued


def token_row(world, token_id):
    import sqlite3
    connection = sqlite3.connect(world['root'] / 'hub.db')
    try:
        connection.row_factory = sqlite3.Row
        row = connection.execute('SELECT * FROM api_tokens WHERE id=?', (token_id,)).fetchone()
        generation = connection.execute('SELECT generation FROM permission_state WHERE id=1').fetchone()[0]
        return dict(row), generation
    finally:
        connection.close()


def test_an_expired_credential_refuses_between_parts(world, monkeypatch):
    """Expiry is clock-dependent and moves with no commit, so it is re-asked.

    A response is released part by part, and each part re-checks. This runs two
    checks on one already captured result and moves only the clock between them.
    Nothing is written: the token row and the permission generation are asserted
    identical across the move. A release that reused its earlier answer - or
    that inferred safety from an unchanged epoch - would let the second part go
    out on an expired credential.
    """
    from bookflow.core import clock
    credential, issued = bearer(world)
    prepared = Prepared(world, 'deposit show', deposit=world['deposit'], credential=credential)
    assert prepared.proof.failure is None
    before, generation = token_row(world, credential.token_id)
    assert before['expires_at'] and before['revoked_at'] is None
    # Part one releases.
    prepared.release(world)
    later = clock.now() + timedelta(days=days_until(before['expires_at']) + 1)
    monkeypatch.setattr(clock, 'now', lambda: later)
    # Part two, same result, same permit, only the clock moved.
    with pytest.raises(BookflowError) as caught:
        prepared.release(world)
    assert caught.value.code == 'E_UNAUTHENTICATED'
    after, generation_after = token_row(world, credential.token_id)
    assert after == before and generation_after == generation, 'the clock moved, nothing committed'
    monkeypatch.undo()
    # And it comes back when the clock does, so the refusal was the expiry.
    prepared.release(world)


def days_until(expires_at):
    from bookflow.core import clock
    return max(1, (clock.parse_iso(expires_at) - clock.now()).days + 1)


def second_company(world):
    """One more company in the same organization, created with the host down."""
    if world.get('other_cid'):
        return world['other_cid']
    support.stop(world)
    created = world['client'].run('company new', dict(
        organization='Demo Holdings LLC', legal_name='Public Detail Second Co',
        display_name='Public Detail Second Co', home_currency='USD'))
    support.start(world, serving=True)
    world['other_cid'] = created['id'] if 'id' in created else created['company_id']
    return world['other_cid']


def test_a_captured_result_cannot_cross_a_company_boundary(world):
    """Cross-company leakage stays blocking; the narrowing does not touch it.

    Access is per company, so a result captured in one company is released only
    while its reader still holds that company - proved here by denying the read
    in this company while the same reader keeps another one.
    """
    other = second_company(world)
    assert other != world['cid']
    prepared = Prepared(world, 'deposit show', deposit=world['deposit'])
    assert prepared.result['company_id'] == prepared.request.company == world['cid']
    prepared.release(world)
    with denying(world, ('ledger.read',)):
        with pytest.raises(BookflowError) as caught:
            prepared.release(world)
        assert caught.value.code == 'E_PERMISSION'
        # The denial really is scoped to one company: the same reader, in the
        # same moment, still opens the other one.
        with support.reading_hub(world) as (reader, audience, binding, ctx):
            reads.open_selected(reader, audience, other, ctx)
    prepared.release(world)


def test_a_document_from_another_company_can_never_be_sealed_or_released(world):
    """The scope check is enforced where a proof is built and again at release."""
    prepared = Prepared(world, 'deposit show', deposit=world['deposit'])
    foreign = dict(prepared.result, company_id=second_company(world))
    with pytest.raises(TypeError):
        owner.DepositProof(prepared.proof.identity, prepared.request, foreign,
                           _seal=owner._SEAL, observed_at=FIXED)
    mutated = owner.DepositProof(prepared.proof.identity, prepared.request, dict(prepared.result),
                                 _seal=owner._SEAL, observed_at=FIXED)
    object.__setattr__(mutated, 'document', foreign)
    with pytest.raises(BookflowError) as caught:
        owner._scoped(mutated)
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
