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


# Every accepted version's descriptor sha256, pinned here rather than in the module
# that builds it, for the reason ACCEPTED_ANCESTOR_SHA256 is pinned here: a digest kept
# beside the descriptor it guards gets regenerated along with the descriptor and never
# fires. Each sha below is compared against the manifest that module computes from its
# own CATALOG at import, so this cannot be satisfied by editing a literal.
#
# A root stores the exact descriptor it activated. An accepted delta is therefore as
# frozen as the ancestor is: the ancestor guard above does not cover these, and until
# this check existed only a test did. Verification runs inside known_catalog(), where
# the chain is imported anyway -- at module scope it would force that import on every
# command and cost roughly half a second, against nothing here.
ACCEPTED_DESCRIPTOR_SHA256 = {
    c.SCOPED_POLICY_VERSION: 'a0823e6d9ae98e96c98e2b6e4e73042ecc8ecb7cf0e7d4d9b50e8febc7ed6979',
    c.SETUP_POLICY_VERSION: '5d71ff2a0970f85e12b6fca0f84ff2736176565db44064b0668f84a3c132e2f2',
    c.DELETE_POLICY_VERSION: 'bdcd036b653a165a65279386fc91a6ac8ead2dc45fa49e7791e943067c989f9e',
    c.SALES_DELETE_POLICY_VERSION: 'cedd4c0ae270c83bcbaf2756124adf8d866694c00a8e05c06733282fcc844943',
    c.PAYMENT_DELETE_POLICY_VERSION: '676fb94480d9d413d0233e3f869a479020b1ee3ce20fc03feef02c96d33b4323',
    c.BILL_DELETE_POLICY_VERSION: '6e0a2035aee9edc6ba907f602d531334eadb81b0176a280d237ff840a0c9d487',
    c.CREDIT_CORRECTION_POLICY_VERSION: '53d98dab0d040498fb0837c4de7cd60e30894436b44a2de25aa41a39c9d518d7',
    c.CREDIT_DELETE_POLICY_VERSION: 'b3818cc259b81e4f7ef277d386cfc703d1cab8bbffb499e3025afec51af9bf5b',
    c.DEPOSIT_DELETE_POLICY_VERSION: '449e98a8fa02bb9b5e3b805e70acbb70c0438df79b857a1e804526c2bf40c750',
    c.JOB_TIME_POLICY_VERSION: '0072e030fb0f0b63b11f41c99facfa400a127148080e1fa3a60cbeffa53d0d07',
}
_VERIFIED_ACCEPTED = False


def _verify_accepted(known):
    """Refuse to serve any version whose stored descriptor this build no longer makes."""
    global _VERIFIED_ACCEPTED
    if _VERIFIED_ACCEPTED:
        return
    moved = sorted(version for version, module in known.items()
                   if version in ACCEPTED_DESCRIPTOR_SHA256
                   and module.MANIFEST.descriptor_sha256 != ACCEPTED_DESCRIPTOR_SHA256[version])
    unpinned = sorted(set(known) - set(ACCEPTED_DESCRIPTOR_SHA256))
    if moved:
        raise RuntimeError(
            'An accepted permission catalog version has been edited: %s.\n'
            '\n'
            'A root stores the exact descriptor it activated, and every later read rebuilds\n'
            'that descriptor from the module named here for the stored version. Changing an\n'
            'accepted delta therefore locks out every installation that activated it: each\n'
            'one fails catalog_mismatch on permission_snapshot._load_root, every\n'
            'company-scoped command stops, and `permission activate` cannot repair it\n'
            'because it performs the same read before it can move the root forward. An\n'
            'accepted delta is as frozen as permission_catalog.FROZEN_CATALOG is.\n'
            '\n'
            'If you are adding commands, they belong in a NEW delta on top -- copy\n'
            'permission_credit_correction_catalog.py, give it its own *_POLICY_VERSION, add\n'
            'that constant to SCOPED_POLICY_VERSIONS, register it in known_catalog() below,\n'
            'point current_catalog() at it, and pin it in ACCEPTED_DESCRIPTOR_SHA256 above\n'
            'and in ACCEPTED in tests/test_permission_catalog_history.py. Revert whatever\n'
            'changed in the module(s) named above.'
            % ', '.join(moved))
    if unpinned:
        raise RuntimeError(
            'Catalog version %s is selectable but has no pinned descriptor.\n'
            '\n'
            'Every version known_catalog() can hand back is a descriptor some root may have\n'
            'stored, so each one needs its sha256 in ACCEPTED_DESCRIPTOR_SHA256 above and a\n'
            'line in ACCEPTED in tests/test_permission_catalog_history.py. Add both; do not\n'
            'change a line that is already in either.'
            % ', '.join(unpinned))
    _VERIFIED_ACCEPTED = True


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
    from . import permission_credit_deletion_catalog, permission_deposit_deletion_catalog
    from . import permission_job_time_catalog
    known = {
        c.SCOPED_POLICY_VERSION: permission_activation_catalog,
        c.SETUP_POLICY_VERSION: permission_setup_catalog,
        c.DELETE_POLICY_VERSION: permission_deletion_catalog,
        c.SALES_DELETE_POLICY_VERSION: permission_sales_deletion_catalog,
        c.PAYMENT_DELETE_POLICY_VERSION: permission_payment_deletion_catalog,
        c.BILL_DELETE_POLICY_VERSION: permission_bill_deletion_catalog,
        c.CREDIT_CORRECTION_POLICY_VERSION: permission_credit_correction_catalog,
        c.CREDIT_DELETE_POLICY_VERSION: permission_credit_deletion_catalog,
        c.DEPOSIT_DELETE_POLICY_VERSION: permission_deposit_deletion_catalog,
        c.JOB_TIME_POLICY_VERSION: permission_job_time_catalog,
    }
    # Each module hashed its own descriptor when it was imported just above, so this is
    # a handful of string comparisons, once per process, and no descriptor is hashed for
    # it. See ACCEPTED_DESCRIPTOR_SHA256 for why it is not done at module scope.
    _verify_accepted(known)
    return known.get(version)


def current_catalog():
    """Executable descriptor owner; this accessor does not activate a root."""
    return known_catalog(c.JOB_TIME_POLICY_VERSION)


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
