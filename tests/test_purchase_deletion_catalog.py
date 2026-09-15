"""Literal activated purchase delta and full executable parity, without a count pin."""
from bookflow.core import registry
from bookflow.core.deletion_families import FAMILIES, TOMBSTONE_TABLE, capability
from bookflow.hub import permission_catalog as c, permission_payment_deletion_catalog as build, permission_sales_deletion_catalog as previous
from tests.test_permission_catalog import R, owner


def test_complete_purchase_delete_catalog_and_finite_family_availability():
    registry.load_all()
    descriptors=[];actions={}
    for cmd in registry.all_commands(include_standalone=True):
        if cmd.standalone:continue
        descriptors.append(c.CommandDescriptor(cmd.name,cmd.scope,cmd.capability,cmd.required_role or 'authenticated',
            tuple(sorted((R(*x) for x in cmd.resource_requirements),key=lambda x:(x.capability,c.THRESHOLDS.index(x.threshold)))),
            True,cmd.feature,cmd.local_only,cmd.bootstrap,cmd.authorization,owner(cmd.authorize_input),
            owner(cmd.permanent_recovery),owner(cmd.transfer.prepare) if cmd.transfer else None))
        if cmd.scope=='company':
            actions[cmd.name]=({R(cmd.capability,cmd.required_role or 'authenticated'),*(R(*x) for x in cmd.resource_requirements)},
                {x for x in (owner(cmd.plan),owner(cmd.authorize_input),owner(cmd.permanent_recovery),owner(cmd.transfer.prepare) if cmd.transfer else None) if x})
    assert build.CATALOG.commands==tuple(sorted(descriptors,key=lambda x:x.name))
    actual={x.key:x for x in build.CATALOG.company_actions if not x.key.startswith('contract:')}
    assert actual.keys()==actions.keys()
    for name,(requirements,owners) in actions.items():
        assert set(actual[name].requirements)==requirements,name
        assert set(actual[name].remaining_graph_owners)==owners,name
    assert {x.name for x in build.CATALOG.commands}-{x.name for x in previous.CATALOG.commands}=={'payment delete'}
    assert build.CATALOG.defaults==previous.CATALOG.defaults
    assert registry.EXPLICIT_GRANT_ONLY_CAPABILITIES==frozenset(map(capability,FAMILIES))
    contracts={x.key:x for x in build.CATALOG.company_actions if x.key.startswith('contract:')}
    for family in FAMILIES:
        # Deletable exactly where retained-deletion storage exists; journal entries have none.
        assert contracts['contract:delete:'+family].available == (family in TOMBSTONE_TABLE)
        assert set(contracts['contract:delete:'+family].requirements)=={R(capability(family),'standard'),R('ledger.read','member')}
        assert all(x.requirement.capability!=capability(family) for x in build.CATALOG.defaults)


def test_current_conditional_resource_inventory_and_purchase_examples():
    import ast
    from pathlib import Path
    from bookflow.documentation.examples import EXAMPLES
    root=Path(__file__).resolve().parents[1]
    actual={}
    for folder in ('company','commands'):
        for path in (root/'src/bookflow'/folder).rglob('*.py'):
            tree=ast.parse(path.read_text());parents={child:node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
            for node in ast.walk(tree):
                if not isinstance(node,ast.Call):continue
                fn=node.func
                if not ((isinstance(fn,ast.Name) and fn.id=='require_resource') or (isinstance(fn,ast.Attribute) and fn.attr=='require_resource')):continue
                names=[];ancestor=node
                while ancestor in parents:
                    ancestor=parents[ancestor]
                    if isinstance(ancestor,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):names.append(ancestor.name)
                owner='.'.join(path.relative_to(root/'src').with_suffix('').parts)+'.'+'.'.join(reversed(names))
                actual.setdefault(owner,set()).add((str(path.relative_to(root)),node.lineno))
    assert {x.owner:set(x.call_sites) for x in build.CATALOG.conditional_sources}==actual
    registry.load_all()
    for noun in ('check','card-charge','invoice','sales-receipt','payment'):
        cmd=registry.get(noun+' delete')
        assert cmd.explicit_grant_only and cmd.permanent_recovery
        assert cmd.input_model.model_validate(EXAMPLES[cmd.name].input).expected_version==1


def test_manifest_and_native_shape_reuse_only_validated_immutable_descriptors():
    from dataclasses import replace
    import pytest
    from bookflow.hub import permission_snapshot as snapshot
    good=build.CATALOG
    expected=c.catalog_manifest(good)
    assert snapshot._decode(good,c.Catalog,'catalog',native=True) is good
    assert c._check(good,c.Catalog) is None
    assert c.catalog_manifest(replace(good))==expected
    # True == 1 at Python equality must not let an unvalidated descriptor alias
    # the cached valid manifest or the native shape check.
    wrong=replace(good,commands=(replace(good.commands[0],available=1),*good.commands[1:]))
    for check in (lambda:c.catalog_manifest(wrong),lambda:c._check(wrong,c.Catalog)):
        with pytest.raises(c.PolicyInputError):check()
    with pytest.raises(snapshot.SnapshotError):snapshot._decode(wrong,c.Catalog,'catalog',native=True)
    changed=replace(good,commands=(replace(good.commands[0],available=False),*good.commands[1:]))
    assert c.catalog_manifest(changed).descriptor_sha256!=expected.descriptor_sha256
    with pytest.raises(c.PolicyInputError):c.catalog_manifest(good,["not-a-tuple"])


def test_historical_purchase_delta_remains_exact():
    from bookflow.hub import permission_setup_catalog as setup, permission_deletion_catalog as purchase
    assert {x.name for x in purchase.CATALOG.commands}-{x.name for x in setup.CATALOG.commands} == {'check delete','card-charge delete'}
    assert purchase.CATALOG.defaults == setup.CATALOG.defaults
    assert {x.key for x in purchase.CATALOG.company_actions if x.key.startswith('contract:') and x.available} == {'contract:delete:check','contract:delete:card_charge'}
    assert {x.name for x in previous.CATALOG.commands}-{x.name for x in purchase.CATALOG.commands} == {'invoice delete','sales-receipt delete'}
    assert previous.CATALOG.defaults == purchase.CATALOG.defaults
