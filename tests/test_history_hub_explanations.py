"""Disposable producers -> shared hosted hub wire -> retained publication.

B2 is intentionally called through its private hosted binding, not the command
registry. Two completeness witnesses add an existing producer's capture to an
organization event; they do not invent capture formats or permission facts.
"""
from dataclasses import replace
import json
import shutil

import bookflow
import pytest

from bookflow.adapters.http.execution import run_hosted
from bookflow.core import identity_admin_binding as binding, registry
from bookflow.core.config import Config, os_login
from bookflow.core.context import Context, client_version
from bookflow.core.errors import BookflowError
from bookflow.core.host import Host
from bookflow.core.ids import new_id
from bookflow.core.publication import OSBinding, PublicationPermit
from bookflow.hub import audit_projection as projection, identity_admin as admin
from bookflow.hub.permission_catalog import ScopeKey
from bookflow.hub.permission_runtime import catalog_bundle
from bookflow.storage.engine import open_database
from tests.conftest import make_actor
from tests.permission_admin_support import CONTEXT, OLD, insert, binding as token_binding
from tests.test_audit_projection_publication import credential
from tests.test_permission_runtime import path as root_fixture
from tests.test_permission_snapshots import install_fixture_policy


REASON = 'Reviewed <hub> change & retained explanation'
CITATION = '01J00000000000000000000000'
assert len(CITATION) == 26


def context(**updates):
    return Context.new('http', 'Hub explanation witness').model_copy(
        update={'request_id': 'REQUEST', **updates})


def read(host, cred, mode, **data):
    try:
        return run_hosted(host, registry.get('hub audit ' + mode), data,
                          context(), cred, None, 'none', False)
    except BookflowError as error:
        error.add_note(f'hub audit {mode}: {error.details!r}')
        raise


def produce(host, cred, intent, *, reason=REASON, citation=None):
    def edit():
        with host._commit_hooks.operation('dispatch.apply', host._hub):
            with binding.hosted_operation(
                host, cred, request_id='REQUEST', purpose='apply',
            ) as operation:
                result = operation.apply(intent, audit=replace(
                    CONTEXT, reason=reason, directive_id=citation,
                    directive_code='PRIVATE-CITE' if citation else None))
                host._commit_hooks.commit(host._hub, 'dispatch.apply')
                return result.private.audit.event.id
    return host.submit(edit)


def latest(host, command):
    row = host.submit(lambda: host._hub.raw.execute(
        'SELECT id,reason,directive_id FROM audit_events '
        'WHERE command=? ORDER BY seq DESC LIMIT 1', (command,)).fetchone())
    assert row is not None
    return row


def assert_explanation(host, cred, event, expected, *, after=None):
    replies = [read(host, cred, 'show', event=event)]
    page = read(host, cred, 'list', limit=1)
    while not any(x['id'] == event for x in page['items']):
        page.check()
        assert page['next_before'], f'{event} missing from public hub list'
        page = read(host, cred, 'list', limit=1, before=page['next_before'])
    replies.append(page)
    items = [replies[0], next(x for x in page['items'] if x['id'] == event)]
    if after is not None:
        replies.append(read(host, cred, 'tail', after=after, limit=100))
        items.append(next(x for x in replies[-1]['items'] if x['id'] == event))
    for item in items:
        assert item['explanation'] == expected
        wire = json.dumps(dict(item))
        assert 'reader_redacted' not in wire
        assert CITATION not in wire and 'PRIVATE-CITE' not in wire
        if expected is None:
            assert REASON not in wire
    for reply in replies:
        reply.check()
    assert host._readers_attached == 0
    return replies


@pytest.fixture
def hub(tmp_path):
    path = root_fixture.__wrapped__(tmp_path)
    with open_database(path, writable=True) as db:
        db.raw.execute("UPDATE users SET hub_admin=1 WHERE id='W'")
        db.raw.execute('UPDATE organizations SET name_key=lower(display_name)')
        for who, scope in [('H', 'O'), ('W', 'Z')]:
            insert(db.raw, 'memberships', dict(
                id='EXPLAIN-' + who, user_id=who, scope_type='organization',
                scope_id=scope, role='owner', grants=None, denies='[]',
                granted_by='H', granted_at=OLD, revoked_at=None, version=1))
    registry.load_all()
    host = Host(path.parent, version=client_version())
    host.start()
    try:
        cred = OSBinding.capture(host, os_login())
        yield host, cred
    finally:
        host.stop()


@pytest.fixture(scope='module')
def admitted_hub(tmp_path_factory):
    """Browser reuse: (host, OSBinding, event); HTTP bearer is secret-H."""
    fixture = hub.__wrapped__(tmp_path_factory.mktemp('admitted-hub-explanation'))
    host, cred = next(fixture)
    try:
        event = produce(host, cred, admin.PutMembership(
            'R', ScopeKey('organization', 'O'), admin.Version(1), 'readonly'),
            citation=CITATION)
        yield host, cred, event
    finally:
        fixture.close()


@pytest.mark.parametrize('citation', [None, CITATION])
def test_real_b2_explanation_all_wire_modes_and_retained_authority(hub, citation):
    host, cred = hub
    start = read(host, cred, 'tail')['next_after']
    event = produce(host, cred, admin.PutMembership(
        'R', ScopeKey('organization', 'O'), admin.Version(1), 'readonly'),
        citation=citation)
    assert latest(host, 'permission membership put') == (event, REASON, citation)
    expected = {'reason': REASON, 'directive_status':
                'unavailable' if citation else 'not_cited', 'directive': None}
    replies = assert_explanation(host, cred, event, expected, after=start)
    retained = [PublicationPermit.from_retained(x.permit.retained()) for x in replies]
    for permit in retained:
        permit.check(host, cred)

    # Same visible membership history, but a human organization administrator
    # has no installation-admin entitlement to the operation explanation.
    nonadmin = credential(host, 'A')
    shown = assert_explanation(host, nonadmin, event, None)[0]
    assert shown['entries'] and shown['command'] == 'permission membership put'

    membership = host.submit(lambda: host._hub.raw.execute(
        "SELECT id,version FROM memberships WHERE user_id='H' "
        "AND scope_type='organization' AND scope_id='O'",).fetchone())
    produce(host, cred, admin.RevokeMembership(*membership))
    for permit in retained:
        with pytest.raises(BookflowError) as caught:
            permit.check(host, cred)
        assert caught.value.code == 'E_PERMISSION'
    fresh = read(host, OSBinding.capture(host, os_login()), 'list', limit=100)
    assert event not in {x['id'] for x in fresh['items']}
    fresh.check()
    assert host._readers_attached == 0


@pytest.fixture(scope='module')
def company_world(_seeded_template, tmp_path_factory):
    root = tmp_path_factory.mktemp('hub-explanations') / 'root'
    shutil.copytree(_seeded_template, root)
    client = bookflow.connect(data_root=str(root))
    client.run('upgrade', {})  # Only the disposable copy is upgraded.
    with open_database(root / 'hub.db', writable=True) as db:
        install_fixture_policy(db.raw, catalog_bundle())
    company = client.company.list()['items'][0]['company_id']
    info = client.company.show(company=company)
    make_actor(root, 'hub-explanation-outsider', hub_admin=True)
    registry.load_all()
    host = Host(root, version=client_version())
    host.start()
    try:
        cred = OSBinding.capture(host, os_login())
        # Keep list pagination focused on current producer captures. Older seed
        # history is exercised by the parent's separate legacy regression work.
        write(host, cred, 'organization new', {'name': 'Current history boundary'})
        grant_created_organization(host, 'Current history boundary')
        yield host, cred, company, info
    finally:
        host.stop()


def write(host, cred, command, data, *, company=None, reason=REASON, citation=None):
    registry.get(command).input_model.model_validate(data)
    try:
        return run_hosted(host, registry.get(command), data,
                          context(reason=reason, directive_id=citation), cred, company,
                          'option' if company else 'none', False)
    except BookflowError as error:
        error.add_note(f'{command}: {error.details!r}')
        raise


def grant_created_organization(host, name):
    """Fixture membership: organization creation does not grant membership."""
    uid = Config.load(host.data_root / 'config.toml').user_table(os_login())['user_id']
    def grant():
        raw = host._hub.raw
        oid = raw.execute('SELECT id FROM organizations WHERE display_name=?', (name,)).fetchone()[0]
        insert(raw, 'memberships', dict(
            id=new_id(), user_id=uid, scope_type='organization', scope_id=oid,
            role='owner', grants=None, denies='[]', granted_by=uid,
            granted_at=OLD, revoked_at=None, version=1))
    host.submit(grant)


def test_organization_new_and_rename_are_actual_supported_producers(company_world):
    host, cred, _, _ = company_world
    write(host, cred, 'organization new', {'name': 'Explanation organization'})
    event, reason, citation = latest(host, 'organization new')
    assert reason == REASON and citation is None
    grant_created_organization(host, 'Explanation organization')
    expected = {'reason': REASON, 'directive_status': 'not_cited', 'directive': None}
    assert_explanation(host, cred, event, expected)
    start = read(host, cred, 'tail')['next_after']
    write(host, cred, 'organization rename', {
        'organization': 'Explanation organization', 'name': 'Renamed explanation organization'})
    event, reason, citation = latest(host, 'organization rename')
    assert reason == REASON and citation is None
    assert_explanation(host, cred, event, expected, after=start)


def test_company_use_and_real_registry_repair_do_not_explain(company_world):
    host, cred, company, info = company_world
    outsider = OSBinding.capture(host, 'hub-explanation-outsider')
    start = read(host, outsider, 'tail')['next_after']
    write(host, cred, 'company use', {'company': company})
    preference, reason, _ = latest(host, 'company use')
    assert reason == REASON
    shown = assert_explanation(host, outsider, preference, None, after=start)[0]
    assert shown['entries'] == []
    assert shown['summary'] == 'Default company preference changed.'
    assert company not in json.dumps(dict(shown))
    assert info['display_name'] not in json.dumps(dict(shown))

    directive = write(host, cred, 'directive add', {'text': 'Private company repair instruction'},
                      company=company)['directive']
    start = read(host, cred, 'tail')['next_after']
    write(host, cred, 'company update', {'legal_name': 'Actual hub repair witness'},
          company=company, citation=directive['id'])
    event, reason, citation = latest(host, 'company update')
    assert reason == REASON and citation == directive['id']
    shown = assert_explanation(host, cred, event, None, after=start)[0]
    assert any(x['identity']['kind'] == 'company' for x in shown['entries'])
    assert directive['id'] not in json.dumps(dict(shown))
    assert directive['text'] not in json.dumps(dict(shown))

    # A no-op against company_info still repairs stale hub metadata and carries
    # the company command's context. Seed only the disposable projection drift.
    host.submit(lambda: host._hub.raw.execute(
        'UPDATE companies SET legal_name=? WHERE id=?', ('Stale registry name', company)))
    write(host, cred, 'company update', {'legal_name': 'Actual hub repair witness'},
          company=company, citation=directive['id'])
    noop = latest(host, 'company update')[0]
    assert noop != event
    assert_explanation(host, cred, noop, None)
    # The installation-only reader cannot see this registry event or its reason.
    page = read(host, outsider, 'list', limit=100)
    assert not {event, noop} & {x['id'] for x in page['items']}
    assert REASON not in json.dumps(dict(page))
    page.check()


def add_reference_capture(host, source, target, kind):
    """One unchanged co-entry, copied byte-for-byte from a real producer.

    This is a traversal witness for captured co-effects, not a claim that today's
    organization rename itself changes memberships or company registry records.
    """
    def copy():
        raw = host._hub.raw
        row = raw.execute(
            'SELECT record_id,version_after,after FROM audit_entries '
            'WHERE event_id=? AND record_type=?', (source, kind)).fetchone()
        assert row is not None
        raw.execute('BEGIN IMMEDIATE')
        try:
            raw.execute(
                'INSERT INTO audit_entries '
                '(id,event_id,record_type,record_id,action,version_before,version_after,before,after) '
                'VALUES(?,?,?,?,?,?,?,?,?)',
                (new_id(), target, kind, row[0], 'update', row[1], row[1], row[2], row[2]))
            raw.execute('COMMIT')
        except BaseException:
            raw.execute('ROLLBACK')
            raise
    host.submit(copy)


def test_hidden_unchanged_membership_coeffect_suppresses_visible_operation(hub):
    host, cred = hub
    # H is a global admin, but has no visibility into E or its parent Z.
    other_admin = token_binding(host.data_root / 'hub.db', 'W')
    source = produce(host, other_admin, admin.PutMembership(
        'U', ScopeKey('company', 'E'), admin.Absent(), 'readonly'))
    write(host, cred, 'organization new', {'name': 'Visible coeffect organization'})
    grant_created_organization(host, 'Visible coeffect organization')
    write(host, cred, 'organization rename', {
        'organization': 'Visible coeffect organization', 'name': 'Visible rename'})
    event = latest(host, 'organization rename')[0]
    add_reference_capture(host, source, event, 'membership')
    shown = assert_explanation(host, cred, event, None)[0]
    assert shown['command'] == 'organization rename' and shown['entries']
    assert all(x['identity']['kind'] != 'membership' for x in shown['entries'])
    produce(host, other_admin, admin.PutMembership(
        'H', ScopeKey('organization', 'Z'), admin.Absent(), 'owner'))
    # Once every captured scope is visible the unchanged reference may be
    # omitted from the wire without defeating explanation admission.
    assert_explanation(host, cred, event, {
        'reason': REASON, 'directive_status': 'not_cited', 'directive': None})


def test_hidden_registry_parent_suppresses_otherwise_complete_operation(company_world, monkeypatch):
    host, cred, company, info = company_world
    write(host, cred, 'company update', {'legal_name': 'Parent reference capture'}, company=company)
    source = latest(host, 'company update')[0]
    # Use a separate visible initiating entry, so hiding the referenced parent
    # cannot remove the whole operation before completeness is examined.
    write(host, cred, 'organization new', {'name': 'Coentry parent witness'})
    grant_created_organization(host, 'Coentry parent witness')
    event = latest(host, 'organization new')[0]
    add_reference_capture(host, source, event, 'company')
    expected = {'reason': REASON, 'directive_status': 'not_cited', 'directive': None}
    assert_explanation(host, cred, event, expected)
    visible = projection.AuditAudience.visible

    def hide_parent(self, scope):
        if scope == ScopeKey('organization', info['organization_id']):
            return False
        return visible(self, scope)

    # Native company membership discovers its parent. Supply the stricter
    # governed audience case explicitly; retain real authentication/captures.
    monkeypatch.setattr(projection.AuditAudience, 'visible', hide_parent)
    shown = assert_explanation(host, cred, event, None)[0]
    assert shown['command'] == 'organization new' and shown['entries']
