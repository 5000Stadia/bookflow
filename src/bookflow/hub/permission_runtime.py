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


def observe_current(tx) -> s.ObservedPair:
    """Fresh complete observation of this root against itself, on the caller's transaction.

    This is a read, not a policy transition: it needs the catalog in force, not a
    stored activation copy of it. A root that has never been activated has no
    such copy and there is no command that would make one, so demanding
    policy_v1 here would leave every install unable to observe its own
    memberships and so unable to read its own audit trail. Administration keeps
    the strict requirement through assemble_pair.
    """
    bundle = catalog_bundle()
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
