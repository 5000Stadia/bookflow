"""Confirm-first marks (R187 addendum, design/principal-directives.md: impact, not quantity).

A command whose effect could damage carries a ``confirm_first`` mark, which `--help`, the generated reference and the
MCP command list and help show. It is information for an agent, never a prompt and never enforced: no command asks
"are you sure", and permissions and reversible batches stay the hard guarantees. The agent guide says how to use it:
a named act just runs; a damaging act the principal did not plainly name gets one plain confirmation; a broad change
that sets how the business works from then on gets one check of the dry run's exact details even when named.

The mark is decided here, in one table, so a reviewer reads every choice in one place. Every void and delete is
marked by its verb (a command added later under those verbs is marked too); the rest are named.
"""
from __future__ import annotations

VERBS = {'void': 'void', 'delete': 'delete', 'transfer-void': 'void'}

DELETE = 'delete'
REVERSAL = 'batch reversal'
SEND = 'send'
PERMISSION = 'permission change'
AUTOMATION = 'automation'
BROAD = 'broad lasting change'
MERGE = 'merge'

NAMED: dict[str, str] = {
    # Removals beyond the delete verb.
    'company detach': DELETE, 'demo reset': DELETE, 'delivery-connection remove': DELETE, 'pos-box remove': DELETE,
    'budget clear': DELETE,
    # Reversals of batches: one command undoes many records.
    'batch reverse': REVERSAL, 'bank-feed reverse': REVERSAL, 'bank-line undo': REVERSAL,
    'bulk-edit reverse': REVERSAL, 'import reverse': REVERSAL, 'import cancel': REVERSAL,
    'opening-draft reverse': REVERSAL, 'delivery batch-cancel': REVERSAL,
    # Sends and mark-sent: mail cannot be recalled, and a hand-sent mark ends the message's sending.
    'delivery send': SEND, 'delivery approve': SEND, 'delivery retry': SEND, 'delivery run': SEND,
    'delivery batch': SEND, 'delivery mark-sent': SEND, 'notice mark-sent': SEND, 'delivery-schedule run': SEND,
    'delivery-connection enable': SEND,
    # Permission and capability changes: who may do what.
    'membership grant': PERMISSION, 'membership revoke': PERMISSION, 'permission activate': PERMISSION,
    'agent assign': PERMISSION, 'agent unassign': PERMISSION, 'agent authorize': PERMISSION,
    'agent activate': PERMISSION, 'agent deactivate': PERMISSION, 'user add': PERMISSION,
    'user activate': PERMISSION, 'user deactivate': PERMISSION, 'user set-password': PERMISSION,
    'token issue': PERMISSION, 'token revoke': PERMISSION, 'accountant add': PERMISSION,
    'accountant remove': PERMISSION, 'accountant-copy cancel': PERMISSION,
    # Making an automation live: it then writes or sends with nobody asking at the time.
    'delivery-schedule live': AUTOMATION, 'delivery-schedule update': AUTOMATION, 'bank-rule update': AUTOMATION,
    # Broad edits and changes that set how the business works from then on.
    'bulk-edit apply': BROAD, 'import run': BROAD, 'cutover apply': BROAD, 'opening-draft apply': BROAD,
    'accountant-copy import': BROAD, 'costing-method set': BROAD, 'costing-method withdraw': BROAD,
    'backup schedule': BROAD,
    # Merges: one record read as another everywhere.
    'customer merge': MERGE, 'customer unmerge': MERGE, 'vendor merge': MERGE, 'vendor unmerge': MERGE,
    'account merge': MERGE, 'account unmerge': MERGE, 'item merge': MERGE, 'item unmerge': MERGE,
}

WHY = {
    DELETE: 'it deletes or removes records',
    'void': 'it voids a posted document',
    REVERSAL: 'it undoes a whole batch at once',
    SEND: 'what it sends cannot be recalled',
    PERMISSION: 'it changes who may do what',
    AUTOMATION: 'it can make a rule live that then acts unattended',
    BROAD: 'it changes many records, or how the business works from then on',
    MERGE: 'it reads one record as another everywhere',
}


def category(name: str) -> str | None:
    """The kind of damage a command could do (its confirm-first mark), or None for an unmarked command."""
    found = NAMED.get(name)
    if found is not None:
        return found
    verb = name.split(' ')[-1] if ' ' in name else ''
    return VERBS.get(verb)


def note(name: str) -> str | None:
    """The one line `--help`, the reference and MCP help show for a marked command."""
    kind = category(name)
    if kind is None:
        return None
    return (f'Confirm first ({kind}): {WHY[kind]}. Run it at once when your principal plainly named this act; '
            'otherwise say in plain words what it will do, show the dry run, and run it on their yes. Bookflow '
            'never asks; this mark is guidance.')
