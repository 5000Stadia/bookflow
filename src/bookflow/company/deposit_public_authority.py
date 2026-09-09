"""Sealed public deposit audience over an authenticated reader and its own binding.

This is a request-local sibling of AuditAudience, not a widening of it. Rule
ownership stays with the shared permission catalog/policy owners; this module
only asks them the questions the public deposit projector needs, using the
closed reference vocabulary the legacy audit projection already owns.
"""
from pathlib import Path

from bookflow.core.errors import BookflowError
from bookflow.core.identity_admin_binding import BoundReader
from bookflow.core.publication import OSBinding
from bookflow.hub import permission_catalog as c, permission_policy as policy

# Reading a deposit at all.
READ_REQUIREMENT = ('ledger.read', 'member')

# Closed navigation kind -> (audit record kind, catalog capability). The private
# reader emits exactly these navigation tables; conform() pins the mapping to the
# existing audit resource vocabulary so a new list noun cannot arrive untyped.
REFERENCE_CAPABILITIES = {
    'accounts': ('account', 'account'),
    'customers': ('customer', 'customer'),
    'vendors': ('vendor', 'vendor'),
    'employees': ('employee', 'employee'),
    'other_names': ('other_name', 'other-name'),
    'payment_methods': ('payment_method', 'payment-method'),
    'classes': ('class', 'class'),
}

# Public wire group name for each navigation table.
REFERENCE_GROUPS = {
    'accounts': 'account',
    'customers': 'customer',
    'vendors': 'vendor',
    'employees': 'employee',
    'other_names': 'other_name',
    'payment_methods': 'payment_method',
    'classes': 'class',
}

PARTY_TABLES = {'customer': 'customers', 'vendor': 'vendors',
                'employee': 'employees', 'other_name': 'other_names'}

CUSTOM_FIELD_CAPABILITY = 'custom-field'
COMPANY_CAPABILITY = 'company'
ANNOTATION_CAPABILITIES = {'notes': 'note', 'attachments': 'attachment'}

_SEAL = object()


def conform():
    """Fail loudly if the shared resource vocabulary moves under this module."""
    from bookflow.hub import audit_projection_legacy as legacy
    expected = {kind: capability for kind, capability in REFERENCE_CAPABILITIES.values()}
    for kind, capability in expected.items():
        if legacy._LIST_NOUNS.get(kind) != capability:
            raise BookflowError('E_INTERNAL', message='deposit reference vocabulary changed')
    if legacy.entry_requirement('deposit') != (READ_REQUIREMENT,):
        raise BookflowError('E_INTERNAL', message='deposit read requirement changed')
    for name, capability in ANNOTATION_CAPABILITIES.items():
        if legacy.entry_requirement(name[:-1]) != ((capability, 'member'),):
            raise BookflowError('E_INTERNAL', message='annotation requirement changed')
    if legacy.entry_requirement('custom_field_value') != ((CUSTOM_FIELD_CAPABILITY, 'member'),):
        raise BookflowError('E_INTERNAL', message='custom field requirement changed')
    if legacy.entry_requirement('company_info') != ((COMPANY_CAPABILITY, 'member'),):
        raise BookflowError('E_INTERNAL', message='issuer requirement changed')
    if set(REFERENCE_GROUPS) != set(REFERENCE_CAPABILITIES):
        raise BookflowError('E_INTERNAL', message='reference group table mismatch')


class DepositAudience:
    """Current actor/principal intersection for one public deposit request.

    Constructed only through :func:`audience`, from a live BoundReader and the
    genuine OS/HTTP binding execution already holds. The binding is kept for the
    private financial owners; it is never serialized, hashed or published.
    """

    def __init__(self, reader, binding, *, _seal=None):
        if _seal is not _SEAL:
            raise BookflowError('E_UNAUTHENTICATED')
        if type(reader) is not BoundReader:
            raise BookflowError('E_UNAUTHENTICATED')
        conform()
        observed = reader.observe()
        identity = observed.identity
        _agree(identity, binding)
        self.reader = reader
        self.identity = identity
        self.observation = observed.observation
        self.comparison = observed.observation.snapshot.comparison
        self.company = None
        self._binding = binding
        self._signatures = {}
        self._answers = {}

    # -- lifetime ---------------------------------------------------------
    def binding(self):
        """The retained execution binding, after re-checking the reader."""
        self.validate()
        return self._binding

    def validate(self):
        if self.reader.authenticate() != self.identity:
            raise BookflowError('E_UNAUTHENTICATED')

    # -- policy -----------------------------------------------------------
    def subjects(self):
        i = self.identity
        return (i.actor,) if i.principal is None else (i.actor, i.principal)

    def signature(self, subject, scope):
        if subject not in self.comparison.subjects or scope not in self.comparison.scopes:
            return None
        if subject not in self._signatures:
            self._signatures[subject] = policy.signature(self.comparison, phase='old', subject=subject)
        return next((x for x in self._signatures[subject].scopes if x.scope == scope), None)

    def visible(self, scope):
        return all((x := self.signature(subject, scope)) is not None and x.effective_visible
                   for subject in self.subjects())

    def admits(self, capability, threshold='member', *, company=None):
        """Current intersection answer for one catalog requirement; no raising."""
        identifier = self.company if company is None else company
        if identifier is None:
            raise BookflowError('E_INTERNAL', message='no company selected for this audience')
        key = (identifier, capability, threshold)
        if key not in self._answers:
            scope = c.ScopeKey('company', identifier)
            if not self.visible(scope):
                self._answers[key] = False
            else:
                answer = policy.execution(self.comparison, phase='old', actor=self.identity.actor,
                                          bound_human=self.identity.principal, scope=scope,
                                          requirement=c.Requirement(capability, threshold))
                self._answers[key] = bool(answer.intersection_admitted)
        return self._answers[key]

    def require(self, company, requirements=(READ_REQUIREMENT,)):
        scope = c.ScopeKey('company', company)
        if not self.visible(scope):
            raise BookflowError('E_PERMISSION')
        for capability, threshold in requirements:
            if not self.admits(capability, threshold, company=company):
                raise BookflowError('E_PERMISSION')
        self.company = company
        self.validate()
        return company

    # -- closed reference groups -----------------------------------------
    def reference_admitted(self, table):
        """Is this reader admitted to the list resource behind a navigation kind?"""
        entry = REFERENCE_CAPABILITIES.get(table)
        if entry is None:
            raise BookflowError('E_INTERNAL', message='unknown deposit reference kind')
        return self.admits(entry[1])

    def party_admitted(self, party_kind):
        table = PARTY_TABLES.get(party_kind)
        if table is None:
            raise BookflowError('E_INTERNAL', message='unknown deposit party kind')
        return self.reference_admitted(table)

    def custom_fields_admitted(self):
        return self.admits(CUSTOM_FIELD_CAPABILITY)

    def issuer_admitted(self):
        return self.admits(COMPANY_CAPABILITY)

    def annotation_access(self):
        """Capability-derived availability, decided before any association read."""
        return {name: ('available' if self.admits(capability) else 'unavailable')
                for name, capability in ANNOTATION_CAPABILITIES.items()}


def _agree(identity, binding):
    """The supplied binding must be this reader's own authenticated producer."""
    from bookflow.adapters.http.app import Credential
    if type(binding) not in (OSBinding, Credential):
        raise BookflowError('E_UNAUTHENTICATED')
    if (binding.user_id, binding.actor_kind, binding.on_behalf_of) != (
            identity.actor, identity.actor_kind, identity.principal):
        raise BookflowError('E_UNAUTHENTICATED')
    if type(binding) is OSBinding:
        if identity.credential_kind != 'os' or binding.token_id is not None:
            raise BookflowError('E_UNAUTHENTICATED')
        if Path(binding.root) != Path(identity.root):
            raise BookflowError('E_UNAUTHENTICATED')
    else:
        if identity.credential_kind == 'os' or binding.token_id != identity.token_id:
            raise BookflowError('E_UNAUTHENTICATED')


def audience(reader, binding):
    """Private constructor for trusted execution: a live reader plus its binding."""
    return DepositAudience(reader, binding, _seal=_SEAL)
