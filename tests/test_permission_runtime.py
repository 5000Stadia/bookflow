"""Complete governed membership observations; real transaction/credential bridge."""
from dataclasses import replace
import pytest

from bookflow.hub import permission_runtime as r, permission_snapshot as s, permission_catalog as c, identity_admin as b
from bookflow.core import identity_admin_binding as producer
from bookflow.core.config import Config, os_login
from bookflow.core.context import client_version
from bookflow.core.host import Host
from bookflow.core.publication import OSBinding
from bookflow.storage.engine import open_database
from tests.permission_admin_support import make_root, snapshot, CONTEXT
from tests.test_permission_snapshots import install_fixture_policy


@pytest.fixture
def path(tmp_path):
    path=make_root(tmp_path/'hub.db')
    with open_database(path,writable=True) as db:
        db.raw.execute('DELETE FROM role_capabilities')
        db.raw.executemany('INSERT INTO role_capabilities(role,capability,required_role) VALUES(?,?,?)',
            [(x.role,x.requirement.capability,x.requirement.threshold) for x in c.FROZEN_DEFAULTS])
        install_fixture_policy(db.raw,r.catalog_bundle())
    config=Config(tmp_path/'config.toml');config.set_user(os_login(),'H');config.save()
    return path


def test_full_scope_subject_visibility_including_admin_and_inactive(path):
    with open_database(path,writable=False) as db:
        before=snapshot(db.raw);observed=r.observe_current(db)
        actual={(v.subject,v.scope.kind,v.scope.id):v.visible for v in observed.snapshot.comparison.old.visibility}
        subjects=('A','B','G','H','I','J','P','Q','R','RO','S','U','W')
        domains=(('hub','root'),('organization','O'),('organization','Z'),('future_company','O'),('future_company','Z'),('company','C'),('company','D'),('company','E'))
        expected={}
        for who in subjects:
            for kind,identifier in domains:
                allowed=(who!='I' and kind=='hub') or (who in ('A','B','G','P','Q','R','RO','W') and identifier in ('O','C','D')) or (who in ('J','U') and identifier in ('Z','E'))
                expected[who,kind,identifier]=allowed
        assert actual==expected
        assert observed.snapshot.old.keys==observed.snapshot.proposed.keys
        assert snapshot(db.raw)==before


@pytest.mark.parametrize('pending',['trash/retired',r'trash\retired'])
def test_raw_retirement_complete_false_without_losing_members(path,pending):
    with open_database(path,writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE')
        old=s.load_root(db,catalog=r.catalog_bundle())
        replacement=replace(old.organizations[0],pending_path=pending)
        proposed=s.derive_scope_proposal(old,old_catalog=r.catalog_bundle(),new_catalog=r.catalog_bundle(),
            scopes=s.ScopeRows(organizations=(replacement,)),changes=s.ProposalRows(),generation=old.stamp.generation)
        observed=s.observe_pair(old,proposed.root,old_catalog=r.catalog_bundle(),new_catalog=r.catalog_bundle(),visibility=r.VISIBILITY)
        assert proposed.root.memberships==old.memberships
        assert {x.scope.id for x in observed.new_observation.scopes if x.raw_present and not x.logical_present}=={'O','C','D'}
        assert {x.scope.id for x in observed.new_observation.scopes if x.logical_present}=={'root','Z','E'}
        assert all(not x.visible for x in observed.snapshot.comparison.new.visibility if x.scope.id in ('O','C','D'))
        assert proposed.base_stamp==old.stamp


def test_company_only_parent_discovery_not_sibling_future_or_org_role(path):
    with open_database(path,writable=True) as db:
        db.raw.execute("UPDATE memberships SET scope_type='company',scope_id='C' WHERE id='M-A'")
        db.raw.execute('BEGIN')
        pair=r.observe_current(db).snapshot
        actual={v.scope:v.visible for v in pair.comparison.old.visibility if v.subject=='A'}
        assert actual=={c.ScopeKey(k,i):v for k,i,v in [('hub','root',True),('organization','O',True),('organization','Z',False),('future_company','O',False),('future_company','Z',False),('company','C',True),('company','D',False),('company','E',False)]}
        from bookflow.hub.permission_policy import signature
        organization=next(x for x in signature(pair.comparison,phase='old',subject='A').scopes if x.scope==c.ScopeKey('organization','O'))
        assert organization.membership_role is None
        assert not any(bit for _,bit in organization.admin_bits)


@pytest.mark.parametrize('changed',['none','principal_deny','actor_deny','suspended','revoked_assignment','old_epoch'])
def test_real_host_token_actor_principal_conjunction(path,changed):
    with open_database(path,writable=True) as db:
        if changed=='principal_deny':db.raw.execute("UPDATE memberships SET denies='[\"ledger.read\"]' WHERE id='M-P'")
        if changed=='actor_deny':db.raw.execute("UPDATE memberships SET denies='[\"ledger.read\"]' WHERE id='M-G'")
        if changed=='suspended':db.raw.execute("UPDATE agent_authority SET suspended_at='2026-01-01' WHERE agent_user_id='G'")
        if changed=='revoked_assignment':db.raw.execute("UPDATE agent_principals SET revoked_at='2026-01-01' WHERE agent_user_id='G' AND principal_user_id='P'")
        if changed=='old_epoch':db.raw.execute("UPDATE api_tokens SET authority_epoch=6 WHERE id='GP-live'")
    host=Host(path.parent,version=client_version());host.start()
    try:
        admitted=b.TokenBinding('secret-GP-live','GP-live','G','bearer','P',path,'REQUEST')
        def job():
            before=snapshot(host._hub.raw)
            def check():
                with producer.hosted_operation(host,admitted,request_id='REQUEST',purpose='preview') as operation:
                    result=operation.require_company('C',c.Requirement('ledger.read','member'))
                    assert result.actor_admitted and result.principal_admitted and result.intersection_admitted
                    with pytest.raises(b.AdministrationError,match='not_administrator'):
                        operation.preview(b.SetUserActive('R',5,False))
            if changed=='none':check()
            else:
                from bookflow import BookflowError
                with pytest.raises((b.AdministrationError,BookflowError)):check()
            assert snapshot(host._hub.raw)==before
        host.submit(job)
    finally:host.stop()


def test_complete_current_catalog_source_delta_no_policy_rewrite():
    import ast
    from pathlib import Path
    from tests.test_permission_catalog import RESOURCE_PAIRS, test_complete_unfiltered_registry_descriptors_and_action_owners
    test_complete_unfiltered_registry_descriptors_and_action_owners()
    assert replace(r.CURRENT_CATALOG,conditional_sources=c.CONDITIONAL_RESOURCE_SOURCES)==c.FROZEN_CATALOG
    from bookflow.company.transaction_deletion_facts import FAMILIES
    assert FAMILIES==('journal_entry','invoice','sales_receipt','payment')
    expected=dict(RESOURCE_PAIRS, **{
        'transaction_deletion_facts.admit':{
            ('transaction.journal_entry.delete','standard'),
            ('transaction.invoice.delete','standard'),
            ('transaction.sales_receipt.delete','standard'),
            ('transaction.payment.delete','standard'),('ledger.read','member')},
        'transaction_deletion_facts.load':{('customer-work','standard')},
        'reconciliation_adapters.authority':{('ledger.read','member')},
        'reconciliation_adapters.population':{('ledger.read','member')},
        'reconciliation_adapters.prepare_prospective':{('customer-work','member')},
        'reconciliation_proposals.preview':{('ledger.post','standard')},
    })
    root=Path(__file__).resolve().parents[1];sites={}
    for folder in ('company','commands'):
        for path in (root/'src/bookflow'/folder).rglob('*.py'):
            tree=ast.parse(path.read_text());parents={child:n for n in ast.walk(tree) for child in ast.iter_child_nodes(n)}
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom):assert all(x.name!='require_resource' or x.asname in (None,'require_resource') for x in node.names)
                if not isinstance(node,ast.Call):continue
                fn=node.func
                if not ((isinstance(fn,ast.Name) and fn.id=='require_resource') or (isinstance(fn,ast.Attribute) and fn.attr=='require_resource')):continue
                current=node;names=[]
                while current in parents:
                    current=parents[current]
                    if isinstance(current,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):names.append(current.name)
                key='.'.join(path.relative_to(root/'src').with_suffix('').parts)+'.'+'.'.join(reversed(names))
                assert key.removeprefix('bookflow.company.') in expected
                sites.setdefault(key,set()).add((str(path.relative_to(root)),node.lineno))
    assert {source.owner:set(source.call_sites) for source in r.CURRENT_SOURCES}==sites
    assert {source.owner.removeprefix('bookflow.company.'):{(q.capability,q.threshold) for q in source.requirements} for source in r.CURRENT_SOURCES}==expected
    assert r.CURRENT_MANIFEST==c.catalog_manifest(r.CURRENT_CATALOG,c.FROZEN_MANIFEST.standalone_names)
    assert r.CURRENT_MANIFEST.company_requirements==c.FROZEN_MANIFEST.company_requirements
    assert r.CURRENT_MANIFEST.registered_requirements==c.FROZEN_MANIFEST.registered_requirements


def test_real_os_agent_binding_epoch_refresh_and_no_admin(path):
    config=Config(path.parent/'config.toml');config.set_user(os_login(),'G');config.save()
    host=Host(path.parent,version=client_version());host.start()
    try:
        admitted=OSBinding.capture(host,os_login(),'P')
        def job():
            before=snapshot(host._hub.raw)
            with producer.hosted_operation(host,admitted,request_id='REQUEST',purpose='apply') as operation:
                assert operation.require_company('C',c.Requirement('ledger.read','member')).intersection_admitted
                with pytest.raises(b.AdministrationError,match='not_administrator'):
                    operation.apply(b.SetUserActive('R',5,False),audit=CONTEXT)
                host._hub.raw.execute("UPDATE agent_authority SET epoch=8 WHERE agent_user_id='G'")
                changed=snapshot(host._hub.raw)
                with pytest.raises(b.AdministrationError,match='not_administrator'):
                    operation.require_company('C',c.Requirement('ledger.read','member'))
                assert snapshot(host._hub.raw)==changed
            assert snapshot(host._hub.raw)==before
        host.submit(job)
    finally:host.stop()


@pytest.mark.parametrize('company',['C','D','E','missing'])
def test_hub_admin_without_membership_no_books(path,company):
    host=Host(path.parent,version=client_version());host.start()
    try:
        admitted=OSBinding.capture(host,os_login())
        def job():
            before=snapshot(host._hub.raw)
            with producer.hosted_operation(host,admitted,request_id='REQUEST',purpose='preview') as operation:
                with pytest.raises(b.AdministrationError) as caught:operation.require_company(company,c.Requirement('ledger.read','member'))
                assert caught.value.args==('unavailable_target','scope')
            assert snapshot(host._hub.raw)==before
        host.submit(job)
    finally:host.stop()



def test_hub_admin_without_membership_retains_identity_admin(path):
    host=Host(path.parent,version=client_version());host.start()
    try:
        admitted=OSBinding.capture(host,os_login())
        def job():
            before=snapshot(host._hub.raw)
            with producer.hosted_operation(host,admitted,request_id='REQUEST',purpose='preview') as operation:
                assert operation.preview(b.SetUserActive('R',5,False)).visible.changed
            assert snapshot(host._hub.raw)==before
        host.submit(job)
    finally:host.stop()
