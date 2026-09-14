"""Private, authenticated legacy transition preparation; no public activation route.

Persistence uses identity_admin.apply_edit's existing savepoint, exact write-set
validation and audit owner. The current public writers are intentionally unchanged.
"""
from dataclasses import replace
from . import identity_admin as b, permission_snapshot as s, permission_catalog as c
from . import permission_runtime as runtime, permission_activation_catalog as build
from . import agent_authority as aa, permission_admin_audit as audit
from .agent_authority import fail, typed, integer, increment
from bookflow.core.ids import new_id


class _TransitionVisibility:
    def facts(self, root, scopes, subjects):
        result = runtime.VISIBILITY.facts(root, scopes, subjects)
        if root.stamp.mode != 'legacy':
            return replace(result, policy_revision='legacy-to-membership-v1')
        observed = s._observe(root, scopes, subjects)
        live = {x.scope for x in observed.scopes if x.logical_present}
        admins = {x.id for x in root.users if x.active and x.hub_admin}
        return s.VisibilityFacts('legacy-to-membership-v1', tuple(
            replace(x, visible=True) if x.subject in admins and x.scope in live else x for x in result.rows))


def _administrators(root):
    """Applicable active human admin/owner coverage, without opening company files."""
    retired_orgs, retired_companies = s._retirement_sets(root.organizations, root.companies)
    users = {x.id for x in root.users if x.active and x.kind == 'human'}
    covered = set()
    for company in root.companies:
        if company.id in retired_companies or company.organization_id in retired_orgs:
            continue
        if any(m.user_id in users and m.revoked_at is None and m.role in ('admin', 'owner') and
               ((m.scope_type == 'company' and m.scope_id == company.id) or
                (m.scope_type == 'organization' and m.scope_id == company.organization_id)) for m in root.memberships):
            covered.add(company.id)
    return {x.id for x in root.companies if x.id not in retired_companies and x.organization_id not in retired_orgs}, covered


def _enroll(old, actor, entries, at, via, preview):
    live, covered = _administrators(old)
    if len({x.company_id for x in entries}) != len(entries):
        fail('invalid_input', 'administrators')
    users = {x.id:x for x in old.users}
    rows = []
    for index, entry in enumerate(entries):
        if entry.company_id not in live or entry.company_id in covered:
            fail('invalid_input', 'administrator_scope')
        user = users.get(entry.user_id)
        if user is None or not user.active or user.kind != 'human':
            fail('invalid_input', 'administrator_user')
        before = next((m for m in old.memberships if m.user_id == user.id and
                       m.scope_type == 'company' and m.scope_id == entry.company_id), None)
        if type(entry.expected) is b.Absent:
            if before is not None: fail('conflict', 'membership_version')
        elif before is None or before.version != entry.expected.value:
            fail('conflict', 'membership_version')
        else:
            integer(entry.expected.value, 'membership_version')
        if before:
            rows.append(replace(before, role='admin', revoked_at=None,
                version=increment(before.version, 'membership_version'), granted_by=actor,
                granted_at=at, updated_at=at, updated_by=actor, updated_via=via))
        else:
            identifier = new_id() if not preview else 'activation-prospective-'+str(index)
            if identifier in {x.id for x in old.memberships}: fail('conflict', 'prospective_identity')
            rows.append(s.MembershipRow(identifier, user.id, 'company', entry.company_id,
                'admin', None, None, actor, at, None, 1, at, actor, via))
    missing = live - covered - {x.company_id for x in entries}
    if missing:
        # Names/paths are not company-book output. The later setup adapter owns
        # any entitled registry presentation; this private refusal is fixed.
        fail('invalid_input', 'company_administrator_required')
    return tuple(rows)


def prepare(tx, *, actor_id, intent, catalog, context=None):
    typed(intent, b.ActivatePolicy, 'intent')
    integer(intent.expected_generation, 'generation')
    selected = build
    if catalog != build.catalog_bundle():
        from . import permission_setup_catalog as selected
    if catalog != selected.catalog_bundle():
        from . import permission_deletion_catalog as selected
    if catalog != selected.catalog_bundle():
        from . import permission_sales_deletion_catalog as selected
    if catalog != selected.catalog_bundle() or intent.expected_catalog_sha256 != selected.MANIFEST.descriptor_sha256:
        fail('catalog_mismatch', 'executable_catalog')
    try:
        old = s.load_root(tx, catalog=catalog)
    except s.SnapshotError as exc:
        b._translate_snapshot(exc)
    actor = next(x for x in old.users if x.id == actor_id)
    if actor.kind != 'human' or not actor.active or not actor.hub_admin:
        fail('not_administrator', 'activation')
    if old.stamp.generation != intent.expected_generation:
        fail('conflict', 'generation')
    preview = context is None
    at, via = ('prospective', 'python') if preview else (context.at, context.interface)
    old_state, old_tokens, old_users = audit.read_state(tx), aa.read_tokens(tx), audit.read_users(tx)
    if old.stamp.mode == 'policy_v1':
        if intent.administrators: fail('invalid_input', 'already_activated')
        pair = s.assemble_pair(old, old, old_catalog=catalog, new_catalog=catalog, visibility=runtime.VISIBILITY)
        reconciliation = aa.reconcile(old, old, pair=pair, actor_id=actor_id, at=at, via=via)
        if reconciliation.authorities: fail('conflict', 'activated_authority')
        derived = s.DerivedFacts(old, old.stamp)
        return b.PreparedEdit(b.VisibleEdit('policy activate', '1', old.stamp.generation, False, preview),
            old, derived, derived, pair, reconciliation, old_tokens, old_tokens, old_state, old_state,
            old_users, old_users, catalog, (), None)
    memberships = _enroll(old, actor_id, intent.administrators, at, via, preview)
    changes = s.ProposalRows(memberships=memberships)
    defaults = tuple(set(old.role_defaults) | set(catalog.descriptor.defaults))
    generation = increment(old.stamp.generation, 'generation')
    try:
        initiating = s.derive_activation(old, catalog=catalog, changes=changes,
            generation=generation, full_defaults=defaults)
        pair = s.observe_activation(old, initiating.root, catalog=catalog,
            visibility=_TransitionVisibility()).snapshot
        reconciliation = aa.reconcile(old, initiating.root, pair=pair, actor_id=actor_id, at=at, via=via)
        final = s.derive_activation(old, catalog=catalog,
            changes=replace(changes, authorities=reconciliation.authorities), generation=generation, full_defaults=defaults)
    except s.SnapshotError as exc:
        b._translate_snapshot(exc)
    final_tokens = aa.revoke_tokens(old_tokens, reconciliation.revoke_agents, actor_id=actor_id, at=at, via=via)
    final_state = replace(old_state, mode='policy_v1', generation=generation,
        catalog_version=final.root.catalog.version, catalog_sha256=final.root.stamp.catalog_sha256,
        catalog_json=s.encode_catalog(final.root.catalog), updated_at=at, updated_by=actor_id, updated_via=via)
    mutations = []
    for table, before_rows, after_rows, keys in (
        ('memberships', old.memberships, final.root.memberships, ('id',)),
        ('agent_authority', old.authorities, final.root.authorities, ('agent_user_id',)),
        ('api_tokens', old_tokens, final_tokens, ('id',))):
        previous = {tuple(getattr(x,k) for k in keys):x for x in before_rows}
        for row in after_rows:
            before = previous.get(tuple(getattr(row,k) for k in keys))
            if row != before: mutations.append(b._mutation(table, before, row, keys))
    for row in final.root.role_defaults:
        if row not in old.role_defaults:
            key = (('role',row.role),('capability',row.requirement.capability),('required_role',row.requirement.threshold))
            mutations.append(b.Mutation('role_capabilities', key, (), key, insert=True))
    mutations.append(b._mutation('permission_state', old_state, final_state, ('id',)))
    event = None if preview else audit.prepare_audit(tx, context=context, actor_id=actor_id,
        command='permission activate', old=old, final=final.root, old_tokens=old_tokens, final_tokens=final_tokens,
        old_users=old_users, final_users=old_users, old_state=old_state, final_state=final_state,
        transitions=reconciliation.comparison.agents)
    return b.PreparedEdit(b.VisibleEdit('policy activate', '1', generation, True, preview),
        old, initiating, final, pair, reconciliation, old_tokens, final_tokens,
        old_state, final_state, old_users, old_users, catalog, tuple(mutations), event)
