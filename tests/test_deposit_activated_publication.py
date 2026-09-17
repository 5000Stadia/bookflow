"""The four reader-bound public deposit reads, on four surfaces, AFTER activation.

Why this test exists. `tests/test_mcp_registry_deposits.py` already drives the whole
deposit lifecycle across python, cli, http and mcp -- and never runs `permission
activate`, so it tests the product in a state a real company leaves the moment it
configures per-family Delete permissions. In that un-activated state
`hub.access.require_resource` answers from `company_role`/`role_satisfies` and never
enters the granular policy owner. Activated, it answers through
`hub.permission_access.require` -> `identity_admin_binding.session_operation`, which
takes its producer from `Session.credential`. The hosted reader these four commands
execute under is built inside `identity_admin_binding.hosted_reader`, whose session
was the one session in the product that was never handed its admitted credential: a
token reader has os_login '' and nothing to derive an OSBinding from, so all four
reads failed closed with E_UNAUTHENTICATED over HTTP and MCP while an offline reader,
which has a real OS login to fall back on, kept working in process and over the CLI.
The E_UNAUTHENTICATED was then overwritten by the permit at the publication boundary
and reached the caller as E_PERMISSION {stage: publication, outcome: unknown}.

Which commands, and why only these. The set is not written down here -- it is derived
from `core.publication_inventory.policy`, which is the one place that names a command
reader-bound. They are the only commands that execute under a BoundReader instead of
`adapters/http/execution.run_hosted`'s own session, which is why the defect could not
reach the other 436 company commands: `run_hosted` sets `session.credential` itself.
`deposit sources` is an ordinary company command on the ordinary path and is included
below as the control that separates "the surface broke" from "these commands broke".
"""
import asyncio
from datetime import datetime
import json
import logging

import pytest

from tests.mcp_matrix_support import Matrix
from tests.test_deposit_command import books  # noqa: F401

READS = ('deposit query', 'deposit show', 'deposit items', 'deposit history')
CONTROL = 'deposit sources'
SURFACES = ('python', 'cli', 'http', 'mcp')


def test_the_reader_bound_set_is_exactly_these_four():
    """The scope of this witness is derived from the product, never restated here."""
    from bookflow.core import registry
    from bookflow.core.publication_inventory import policy
    registry.load_all()
    bound = {cmd.name for cmd in registry.all_commands(include_standalone=True)
             if policy(cmd) == 'reader_bound_public_detail_proof'}
    assert bound == set(READS)
    assert policy(registry.get(CONTROL)) == 'selected_company_and_current_resources'


@pytest.mark.timeout(600)
def test_public_deposit_reads_answer_identically_on_four_surfaces_after_activation(root, tmp_path):
    """Activate first, then read the seeded deposit every way an agent can.

    Each surface works on its own copy of one seeded baseline, so the deposit, its
    revision and its continuation digests are the same values on all four; only the
    transport differs. Compare business documents exactly, except the validated
    observation timestamp produced by each separate read. The only write each
    surface performs is its own activation.
    """
    pytest.importorskip('mcp')
    answers = {}

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path / 'surfaces')
            for surface in SURFACES:
                listing = dict(page=dict(limit=10))
                # Before: the product as the un-activated suite has always tested it.
                opening = await matrix.call(surface, 'deposit query', listing)
                assert opening['total_count'] == 1, (surface, opening)

                state = await matrix.call(surface, 'permission show', {})
                await matrix.call(surface, 'permission activate', dict(
                    expected_generation=state['generation'],
                    expected_catalog_sha256=state['catalog_sha256']))

                # After: the state every company that configures Delete permissions is in.
                saved = await matrix.call(surface, 'deposit query', listing)
                deposit = saved['items'][0]['current']['deposit_id']
                documents = {
                    'deposit query': saved,
                    'deposit show': await matrix.call(surface, 'deposit show', dict(deposit=deposit)),
                    'deposit items': await matrix.call(surface, 'deposit items', dict(
                        deposit=deposit, kind='sources', page=dict(limit=50))),
                    'deposit history': await matrix.call(surface, 'deposit history', dict(
                        deposit=deposit, page=dict(limit=50))),
                    CONTROL: await matrix.call(surface, CONTROL, dict(date='2026-10-10', limit=50)),
                }
                assert set(documents) == set(READS) | {CONTROL}
                # Activation changes no answer these reads give.
                assert documents['deposit query'] == opening, (surface, 'activation moved the page')
                assert documents['deposit show']['current']['deposit_id'] == deposit
                assert documents['deposit history']['deposit_id'] == deposit
                assert documents['deposit items']['total_count'] >= 1
                answers[surface] = documents
        finally:
            await matrix.close()

    asyncio.run(witness())

    assert set(answers) == set(SURFACES)
    # Show/items stamp the instant of the read, not a stored business fact.
    # Validate that timestamp and normalize only this named top-level field;
    # stored dates, histories, identities and monetary values remain exact.
    for documents in answers.values():
        for name in ('deposit show', 'deposit items'):
            stamp = documents[name]['current_observed_at']
            assert datetime.fromisoformat(stamp).tzinfo is not None
            documents[name] = {**documents[name], 'current_observed_at': '<read-time>'}
    reference = answers['python']
    for surface in ('cli', 'http', 'mcp'):
        for name in (*READS, CONTROL):
            assert answers[surface][name] == reference[name], (surface, name,
                json.dumps(answers[surface][name], sort_keys=True)[:600],
                json.dumps(reference[name], sort_keys=True)[:600])


@pytest.mark.timeout(180)
def test_a_proven_authentication_loss_crosses_the_publication_boundary_as_itself(
        books, tmp_path, monkeypatch, caplog):
    """E_UNAUTHENTICATED is a fact about the caller's own credential, so it may be said.

    The sibling of `test_deposit_public_query.py`'s redaction witness, and deliberately
    its opposite half. That one pins what a denial may NOT say: an E_INTERNAL raised
    inside a public deposit execution, before its proof exists, must not reach the
    caller as a code or as a reason, because the release check could not verify it.
    This one pins the single exception. An E_UNAUTHENTICATED describes no company fact
    and no other principal's authority -- the caller already knows their own credential
    state -- and it is the only answer they can act on, so the boundary says it instead
    of overwriting it with E_PERMISSION {outcome: unknown}, which told an agent nothing.

    Both halves run through the same unfinished certificate: the permit at that moment
    is the placeholder `adapters/http/execution` builds with an empty membership
    frozenset and no proof.
    """
    from bookflow.commands.host_cmds import start_serving
    from bookflow.company import deposit_public_reads
    from bookflow.core.context import client_version
    from bookflow.core.errors import BookflowError
    from tests.test_deposit_public_query import post
    from tests.test_row3_host import Hosted

    client = books['client']
    post(books, 'boundary-authentication')
    company = client.company.list()['items'][0]['company_id']
    token = client.token.issue(label='Boundary authentication')

    def lost(*args, **kwargs):
        raise BookflowError('E_UNAUTHENTICATED', details={'reason': 'OS authority changed'})

    monkeypatch.setattr(deposit_public_reads, 'query', lost)
    handle = start_serving(tmp_path / 'root', client_version(), bind='127.0.0.1:8765', secure_cookies=False)
    try:
        hosted = Hosted(handle, tmp_path / 'root', '', company, token, '', {})
        with caplog.at_level(logging.INFO, logger='bookflow.http'):
            body = hosted.call('deposit.query', {}, company=company).json()
        assert body['code'] == 'E_UNAUTHENTICATED', body
        assert body['details'] == {'stage': 'publication', 'outcome': 'unknown'}
        # Still a redacting boundary: the raiser's own reason does not travel.
        assert 'OS authority changed' not in json.dumps(body)
        diagnostic = [r.getMessage() for r in caplog.records if 'publication denied' in r.getMessage()]
        assert diagnostic, 'no diagnostic recorded for a denial after failed execution'
        assert 'original=E_UNAUTHENTICATED' in diagnostic[0]
        assert 'publication_reason=unfinished_certificate' in diagnostic[0]
        assert 'proof=absent' in diagnostic[0]
        assert token['secret'] not in diagnostic[0]
    finally:
        handle.stop()


@pytest.mark.parametrize('memberships', [frozenset(), frozenset({('member',)})])
def test_preparation_and_execution_permits_have_distinct_proof_requirements(monkeypatch, memberships):
    from bookflow.core import registry
    from bookflow.core.context import Context
    from bookflow.core.errors import BookflowError
    from bookflow.core.publication import PublicationPermit

    registry.load_all()
    cmd = registry.get('deposit query')
    permit = PublicationPermit(cmd, cmd.input_model.model_validate({}),
        Context.new('mcp', 'Permit phase regression'), ('actor', 'human', False),
        memberships, None, None)
    checked = []
    # Isolate only the phase gate; the real four-surface witness above exercises
    # the unchanged authorization implementation used by preparation permits.
    monkeypatch.setattr(PublicationPermit, '_check', lambda *a, **kw: checked.append(True))
    permit.check(None, None)
    assert checked == [True]
    permit.requires_deposit_proof = True
    restored = PublicationPermit.from_retained(permit.retained())
    assert restored.requires_deposit_proof is True
    with pytest.raises(BookflowError) as error:
        restored.check(None, None)
    assert error.value.details['reason'] == 'unfinished_certificate'
    assert checked == [True]
