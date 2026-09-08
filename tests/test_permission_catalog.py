"""Complete source projections and independent literal policy boundaries, no DB."""
import ast
import hashlib
import inspect
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from bookflow.core import registry
from bookflow.hub import permission_catalog as c

ROOT = Path(__file__).resolve().parents[1]
R = c.Requirement


def owner(fn):
    if fn is None:
        return None
    return f'{fn.__module__}.{fn.__qualname__}|{Path(inspect.getsourcefile(fn)).relative_to(ROOT)}'


def test_complete_unfiltered_registry_descriptors_and_action_owners():
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
    assert c.FROZEN_COMMANDS == tuple(sorted(expected, key=lambda d: d.name))
    assert c.FROZEN_MANIFEST.standalone_names == tuple(sorted(cmd.name for cmd in commands if cmd.standalone))
    actual = {a.key: a for a in c.FROZEN_COMPANY_ACTIONS if not a.key.startswith('contract:')}
    assert actual.keys() == actions.keys()
    for name, (requirements, owners) in actions.items():
        assert set(actual[name].requirements) == requirements
        assert set(actual[name].remaining_graph_owners) == owners
        assert actual[name].available


# Hand-disposed conditional resource producers. Graph discovery remains with these
# owners; no target graph is executed or replaced by static permission admission.
RESOURCE_PAIRS = {
    # Accepted G1 private resolver; its literal admission remains ledger.post standard.
    'deposit_resolution.resolve': {('ledger.post', 'standard')},
    'billing_edits.carry_allocations': {('customer-work', 'standard')},
    'billing_edits.protect_sale': {('customer-work', 'standard')},
    'billing_queries.authorize_sale': {('customer-work', 'member'), ('customer-work', 'standard')},
    'billing_queries.sale_source_links': {('customer-work', 'member')},
    'billing_queries.sale_source_output': {('customer-work', 'member')},
    'payment_authority.authorize': {('ledger.read', 'member'), ('ledger.post', 'standard'), ('customer-work', 'member'), ('customer-work', 'standard')},
    # _PublicationSelectionCohort.requirements: read/write ledger, plus work
    # on complete historical selection/operation roots and paid targets.
    'payment_authority.authorize_publication_selections': {('ledger.read', 'member'), ('ledger.post', 'standard'), ('customer-work', 'member'), ('customer-work', 'standard')},
    'payment_authority.authorize_publication_transactions': {('ledger.read', 'member'), ('ledger.post', 'standard'), ('customer-work', 'member'), ('customer-work', 'standard')},
    'payment_authority.authorize_query': {('ledger.read', 'member'), ('ledger.post', 'standard'), ('customer-work', 'member'), ('customer-work', 'standard')},
    'payment_authority.authorize_event': {('ledger.read', 'member'), ('customer-work', 'member')},
    # fe3 publication addition since the plan's c8 source: same read predicates.
    'payment_authority.authorize_events': {('ledger.read', 'member'), ('customer-work', 'member')},
    'payment_authority.denied_events': {('ledger.read', 'member'), ('customer-work', 'member')},
    'payment_authority.readable_predicate': {('customer-work', 'member')},
    'payment_preparation.payment_page': {('customer-work', 'member')},
    'payment_recovery.readable_selection': {('customer-work', 'member')},
}


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
    assert {s.owner: set(s.call_sites) for s in c.CONDITIONAL_RESOURCE_SOURCES} == sites
    assert {s.owner.removeprefix('bookflow.company.'): {(r.capability, r.threshold) for r in s.requirements} for s in c.CONDITIONAL_RESOURCE_SOURCES} == RESOURCE_PAIRS


def test_exact_requirement_universes_and_effective_literal_migration_seeds():
    # Evaluate only the literal seed assignment, not migration modules/upgrades.
    effective = set()
    for revision, replace_all in ((3, False), (4, True), (5, True), (7, False), (8, False), (10, False), (11, False)):
        paths = tuple((ROOT / 'src/bookflow/storage/hub_migrations/versions').glob(f'{revision:04d}_*.py'))
        assert len(paths) == 1
        tree = ast.parse(paths[0].read_text())
        assignments = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'ROLE_CAPABILITY_SEED' for t in n.targets)]
        assert len(assignments) == 1
        if revision in (4, 5):
            # These two frozen seeds spell their literal expansion as tuple(sorted
            # (...)) over literal requirements/ranks. Evaluate only those four
            # assignments; do not import or execute any migration operation.
            names = {'CAPABILITY_REQUIREMENTS', '_ROLE_RANK', '_REQUIRED_RANK', 'ROLE_CAPABILITY_SEED'}
            seed_nodes = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets)]
            assert len(seed_nodes) == 4
            namespace = {'__builtins__': {'tuple': tuple, 'sorted': sorted}}
            exec(compile(ast.Module(body=seed_nodes, type_ignores=[]), str(paths[0]), 'exec'), namespace)
            rows = namespace['ROLE_CAPABILITY_SEED']
        else:
            rows = ast.literal_eval(assignments[0].value)
        if replace_all:
            effective.clear()
        effective.update(rows)
    expected = tuple(c.DefaultEntry(role, R(cap, threshold)) for role, cap, threshold in sorted(effective, key=lambda x: ((*c.ROLES, 'hub_admin').index(x[0]), x[1], c.THRESHOLDS.index(x[2]))))
    assert c.FROZEN_DEFAULTS == expected
    registered = {R(d.capability, d.threshold) for d in c.FROZEN_COMMANDS}
    registered.update(r for d in c.FROZEN_COMMANDS for r in d.resources)
    registered.update(r for s in c.CONDITIONAL_RESOURCE_SOURCES for r in s.requirements)
    assert set(c.FROZEN_MANIFEST.registered_requirements) == registered
    assert {d.requirement for d in expected} <= registered  # no invented legacy atom
    company = {R(d.capability, d.threshold) for d in c.FROZEN_COMMANDS if d.routed_scope == 'company'}
    company.update(r for d in c.FROZEN_COMMANDS for r in d.resources)
    company.update(r for s in c.CONDITIONAL_RESOURCE_SOURCES for r in s.requirements)
    company.update(R(n, 'standard') for n in c.DELETE_NAMES)
    assert set(c.FROZEN_MANIFEST.company_requirements) == company
    assert set(c.FROZEN_MANIFEST.capability_names) == {r.capability for r in registered | company}


def test_explicit_admin_grant_only_and_unavailable_delete_contracts():
    expected_admin = {
        c.AdminAction('admin:users', 'hub', 'hub_admin', True, False),
        c.AdminAction('admin:agents', 'hub', 'hub_admin', True, False),
        c.AdminAction('admin:company:new', 'organization', 'admin', False, True),
    }
    for role, floor in (('readonly', 'admin'), ('standard', 'admin'), ('admin', 'admin'), ('owner', 'owner')):
        for domain in ('organization', 'company'):
            expected_admin.add(c.AdminAction(f'admin:members:{role}:{domain}', domain, floor, True, False))
    for key in ('organization:new', 'organization:rename', 'company:attach', 'company:detach', 'demo:reset'):
        expected_admin.add(c.AdminAction('admin:' + key, 'hub', 'hub_admin', False, True))
    assert set(c.FROZEN_ADMIN_ACTIONS) == expected_admin
    families = ('journal_entry', 'invoice', 'sales_receipt', 'payment')
    assert c.DELETE_NAMES == tuple(f'transaction.{family}.delete' for family in families)
    assert not any(d.requirement.capability in c.DELETE_NAMES for d in c.FROZEN_DEFAULTS)
    contract = {a.key: a for a in c.FROZEN_COMPANY_ACTIONS if a.key.startswith('contract:')}
    assert set(contract) == {'contract:delete:' + f for f in families}
    for family in families:
        a = contract['contract:delete:' + family]
        assert not a.available
        assert set(a.requirements) == {R(f'transaction.{family}.delete', 'standard'), R('ledger.read', 'member')}
        assert set(a.remaining_graph_owners) == {'design/transaction-deletion.md', 'design/permission-resolution.md#delete-disclosure'}
    assert 'transaction.deposit.delete' not in c.FROZEN_MANIFEST.capability_names


def test_frozen_manifest_digest_and_pure_import_boundary():
    assert c.FROZEN_CATALOG.version == 'deposit-lifecycle-co0021-v1'
    assert c.catalog_manifest(c.FROZEN_CATALOG, c.FROZEN_MANIFEST.standalone_names) == c.FROZEN_MANIFEST
    raw = json.dumps(asdict(c.FROZEN_CATALOG), sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    assert hashlib.sha256(raw).hexdigest() == '7e213af6aca70c7836e11c59728ea33542d4528f001d6e1106c9472b414d3bd8'
    for name in ('permission_catalog', 'permission_policy'):
        tree = ast.parse((ROOT / f'src/bookflow/hub/{name}.py').read_text())
        modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert modules <= {'__future__', 'dataclasses', 'typing', 'types', 'enum', 'hashlib', 'json', 'functools', 'permission_catalog'}


@pytest.mark.parametrize('field', ['commands', 'capabilities', 'defaults', 'company_actions', 'admin_actions', 'conditional_sources'])
def test_catalog_duplicate_keys_rejected(field):
    vals = getattr(c.FROZEN_CATALOG, field)
    with pytest.raises(c.PolicyInputError) as e:
        c.catalog_manifest(replace(c.FROZEN_CATALOG, **{field: vals + vals[:1]}))
    assert e.value.category is c.InputErrorCategory.duplicate_key


def test_catalog_references_and_delete_defaults_reject():
    bad = [
        replace(c.FROZEN_CATALOG, defaults=c.FROZEN_DEFAULTS + (c.DefaultEntry('owner', R('transaction.invoice.delete', 'standard')),)),
        replace(c.FROZEN_CATALOG, company_actions=(c.CompanyAction('bad', (R('unknown', 'member'),), True, ()),)),
        replace(c.FROZEN_CATALOG, capabilities=c.FROZEN_CAPABILITIES + (c.CapabilitySpec('bad', ('member', 'member'), ()),)),
        replace(c.FROZEN_CATALOG, conditional_sources=(c.ResourceSource('owner', (('path', True),), ()),)),
    ]
    for value in bad:
        with pytest.raises(c.PolicyInputError):
            c.catalog_manifest(value)
