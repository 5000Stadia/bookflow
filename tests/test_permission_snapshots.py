"""Complete real-root projections, strict catalog round trips and supplied visibility."""
from dataclasses import asdict, replace
import hashlib
import json
import sqlite3
import pytest
from bookflow.hub import permission_catalog as c, permission_policy as a, permission_runtime as r, permission_snapshot as s
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS
from tests.permission_storage_support import create_hub, snapshot

BUNDLE=s.CatalogBundle(r.SOURCE_COMMIT,c.FROZEN_CATALOG,c.FROZEN_MANIFEST.standalone_names,
    hashlib.sha256(json.dumps([asdict(x) for x in c.CONDITIONAL_RESOURCE_SOURCES],sort_keys=True).encode()).hexdigest())


def without_capability(catalog, name):
    """Retire a capability the way a real transition has to: leave nothing referencing it.

    Dropping the capability and its company actions is not enough. A conditional source
    that still names the atom describes a requirement the company universe can no longer
    express, and the catalog validator rejects it - correctly. Trim the atom out of every
    source, and drop any source it was the whole of.
    """
    return replace(catalog,
        capabilities=tuple(x for x in catalog.capabilities if x.name != name),
        company_actions=tuple(x for x in catalog.company_actions
                              if name not in {r.capability for r in x.requirements}),
        defaults=tuple(x for x in catalog.defaults if x.requirement.capability != name),
        conditional_sources=tuple(
            replace(x, requirements=tuple(r for r in x.requirements if r.capability != name))
            for x in catalog.conditional_sources
            if any(r.capability != name for r in x.requirements)))


def install_fixture_policy(raw,bundle=BUNDLE):
    # Explicit owned fixture facts, NOT a production activation service.
    defaults=tuple(c.DefaultEntry(role,c.Requirement(cap,threshold)) for role,cap,threshold in raw.execute('SELECT role,capability,required_role FROM role_capabilities'))
    catalog=replace(bundle.descriptor,defaults=defaults)
    encoded=s.encode_catalog(catalog);manifest=c.catalog_manifest(catalog)
    raw.execute("UPDATE permission_state SET mode='policy_v1',catalog_version=?,catalog_sha256=?,catalog_json=? WHERE id=1",(catalog.version,manifest.descriptor_sha256,encoded))
    return c._normal_catalog(catalog)


class SuppliedVisibility:
    """Hand-supplied test policy; membershipless administrators remain parameterized."""
    def __init__(self,admin_visible=False):self.admin_visible=admin_visible
    def facts(self,root,scopes,subjects):
        users={x.id:x for x in root.users};orgs=set(root.keys.organizations);companies=dict(root.keys.companies)
        rows=[]
        for who in subjects:
            for scope in scopes:
                present=scope.kind=='hub' or scope.id in (companies if scope.kind=='company' else orgs)
                visible=bool(who in users and present and self.admin_visible and users[who].hub_admin)
                if who in users and present:
                    visible |= any(m.user_id==who and m.revoked_at is None and (
                        scope.kind=='organization' and m.scope_type=='organization' and m.scope_id==scope.id or
                        scope.kind=='future_company' and m.scope_type=='organization' and m.scope_id==scope.id or
                        scope.kind=='company' and (m.scope_type=='company' and m.scope_id==scope.id or m.scope_type=='organization' and m.scope_id==companies.get(scope.id))) for m in root.memberships)
                rows.append(a.Visibility(who,scope,bool(visible)))
        return s.VisibilityFacts('owned-explicit-v1',tuple(rows))


@pytest.fixture
def path(tmp_path):
    value=tmp_path/'hub.db';create_hub(value,HEADS['hub'])
    with open_database(value,writable=True) as db:install_fixture_policy(db.raw)
    return value


def test_complete_root_custom_defaults_and_strict_roundtrip(path):
    with open_database(path,writable=False) as db:
        before=snapshot(db.raw);root=s.load_root(db,catalog=BUNDLE)
        assert root.keys.users==('G','H','I','J','Q','S')
        assert root.keys.organizations==('O','Z') and root.keys.companies==( ('C','O'),('D','O'),('E','Z'))
        assert root.keys.authorities==('G','J') and len(root.keys.memberships)==4 and len(root.keys.assignments)==3
        assert c.DefaultEntry('standard',c.Requirement('ledger.post','standard')) in BUNDLE.descriptor.defaults
        assert c.DefaultEntry('standard',c.Requirement('ledger.post','standard')) not in root.role_defaults
        encoded=s.encode_catalog(root.catalog)
        assert s.decode_catalog(encoded,version=root.catalog.version,sha256=root.stamp.catalog_sha256)==root.catalog
        assert 'private-password-hash' not in repr(root) and 'TTTTTTTT' not in repr(root)
        assert snapshot(db.raw)==before
        pair=s.assemble_pair(root,root,old_catalog=BUNDLE,new_catalog=BUNDLE,visibility=SuppliedVisibility())
        assert len(pair.comparison.scopes)==8 and len(pair.comparison.old.memberships)==6*5
        agents={x.id:x.value for x in pair.comparison.old.agents}
        assert agents['G'].eligible_humans==('H',) and agents['J'].eligible_humans==()
        assert next(x.value for x in pair.comparison.old.subjects if x.id=='J').active is False


@pytest.mark.parametrize('edit',['missing','extra','duplicate','bool','scalar','unknown','delete-default','wrong-sha','wrong-version'])
def test_strict_catalog_rejects_invalid_or_unbound_fields(edit):
    value=asdict(BUNDLE.descriptor);raw=None;sha=c.FROZEN_MANIFEST.descriptor_sha256;version=BUNDLE.descriptor.version
    if edit=='missing':value.pop('commands')
    if edit=='extra':value['secret-private-value']='private'
    if edit=='duplicate':raw='{"version":"secret-value","version":"again"}'
    if edit=='bool':value['commands'][0]['available']=1
    if edit=='scalar':value['commands']=None
    if edit=='unknown':value['defaults']=(*value['defaults'],dict(role='standard',requirement=dict(capability='private-invalid',threshold='standard')))
    if edit=='delete-default':value['defaults']=(*value['defaults'],dict(role='hub_admin',requirement=dict(capability='transaction.invoice.delete',threshold='standard')))
    if edit=='wrong-sha':sha='0'*64
    if edit=='wrong-version':version='different'
    with pytest.raises(s.SnapshotError) as caught:s.decode_catalog(raw or json.dumps(value),version=version,sha256=sha)
    assert 'secret' not in str(caught.value) and 'private-invalid' not in str(caught.value)


def test_catalog_tuple_permutations_normalize_by_A_rank():
    permuted=replace(BUNDLE.descriptor,defaults=tuple(reversed(BUNDLE.descriptor.defaults)),commands=tuple(reversed(BUNDLE.descriptor.commands)))
    assert s.encode_catalog(permuted)==s.encode_catalog(BUNDLE.descriptor)
    decoded=s.decode_catalog(json.dumps(asdict(permuted)),version=permuted.version,sha256=c.FROZEN_MANIFEST.descriptor_sha256)
    assert decoded==c._normal_catalog(BUNDLE.descriptor)


@pytest.mark.parametrize('change',[
    "DELETE FROM agent_authority WHERE agent_user_id='J'",
    "UPDATE agent_principals SET principal_user_id='G' WHERE principal_user_id='I'",
    "UPDATE companies SET organization_id='absent' WHERE id='C'",
    "UPDATE memberships SET scope_id='absent' WHERE id='M'",
    "UPDATE users SET kind='unknown' WHERE id='Q'",
    "UPDATE users SET active=2 WHERE id='Q'",
    "UPDATE memberships SET role='unknown' WHERE id='R'",
    "UPDATE memberships SET grants='null' WHERE id='R'",
    "UPDATE memberships SET grants='[\"ledger.read\",\"ledger.read\"]' WHERE id='M'",
    "UPDATE memberships SET grants='[\"ledger.read\"]',denies='[\"ledger.read\"]' WHERE id='M'",
    "INSERT INTO role_capabilities VALUES('standard','unknown','standard')",
    "INSERT INTO role_capabilities VALUES('hub_admin','transaction.invoice.delete','standard')",
    "DELETE FROM role_capabilities WHERE role='readonly' AND capability='ledger.read'",
])
def test_invalid_complete_facts_fail_safely_without_repair(path,change):
    # Ordinary synthetic bad input fixtures; never a real root or guard removal.
    with sqlite3.connect(path) as raw:raw.execute(change)
    with open_database(path,writable=False) as db:
        before=snapshot(db.raw)
        with pytest.raises(s.SnapshotError):s.load_root(db,catalog=BUNDLE)
        assert snapshot(db.raw)==before


def test_dense_removed_reparented_and_new_catalog_union(path,tmp_path):
    other=tmp_path/'new.db';create_hub(other,HEADS['hub'])
    new_catalog=replace(without_capability(BUNDLE.descriptor,'transaction.payment.delete'),version='owned-new')
    new_bundle=replace(BUNDLE,source_commit='a'*40,descriptor=new_catalog)
    with open_database(other,writable=True) as db:
        db.raw.execute("DELETE FROM memberships WHERE scope_id='E'")
        db.raw.execute("DELETE FROM companies WHERE id='E'")
        db.raw.execute("UPDATE companies SET organization_id='Z' WHERE id='D'")
        db.raw.execute("DELETE FROM agent_principals WHERE principal_user_id='I'")
        db.raw.execute("DELETE FROM users WHERE id='I'")
        install_fixture_policy(db.raw,new_bundle)
    with open_database(path,writable=False) as left,open_database(other,writable=False) as right:
        old=s.load_root(left,catalog=BUNDLE);new=s.load_root(right,catalog=new_bundle)
        pair=s.assemble_pair(old,new,old_catalog=BUNDLE,new_catalog=new_bundle,visibility=SuppliedVisibility())
        assert next(x for x in pair.comparison.new.companies if x.id=='E')==a.CompanySlot('E',False,None)
        assert next(x for x in pair.comparison.new.companies if x.id=='D').organization=='Z'
        assert next(x for x in pair.comparison.new.subjects if x.id=='I').value is None
        assert c.ScopeKey('future_company','Z') in pair.comparison.scopes
        assert any(x.kind=='capability' and x.key==('transaction.payment.delete',) and x.new_value is None for x in pair.comparison.catalog_slots)
        assert len(pair.comparison.old.memberships)==len(pair.comparison.new.memberships)==30


def test_snapshot_pins_keys_rows_and_defaults_across_interleaved_writer(path,monkeypatch):
    original=s._read_rows;changed=False
    def interleave(raw,table,kind):
        nonlocal changed
        if not changed:
            changed=True
            with open_database(path,writable=True) as writer:
                writer.raw.execute('BEGIN IMMEDIATE')
                writer.raw.execute("UPDATE users SET active=0 WHERE id='Q'")
                writer.raw.execute("DELETE FROM role_capabilities WHERE role='readonly' AND capability='ledger.read'")
                install_fixture_policy(writer.raw)
                writer.raw.execute('COMMIT')
        return original(raw,table,kind)
    monkeypatch.setattr(s,'_read_rows',interleave)
    with open_database(path,writable=False) as db:
        old=s.load_root(db,catalog=BUNDLE)
        assert next(x for x in old.users if x.id=='Q').active
        assert c.DefaultEntry('readonly',c.Requirement('ledger.read','member')) in old.role_defaults
    with open_database(path,writable=False) as db:
        new=s.load_root(db,catalog=BUNDLE)
        assert not next(x for x in new.users if x.id=='Q').active
        assert c.DefaultEntry('readonly',c.Requirement('ledger.read','member')) not in new.role_defaults


def test_partial_row_materialization_cannot_supply_its_own_manifest(path,monkeypatch):
    original=s._read_rows
    monkeypatch.setattr(s,'_read_rows',lambda raw,table,kind:tuple(x for x in original(raw,table,kind) if table!='users' or x.id!='S'))
    with open_database(path,writable=False) as db:
        with pytest.raises(s.SnapshotError,match='source_incomplete'):s.load_root(db,catalog=BUNDLE)


def test_visibility_remains_explicit_complete_and_parameterized(path):
    with open_database(path,writable=False) as db:root=s.load_root(db,catalog=BUNDLE)
    for provider in (None,object()):
        with pytest.raises(s.SnapshotError,match='visibility_unresolved'):s.assemble_pair(root,root,old_catalog=BUNDLE,new_catalog=BUNDLE,visibility=provider)
    class Incomplete:
        def facts(self,*args):return s.VisibilityFacts('explicit',())
    with pytest.raises(s.SnapshotError):s.assemble_pair(root,root,old_catalog=BUNDLE,new_catalog=BUNDLE,visibility=Incomplete())
    left=s.assemble_pair(root,root,old_catalog=BUNDLE,new_catalog=BUNDLE,visibility=SuppliedVisibility(False))
    right=s.assemble_pair(root,root,old_catalog=BUNDLE,new_catalog=BUNDLE,visibility=SuppliedVisibility(True))
    target=lambda pair:next(x.visible for x in pair.comparison.old.visibility if x.subject=='H' and x.scope==c.ScopeKey('company','E'))
    assert not target(left) and target(right)


def test_legacy_preflight_is_not_actual_legacy_activation(path):
    with open_database(path,writable=True) as db:
        with pytest.raises(s.SnapshotError,match='snapshot_required'):s.load_root(db,catalog=BUNDLE)
        db.raw.execute("UPDATE permission_state SET mode='legacy',catalog_version=NULL,catalog_sha256=NULL,catalog_json=NULL")
    with open_database(path,writable=False) as db:
        root=s.load_root(db,catalog=BUNDLE)
        with pytest.raises(s.SnapshotError,match='legacy_comparison_unavailable'):s.assemble_pair(root,root,old_catalog=BUNDLE,new_catalog=BUNDLE,visibility=SuppliedVisibility())


def test_empty_actual_defaults_do_not_fall_back_to_shipped_catalog(path):
    with open_database(path,writable=True) as db:
        db.raw.execute('DELETE FROM role_capabilities')
        install_fixture_policy(db.raw)
    with open_database(path,writable=False) as db:
        root=s.load_root(db,catalog=BUNDLE)
        assert root.role_defaults==root.catalog.defaults==root.keys.defaults==()


@pytest.mark.parametrize('column,value',[('catalog_sha256','0'*64),('catalog_version','unreviewed'),('catalog_json','{"private":"malformed"}')])
def test_stored_catalog_build_mismatch_never_rewrites_root(path,column,value):
    with open_database(path,writable=True) as db:db.raw.execute('UPDATE permission_state SET '+column+'=?',(value,))
    with open_database(path,writable=False) as db:
        before=snapshot(db.raw)
        with pytest.raises(s.SnapshotError):s.load_root(db,catalog=BUNDLE)
        assert snapshot(db.raw)==before


def test_comparison_rejects_changed_bundle_provenance_and_missing_rows(path):
    with open_database(path,writable=False) as db:root=s.load_root(db,catalog=BUNDLE)
    for bundle in (replace(BUNDLE,source_commit='f'*40),replace(BUNDLE,source_inventory_digest='e'*64),replace(BUNDLE,exclusions=())):
        with pytest.raises(s.SnapshotError,match='catalog_mismatch'):
            s.assemble_pair(root,root,old_catalog=bundle,new_catalog=BUNDLE,visibility=SuppliedVisibility())
    with pytest.raises(s.SnapshotError,match='source_incomplete'):
        s.assemble_pair(root,replace(root,users=root.users[:-1]),old_catalog=BUNDLE,new_catalog=BUNDLE,visibility=SuppliedVisibility())
