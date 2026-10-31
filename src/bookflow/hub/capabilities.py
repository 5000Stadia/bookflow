"""One permission model (R187, design/principal-directives.md): does this actor hold the capability?

The former people-only acts are capabilities (catalog capabilities-v1). A person holds each at the role that ran
the command before, so nothing here runs for a person: their admission is exactly what it was. An agent holds one
only when all of these are true, asked the same way on every interface:

* the installation's permission rules are activated (grants exist only there);
* one of its own live memberships covering the scope grants the capability by name, and none denies it -- a role
  default is never enough for an agent, which is the one place the actor's kind matters;
* the agent meets the threshold and its principal holds the capability too (the actor/principal intersection the
  policy already computes; for the installation-wide ones, its principal is an active hub administrator).

The five acts no capability could make safe stay a person's (`people_only`), refused with `principal_only`.
"""
from __future__ import annotations

import json

from bookflow.core.errors import BookflowError

from . import people_only
from . import permission_automation_catalog as _automation_v1
from .permission_capabilities_catalog import CAPABILITIES as _V1, CLOSING_DATE_CAPABILITY, COMMAND_CAPABILITY

# capabilities-v1's and automation-v1's (automate.bank-rule, automate.invoice: no command of their own).
CAPABILITIES = {**_V1, **_automation_v1.CAPABILITIES}

HUB = frozenset(name for name, (threshold, _) in CAPABILITIES.items() if threshold == 'hub_admin')
# The POS layer (pos-v1, design/point-of-sale.md §9): an agent holds a pos.* capability only by an explicit grant, as
# it does the acts above. Unlike them, each POS command names its capability itself, so a person is admitted, or
# refused by a deny, through the policy like any capability; only the agent's half is asked here.
from .permission_pos_catalog import CAPABILITIES as _POS  # noqa: E402
POS = frozenset(_POS)


def pos_capability(cmd) -> str | None:
    """The POS capability a registered command needs (its own, else a resource requirement), or None: decided by the
    capability, never the name, so a POS report under another noun (`report pos-day`) is gated and hidden too."""
    if cmd is None:
        return None
    if cmd.capability in POS:
        return cmd.capability
    return next((capability for capability, _ in cmd.resource_requirements if capability in POS), None)


def capability_for(command: str) -> str | None:
    found = COMMAND_CAPABILITY.get(command)
    if found is None:
        from bookflow.core import registry
        found = pos_capability(registry.get(command))
    return found


def threshold(capability: str) -> str:
    return _POS[capability][0] if capability in _POS else CAPABILITIES[capability][0]


def _is_person(s) -> bool:
    return s.actor is not None and s.actor.kind == 'human'


def principal_of(s) -> str | None:
    """Whom the acting agent acts for: the admitted credential's, or a local session's context."""
    credential = getattr(s, 'credential', None)
    principal = getattr(credential, 'on_behalf_of', None)
    if principal is None:
        ctx = getattr(s, '_permission_context', None)
        principal = getattr(ctx, 'on_behalf_of', None)
    return principal


def refusal(capability: str, command: str, why: str = 'capability_not_granted', *, field: str | None = None) -> BookflowError:
    act = people_only.act_subject(command, field)
    holds = {'capability_not_granted': f'your principal has not granted you {capability}',
             'principal_lacks_capability': f'your principal does not hold {capability} themselves',
             'activation_required': f'capabilities are granted only once permissions are activated'}[why]
    message = f'{act} needs the capability {capability}, and {holds}. Ask your principal to grant it if this is yours to do.'
    details = {'capability': capability, 'reason': why, 'command': command, 'required_role': threshold(capability),
               'next_step': (f'Ask your principal (the person you act for) to grant {capability} on your membership '
                             f'(membership grant <you> --grants {capability}) if this is yours to do.')}
    if field:
        details['field'] = field
    return BookflowError('E_PERMISSION', message=message, details=details)


def _activated(s) -> bool:
    from .permission_access import activated
    return getattr(s, 'hub', None) is not None and activated(s)


def _memberships(s, user_id: str, scopes=None):
    rows = s.hub.raw.execute(
        'SELECT scope_type, scope_id, role, grants, denies FROM main.memberships WHERE user_id=? AND revoked_at IS NULL',
        (user_id,)).fetchall()
    return [r for r in rows if scopes is None or (r[0], r[1]) in scopes]


def _policy(row):
    return set(json.loads(row[3] or '[]')), set(json.loads(row[4] or '[]'))


def granted(s, user_id: str, capability: str, scopes=None) -> bool:
    """An explicit grant of the capability on a live membership (in `scopes`, if given), and no deny of it there."""
    rows = _memberships(s, user_id, scopes)
    policies = [_policy(r) for r in rows]
    return any(capability in g for g, _ in policies) and not any(capability in d for _, d in policies)


def _company_scopes(s):
    row = s.company_row
    return {('company', row['id']), ('organization', row['organization_id'])}


def require_for_command(s, cmd) -> None:
    """Admission before the company opens: the five, the installation-wide capabilities, and activation."""
    if _is_person(s) or s.actor is None:
        return
    people_only.require_person_for_command(s, cmd)
    capability = capability_for(cmd.name)
    if capability is None:
        return
    if not _activated(s):
        raise refusal(capability, cmd.name, 'activation_required')
    if capability in HUB:
        require_hub(s, capability, cmd.name)
    elif capability == 'membership.grant':
        # The scope comes with the input; hub.identity_admin checks it there. Here: is it granted at all.
        if not granted(s, s.actor.id, capability):
            raise refusal(capability, cmd.name)
        principal = principal_of(s)
        if principal is None or any(capability in _policy(r)[1] for r in _memberships(s, principal)):
            raise refusal(capability, cmd.name, 'principal_lacks_capability')


def require_hub(s, capability: str, command: str) -> None:
    """An installation-wide capability: the agent's own grant, and an active hub administrator as its principal."""
    if _is_person(s):
        if not s.is_hub_admin:
            raise BookflowError('E_PERMISSION', details={'capability': capability, 'required_role': 'hub_admin'})
        return
    if s.actor is None or not _activated(s):
        raise refusal(capability, command, 'activation_required')
    if not granted(s, s.actor.id, capability):
        raise refusal(capability, command)
    principal = principal_of(s)
    from .credentials import _binding
    eligible, _ = _binding(s.hub, s.actor.id, principal)
    row = s.hub.raw.execute('SELECT kind, active, hub_admin FROM main.users WHERE id=?', (principal,)).fetchone() \
        if principal else None
    if not eligible or row is None or tuple(row) != ('human', 1, 1) or any(capability in _policy(r)[1] for r in _memberships(s, principal)):
        raise refusal(capability, command, 'principal_lacks_capability')


def holds_hub(s, capability: str) -> bool:
    """For a command's own hub-administrator check: a hub administrator, or an agent that passes require_hub."""
    if _is_person(s):
        return s.is_hub_admin
    try:
        require_hub(s, capability, '')
    except BookflowError:
        return False
    return True


def require_company(s, command: str, capability: str | None = None, *, field: str | None = None) -> None:
    """A company capability, once the company is resolved: own explicit grant, then the actor/principal admission."""
    if _is_person(s):
        return
    capability = capability or capability_for(command)
    if capability is None or capability in HUB or capability == 'membership.grant':
        return
    if s.actor is None:
        raise people_only.refusal(people_only.ROLES_RULE, command=command, capability=capability)
    if not _activated(s):
        raise refusal(capability, command, 'activation_required', field=field)
    if not granted(s, s.actor.id, capability, _company_scopes(s)):
        raise refusal(capability, command, field=field)
    from bookflow.company.capability_gate import require_held
    try:
        require_held(s, capability, threshold(capability))
    except BookflowError as exc:
        if exc.code != 'E_PERMISSION':
            raise
        raise refusal(capability, command, 'principal_lacks_capability', field=field) from None


def require_closing_date(s) -> None:
    """`company update` changing closing_date (company.closing-date)."""
    require_company(s, 'company update', CLOSING_DATE_CAPABILITY, field='closing_date')
