"""Private current-policy bridge. No legacy activation or public access routing.

Visibility is governed by memberships, not installation administration. Record
and conditional source owners still perform their complete target graph checks.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_catalog as c, permission_policy as a, permission_snapshot as s
from .agent_authority import fail

# Verified accepted base for the resource inventory, not this module's final
# commit (which cannot name itself). Union review verifies actual call-site parity.
SOURCE_COMMIT = '3990636d2a2d3c683a0e806e6635a4a64c0075b0'


# Explicit source-only delta from the accepted descriptor. No capability,
# threshold, default, action or availability policy changes. Existing roots must
# go through the separately admitted catalog transition, never automatic adoption.
_PAYMENT_LINES = {
    'authorize_event': (415,), 'authorize_events': (430,), 'denied_events': (448,),
    'authorize_publication_transactions': (525, 527),
    'authorize_publication_selections': (675,),
}
CURRENT_SOURCES = tuple(sorted((
    c.ResourceSource('bookflow.company.transaction_deletion_facts.admit',
        (('src/bookflow/company/transaction_deletion_facts.py', 100),
         ('src/bookflow/company/transaction_deletion_facts.py', 101)),
        (c.Requirement('transaction.journal_entry.delete','standard'),
         c.Requirement('transaction.invoice.delete','standard'),
         c.Requirement('transaction.sales_receipt.delete','standard'),
         c.Requirement('transaction.payment.delete','standard'),
         c.Requirement('ledger.read','member'))),
    c.ResourceSource('bookflow.company.transaction_deletion_facts.load',
        (('src/bookflow/company/transaction_deletion_facts.py', 152),),
        (c.Requirement('customer-work','standard'),)),
    *(replace(source, call_sites=tuple(('src/bookflow/company/payment_authority.py', line)
        for line in _PAYMENT_LINES[source.owner.rsplit('.', 1)[-1]]))
      if source.owner.startswith('bookflow.company.payment_authority.') and source.owner.rsplit('.', 1)[-1] in _PAYMENT_LINES
      else source for source in c.CONDITIONAL_RESOURCE_SOURCES),
    c.ResourceSource('bookflow.company.reconciliation_adapters.authority',
        (('src/bookflow/company/reconciliation_adapters.py', 80),), (c.Requirement('ledger.read','member'),)),
    c.ResourceSource('bookflow.company.reconciliation_adapters.population',
        (('src/bookflow/company/reconciliation_adapters.py', 261),), (c.Requirement('ledger.read','member'),)),
    c.ResourceSource('bookflow.company.reconciliation_adapters.prepare_prospective',
        (('src/bookflow/company/reconciliation_adapters.py', 393),), (c.Requirement('customer-work','member'),)),
    c.ResourceSource('bookflow.company.reconciliation_proposals.preview',
        (('src/bookflow/company/reconciliation_proposals.py', 37),), (c.Requirement('ledger.post','standard'),)),
), key=lambda source: source.owner))
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
    """Fresh complete policy_v1 observation on the caller's transaction."""
    bundle = catalog_bundle()
    try:
        root = s.load_root(tx, catalog=bundle)
        return s.observe_pair(root, root, old_catalog=bundle, new_catalog=bundle,
                              visibility=VISIBILITY)
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
