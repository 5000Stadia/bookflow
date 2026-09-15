"""Private current-policy bridge. No legacy activation or public access routing.

Visibility is governed by memberships, not installation administration. Record
and conditional source owners still perform their complete target graph checks.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_catalog as c, permission_policy as a, permission_snapshot as s
from .agent_authority import fail

# The commit at which call-site parity between the code and the inventory was last
# verified by union review. Not this module's own commit, which cannot name itself.
SOURCE_COMMIT = '3990636d2a2d3c683a0e806e6635a4a64c0075b0'


# The accepted descriptor now carries every conditional source at its current call
# sites, so this bridge holds no delta and CURRENT_SOURCES is the frozen inventory
# itself. The seam stays because the next drift between the code and the descriptor
# lands here first, ahead of the catalog transition that absorbs it. Nothing here may
# change a capability, threshold, default, action or availability; existing roots go
# through that separately admitted transition, never automatic adoption.
CURRENT_SOURCES = c.CONDITIONAL_RESOURCE_SOURCES
CURRENT_CATALOG = replace(c.FROZEN_CATALOG, conditional_sources=CURRENT_SOURCES)
CURRENT_MANIFEST = c.catalog_manifest(CURRENT_CATALOG, c.FROZEN_MANIFEST.standalone_names)


# The frozen ancestor, as every version in known_catalog() below was built on it.
# Each of those versions is replace(previous.CATALOG, ...) down a chain that ends at
# permission_catalog.FROZEN_CATALOG, and an activation stores the exact descriptor it
# accepted, so an edit to that ancestor rewrites descriptors installations already
# stored and those installations can no longer load their own permission state --
# every company-scoped command fails catalog_mismatch and `permission activate`
# cannot repair it, because it performs the same read first.
#
# This value lives here, and not beside the literal it checks, deliberately.
# permission_catalog.FROZEN_MANIFEST carries the same digest, but regenerating that
# literal to match is the second half of the mistake: a copy kept in that file would
# be regenerated along with it and would never fire. The sha compared below is
# computed from the ancestor as it stands now, not read from any literal.
ACCEPTED_ANCESTOR_SHA256 = 'c4b724e114772d9940ead6450a4bd64fd490cd1f45ca42092e647284e2fef123'
# CURRENT_CATALOG is the ancestor with CURRENT_SOURCES substituted, so while this
# bridge holds no delta it *is* the ancestor and its manifest, computed just above,
# is the ancestor's. Reuse it rather than hashing the same descriptor twice on every
# import; the day the bridge does hold a delta, the `is` fails and we hash.
_ANCESTOR_SHA256 = (CURRENT_MANIFEST.descriptor_sha256
                    if CURRENT_SOURCES is c.CONDITIONAL_RESOURCE_SOURCES else
                    c.catalog_manifest(c.FROZEN_CATALOG, c.FROZEN_MANIFEST.standalone_names).descriptor_sha256)
if _ANCESTOR_SHA256 != ACCEPTED_ANCESTOR_SHA256:
    raise RuntimeError(
        'The frozen permission catalog ancestor has been edited.\n'
        '\n'
        'permission_catalog.FROZEN_CATALOG now hashes to %s,\n'
        'where every accepted catalog version was built on %s.\n'
        '\n'
        'If you have just added a command, a company action, a capability or a default\n'
        'to FROZEN_COMMANDS or one of its neighbours in permission_catalog.py, that is\n'
        'the edit, and it is why this refuses to start. That ancestor is shared by every\n'
        'version in known_catalog() below, so adding to it rewrites descriptors that real\n'
        'installations activated and stored -- and such an installation then fails\n'
        'catalog_mismatch on every company-scoped command, with no in-product remedy,\n'
        'because `permission activate` performs the same read before it can move the root\n'
        'forward. Refusing to import is cheaper than shipping that.\n'
        '\n'
        'Put your commands in a new delta module instead: copy\n'
        'permission_credit_correction_catalog.py, give it its own *_POLICY_VERSION, add\n'
        'that constant to SCOPED_POLICY_VERSIONS, register the module in known_catalog()\n'
        'below, point current_catalog() at it, and add one line for the new tip to\n'
        'ACCEPTED in tests/test_permission_catalog_history.py.\n'
        '\n'
        '`git diff src/bookflow/hub/permission_catalog.py` shows exactly what moved. If\n'
        'you are deliberately retiring the ancestor, this constant is what you change,\n'
        'and every pinned version in that test moves with it.'
        % (_ANCESTOR_SHA256, ACCEPTED_ANCESTOR_SHA256))


def catalog_bundle() -> s.CatalogBundle:
    """Explicit executable-build bundle; no registry regeneration or root writes."""
    inventory = dict(manifest=asdict(CURRENT_MANIFEST),
                     sources=[asdict(x) for x in CURRENT_SOURCES])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True,
                                      separators=(',', ':')).encode()).hexdigest()
    return s.CatalogBundle(SOURCE_COMMIT, CURRENT_CATALOG,
                          CURRENT_MANIFEST.standalone_names, digest)


class _MembershipVisibility:
    def facts(self, root, union_scopes, union_subjects):
        # Reuse snapshot's exact raw/logical classifier (including both native
        # separators). It also checks that the observation covers the full root.
        observation = s._observe(root, union_scopes, union_subjects)
        live = {x.scope for x in observation.scopes if x.logical_present}
        users = {x.id: x for x in root.users}
        companies = dict(observation.live_companies)
        memberships = {(x.user_id, x.scope_type, x.scope_id)
                       for x in root.memberships if x.revoked_at is None}
        rows = []
        for who in union_subjects:
            user = users.get(who)
            for scope in union_scopes:
                visible = False
                if user is not None and user.active and scope in live:
                    if scope.kind == 'hub':
                        # Hub identity domain, not company-book access. A keeps
                        # separate human/admin action requirements in this domain.
                        visible = True
                    elif scope.kind == 'company':
                        visible = ((who, 'company', scope.id) in memberships or
                                   (who, 'organization', companies[scope.id]) in memberships)
                    elif scope.kind == 'future_company':
                        visible = (who, 'organization', scope.id) in memberships
                    else:
                        # Parent discovery through an exact company membership
                        # never creates an organization membership or role.
                        visible = ((who, 'organization', scope.id) in memberships or
                                   any(parent == scope.id and (who, 'company', child) in memberships
                                       for child, parent in companies.items()))
                rows.append(a.Visibility(who, scope, visible))
        return s.VisibilityFacts('explicit-membership-retirement-v1', tuple(rows))


VISIBILITY = _MembershipVisibility()


def known_catalog(version):
    """Accepted explicit policy versions; never substitute the current build."""
    from . import permission_activation_catalog, permission_setup_catalog
    from . import permission_deletion_catalog, permission_sales_deletion_catalog
    from . import permission_payment_deletion_catalog, permission_bill_deletion_catalog
    from . import permission_credit_correction_catalog
    return {
        c.SCOPED_POLICY_VERSION: permission_activation_catalog,
        c.SETUP_POLICY_VERSION: permission_setup_catalog,
        c.DELETE_POLICY_VERSION: permission_deletion_catalog,
        c.SALES_DELETE_POLICY_VERSION: permission_sales_deletion_catalog,
        c.PAYMENT_DELETE_POLICY_VERSION: permission_payment_deletion_catalog,
        c.BILL_DELETE_POLICY_VERSION: permission_bill_deletion_catalog,
        c.CREDIT_CORRECTION_POLICY_VERSION: permission_credit_correction_catalog,
    }.get(version)


def current_catalog():
    """Executable descriptor owner; this accessor does not activate a root."""
    return known_catalog(c.CREDIT_CORRECTION_POLICY_VERSION)


def catalog_for_root(tx):
    """Select only a recognized explicitly stored build; legacy stays frozen."""
    state = tx.raw.execute('SELECT mode,catalog_version FROM main.permission_state WHERE id=1').fetchone()
    selected = known_catalog(state[1]) if state[0] == 'policy_v1' else None
    return selected.catalog_bundle() if selected is not None else catalog_bundle()


def observe_current(tx) -> s.ObservedPair:
    """Fresh complete observation of this root against itself, on the caller's transaction.

    This is a read, not a policy transition: it needs the catalog in force, not a
    stored activation copy of it. A root that has never been activated has no
    such copy and there is no command that would make one, so demanding
    policy_v1 here would leave every install unable to observe its own
    memberships and so unable to read its own audit trail. Administration keeps
    the strict requirement through assemble_pair.
    """
    bundle = catalog_for_root(tx)
    try:
        root = s.load_root(tx, catalog=bundle)
        return s.observe_pair(root, root, old_catalog=bundle, new_catalog=bundle,
                              visibility=VISIBILITY, activated=False)
    except s.SnapshotError as exc:
        from .identity_admin import _translate_snapshot
        _translate_snapshot(exc)


def require_company(tx, *, actor: str, principal: str | None, company: str,
                    requirement: c.Requirement) -> a.ExecutionResult:
    """Current actor/human intersection; not a credential or graph certificate.

    The binding producer authenticates immediately before calling this function.
    No stored output of this function can authorize a later transaction.
    """
    observed = observe_current(tx)
    scope = c.ScopeKey('company', company)
    comparison = observed.snapshot.comparison
    if actor not in comparison.subjects or scope not in comparison.scopes:
        fail('unavailable_target', 'scope')
    if principal is not None and principal not in comparison.subjects:
        fail('not_administrator', 'binding')
    try:
        result = a.execution(comparison, phase='old', actor=actor, bound_human=principal,
                             scope=scope, requirement=requirement)
    except c.PolicyInputError:
        fail('invalid_input', 'requirement')
    if not result.intersection_admitted:
        fail('unavailable_target', 'scope')
    return result
