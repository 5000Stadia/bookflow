"""What stays a person's own act: five commands no capability could make safe (design/principal-directives.md §2 b).

The list is the catalog's: `admin:people-only:<command>` admin actions still `human_only` in the tip (capabilities-v1
leaves exactly five: user set-password, token issue, agent assign, agent authorize, permission activate). An agent
-- or any actor that is not a person -- is refused with E_PERMISSION whatever its role or grants, `reason:
people_only` and `principal_only: true`, and told to ask its principal. Every other former people-only act is a
capability now (hub.capabilities); `act_subject` names the act in both refusals.
"""
from __future__ import annotations

from functools import lru_cache

from bookflow.core.errors import BookflowError

PEOPLE_ONLY_PREFIX = 'admin:people-only:'
CLOSING_DATE_ACTION = 'admin:company:closing-date'
ROLES_RULE = 'user roles and permissions are set by a person'
CLOSING_DATE_RULE = 'the closing date is set by a person'
# Commands outside the rules above that are still a person's act, with the sentence that names the act:
# restoring a deleted document (restore-v1), merging parties (party-merge-v1), converting an item
# to an inventory part (item-convert-v1) and merging accounts and items (list-merge-v1).
ACT_SUBJECTS = {
    'journal restore': 'Restoring a deleted document',
    'invoice restore': 'Restoring a deleted document',
    'customer merge': 'Merging customers', 'customer unmerge': 'Undoing a customer merge',
    'vendor merge': 'Merging vendors', 'vendor unmerge': 'Undoing a vendor merge',
    'item convert': 'Converting an item to an inventory part',
    'account merge': 'Merging accounts', 'account unmerge': 'Undoing an account merge',
    # check-printing-v1: what happened to paper in a printer is known to the person at it.
    'check print-confirm': 'Confirming which checks printed', 'check print-cancel': 'Cancelling a check print run',
    'check reprint': 'Reprinting a check',
    'item merge': 'Merging items', 'item unmerge': 'Undoing an item merge',
    'clock link': "Linking a person's login to an employee",
    'accountant add': 'Making someone the external accountant', 'accountant remove': 'Ending an external accountant designation',
    'accountant-copy cancel': "Cancelling an accountant's copy", 'accountant-copy import': "Importing an accountant's changes",
    # fifo-v1: how stock is costed is the company's policy, changed on the accountant's advice.
    'costing-method set': 'Changing the costing method', 'costing-method withdraw': 'Withdrawing a costing method switch',
    # delivery-v1: what happened in the owner's own mail program is known to the owner.
    'delivery mark-sent': 'Recording that an email was sent by hand',
    # delivery-queue-v1: a go, and where mail goes under whose keys (R187: delivery.send, delivery.connect).
    'delivery approve': 'Giving an email its go', 'delivery-connection add': 'Adding a sending connection',
    'delivery-connection update': 'Changing a sending connection',
    'delivery-connection enable': 'Enabling a sending connection', 'delivery-connection disable':
    'Disabling a sending connection', 'delivery-connection remove': 'Removing a sending connection',
    'delivery-connection check': 'Checking a sending connection',
    # delivery-schedule-v1: a send schedule made live sends unattended under its establisher (R187: automate.delivery).
    'delivery-schedule live': 'Making a send schedule live',
    # capabilities-v1: the installation-wide acts, and the five that stay a person's.
    'backup schedule': 'Scheduling backups', 'demo reset': 'Resetting the demo organization',
    'user add': 'Adding a user', 'user activate': 'Reactivating a user', 'user deactivate': 'Deactivating a user',
    'membership grant': "Changing someone's access", 'membership revoke': "Revoking someone's access",
    'agent create': 'Creating an agent', 'agent activate': 'Reactivating an agent',
    'agent deactivate': 'Deactivating an agent', 'agent unassign': 'Taking an agent off a principal',
    'user set-password': 'Setting a password', 'token issue': 'Issuing a token',
    'agent assign': 'Assigning an agent to the person it acts for', 'agent authorize': 'Authorizing an agent',
    'permission activate': 'Activating permissions',
}


def act_subject(command: str, field: str | None = None) -> str:
    if field == 'closing_date':
        return 'Changing the closing date'
    return ACT_SUBJECTS.get(command) or f'`{command}`'


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
    """One of the five: no grant opens it. `people_only` stays the reason so existing clients keep working."""
    act = act_subject(command, field)
    details = {'capability': capability, 'reason': 'people_only', 'principal_only': True, 'rule': rule or
               f"{act[0].lower()}{act[1:]} is your principal's own act", 'command': command, 'required_role': 'human',
               'next_step': 'Ask your principal (the person you act for) to do this themselves; no grant can give it to you.'}
    if field:
        details['field'] = field
    return BookflowError('E_PERMISSION', message=(
        f"{act} is your principal's own act, not an agent's, and no capability grants it. Ask your principal to do it."),
        details=details)


def require_person_for_command(s, cmd) -> None:
    if not _is_person(s) and s.actor is not None and cmd.name in people_only_commands():
        raise refusal(ROLES_RULE, command=cmd.name, capability=cmd.capability)


def require_person_for_closing_date(s) -> None:
    if not _is_person(s) and _tip()[1]:
        raise refusal(CLOSING_DATE_RULE, command='company update', capability='company', field='closing_date')
