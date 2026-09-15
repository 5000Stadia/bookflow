"""Current executable inventory, separate from the immutable legacy descriptor."""
import ast
from bookflow.core import registry
from bookflow.hub import permission_catalog as c, permission_activation_catalog as build, permission_runtime as runtime
from tests.test_permission_catalog import ROOT, R, owner, RESOURCE_PAIRS, FROZEN_DESCRIPTOR_SHA256


def test_complete_unfiltered_registry_descriptors_and_action_owners():
    """Registry parity belongs to the tip; `build` here is a frozen historical delta.

    permission_activation_catalog was accepted long ago and roots have stored its
    descriptor, so it cannot grow to match today's registry and must never be made
    to. This assertion used to name it, which meant a builder who added a command
    and saw it fail was being told to edit an accepted delta -- as fatal as editing
    the frozen ancestor, and not something the ancestor guard in permission_runtime
    covers. tests/test_permission_catalog_history.py is what holds `build` still;
    the delta this file actually owns is asserted below.
    """
    registry.load_all()
    commands = registry.all_commands(include_standalone=True)
    expected = []
    actions = {}
    for cmd in commands:
        if cmd.standalone:
            continue
        expected.append(c.CommandDescriptor(
            cmd.name, cmd.scope, cmd.capability, cmd.required_role or 'authenticated',
            tuple(sorted((R(*r) for r in cmd.resource_requirements), key=lambda r: (r.capability, c.THRESHOLDS.index(r.threshold)))),
            True, cmd.feature, cmd.local_only, cmd.bootstrap, cmd.authorization,
            owner(cmd.authorize_input), owner(cmd.permanent_recovery),
            owner(cmd.transfer.prepare) if cmd.transfer else None))
        if cmd.scope == 'company':
            actions[cmd.name] = (
                {R(cmd.capability, cmd.required_role or 'authenticated'), *(R(*r) for r in cmd.resource_requirements)},
                {x for x in (owner(cmd.plan), owner(cmd.authorize_input), owner(cmd.permanent_recovery), owner(cmd.transfer.prepare) if cmd.transfer else None) if x})
    tip = runtime.current_catalog()
    assert tip.CATALOG.commands == tuple(sorted(expected, key=lambda d: d.name))
    assert tip.MANIFEST.standalone_names == tuple(sorted(cmd.name for cmd in commands if cmd.standalone))
    actual = {a.key: a for a in tip.CATALOG.company_actions if not a.key.startswith('contract:')}
    assert actual.keys() == actions.keys()
    for name, (requirements, owners) in actions.items():
        assert set(actual[name].requirements) == requirements
        assert set(actual[name].remaining_graph_owners) == owners
        assert actual[name].available


def test_every_resource_call_site_has_explicit_owner_disposition():
    sites = {}
    for folder in ('commands', 'company'):
        for path in sorted((ROOT / 'src/bookflow' / folder).rglob('*.py')):
            tree = ast.parse(path.read_text())
            parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert all(a.name != 'require_resource' or a.asname in (None, 'require_resource') for a in node.names), 'new alias needs inventory disposition'
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                if not ((isinstance(fn, ast.Name) and fn.id == 'require_resource') or (isinstance(fn, ast.Attribute) and fn.attr == 'require_resource')):
                    continue
                ancestor = node
                names = []
                while ancestor in parents:
                    ancestor = parents[ancestor]
                    if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        names.append(ancestor.name)
                module = '.'.join(path.relative_to(ROOT / 'src').with_suffix('').parts)
                key = module + '.' + '.'.join(reversed(names))
                sites.setdefault(key, set()).add((str(path.relative_to(ROOT)), node.lineno))
                expected = RESOURCE_PAIRS[key.removeprefix('bookflow.company.')]
                if len(node.args) >= 3 and all(isinstance(a, ast.Constant) for a in node.args[1:3]):
                    assert tuple(a.value for a in node.args[1:3]) in expected
    assert sites.keys() == {'bookflow.company.' + name for name in RESOURCE_PAIRS}
    # The tip's inventory, for the same reason: build.CURRENT_SOURCES was frozen when
    # this delta was accepted and cannot carry a site a later delta introduced.
    inventory = runtime.current_catalog().CATALOG.conditional_sources
    assert {s.owner: set(s.call_sites) for s in inventory} == sites
    assert {s.owner.removeprefix('bookflow.company.'): {(r.capability, r.threshold) for r in s.requirements} for s in inventory} == RESOURCE_PAIRS


def test_literal_delta_keeps_old_descriptors_and_unavailable_contracts():
    old = {x.name:x for x in c.FROZEN_COMMANDS}
    new = {x.name:x for x in build.CATALOG.commands}
    assert {name:new[name] for name in old} == old
    assert new.keys()-old.keys() == {
        'item-receipt post', 'item-receipt query', 'item-receipt show',
        'item-receipt history', 'item-receipt update', 'item-receipt void'}
    assert c.FROZEN_MANIFEST.descriptor_sha256 == FROZEN_DESCRIPTOR_SHA256
    assert build.MANIFEST.standalone_names == c.FROZEN_MANIFEST.standalone_names
    assert set(build.CATALOG.defaults)-set(c.FROZEN_DEFAULTS) == {
        c.DefaultEntry(role,R(cap,floor)) for role,cap,floor in (
            ('admin','membership','authenticated'), ('hub_admin','membership','authenticated'),
            ('owner','membership','authenticated'), ('readonly','membership','authenticated'),
            ('standard','membership','authenticated'), ('hub_admin','user','hub_admin'))}
    # These are the literal effective additions in the already-shipped hub0013.
    path, = (ROOT/'src/bookflow/storage/hub_migrations/versions').glob('0013_*.py')
    tree = ast.parse(path.read_text())
    seed, = [n.value for n in tree.body if isinstance(n,ast.Assign) and any(
        isinstance(t,ast.Name) and t.id=='ROLE_CAPABILITY_SEED' for t in n.targets)]
    assert {c.DefaultEntry(role,R(cap,floor)) for role,cap,floor in ast.literal_eval(seed)} == set(build.CATALOG.defaults)-set(c.FROZEN_DEFAULTS)
    contracts = {x.key:x for x in build.CATALOG.company_actions if x.key.startswith('contract:')}
    assert set(contracts) == {'contract:delete:'+family for family in (
        'check','card_charge','invoice','journal_entry','payment','sales_receipt')}
    for name, action in contracts.items():
        family = name.removeprefix('contract:delete:')
        requirement = R('transaction.'+family+'.delete','standard')
        assert set(action.requirements) == {requirement,R('ledger.read','member')}
        assert not action.available
        assert requirement not in build.MANIFEST.registered_requirements
        assert all(x.requirement != requirement for x in build.CATALOG.defaults)
    assert {x.key:x for x in c.FROZEN_COMPANY_ACTIONS if x.key.startswith('contract:')}.items() <= contracts.items()
