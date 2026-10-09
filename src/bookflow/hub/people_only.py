"""What only a person may do: the closing date, and every change to who can do what.

The list is the catalog's (`permission_human_admin_catalog`): human-only admin actions under
`admin:people-only:<command>` and `admin:company:closing-date`, read from the tip, which is what
every command is admitted against. An agent -- or any actor that is not a person -- is refused
with E_PERMISSION whatever its role, and told to ask its principal.
"""
from __future__ import annotations

from functools import lru_cache

from bookflow.core.errors import BookflowError

PEOPLE_ONLY_PREFIX = 'admin:people-only:'
CLOSING_DATE_ACTION = 'admin:company:closing-date'
ROLES_RULE = 'user roles and permissions are set by a person'
CLOSING_DATE_RULE = 'the closing date is set by a person'
# Commands outside the rules above that are still a person's act, with the sentence that names the act:
# restoring a deleted document (restore-v1) and merging parties (party-merge-v1).
ACT_SUBJECTS = {
    'journal restore': 'Restoring a deleted document',
    'invoice restore': 'Restoring a deleted document',
    'customer merge': 'Merging customers', 'customer unmerge': 'Undoing a customer merge',
    'vendor merge': 'Merging vendors', 'vendor unmerge': 'Undoing a vendor merge',
}


@lru_cache(maxsize=4)
def _people_only(version: str) -> tuple[frozenset[str], bool]:
    from .permission_runtime import known_catalog
    actions = known_catalog(version).CATALOG.admin_actions
    commands = frozenset(a.key[len(PEOPLE_ONLY_PREFIX):] for a in actions
                         if a.key.startswith(PEOPLE_ONLY_PREFIX) and a.human_only and a.available)
    closing = any(a.key == CLOSING_DATE_ACTION and a.human_only and a.available for a in actions)
    return commands, closing


def _tip():
    from .permission_runtime import current_catalog
    return _people_only(current_catalog().CATALOG.version)


def people_only_commands() -> frozenset[str]:
    """Every command an agent is refused, from the catalog in force."""
    return _tip()[0]


def _is_person(s) -> bool:
    return s.actor is not None and s.actor.kind == 'human'


def refusal(rule: str, *, command: str, capability: str, field: str | None = None) -> BookflowError:
    act = ACT_SUBJECTS.get(command)
    if act and not field:
        rule = f"{act[0].lower()}{act[1:]} is a person's act"
        return BookflowError('E_PERMISSION', message=(
            f"{act} is a person's act, not an agent's. Ask your principal to make this change."), details={
            'capability': capability, 'reason': 'people_only', 'rule': rule, 'command': command, 'required_role': 'human',
            'next_step': 'Ask your principal (the person you act for) to make this change themselves.'})
    subject = 'The closing date' if field == 'closing_date' else 'Who can do what in Bookflow'
    details = {'capability': capability, 'reason': 'people_only', 'rule': rule, 'command': command, 'required_role': 'human',
               'next_step': 'Ask your principal (the person you act for) to make this change themselves.'}
    if field:
        details['field'] = field
    return BookflowError('E_PERMISSION', message=(
        f'{subject} is set by a person, not an agent. Ask your principal to make this change.'), details=details)


def require_person_for_command(s, cmd) -> None:
    if not _is_person(s) and s.actor is not None and cmd.name in people_only_commands():
        raise refusal(ROLES_RULE, command=cmd.name, capability=cmd.capability)


def require_person_for_closing_date(s) -> None:
    if not _is_person(s) and _tip()[1]:
        raise refusal(CLOSING_DATE_RULE, command='company update', capability='company', field='closing_date')
