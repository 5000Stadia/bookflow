"""Memorize a transaction, see what is due, and enter it.

Two nouns. ``memorized`` is one template -- a create-or-post command, the input it replays,
and a schedule. ``memorized-group`` is an ordered collection of templates that can share one
schedule and be entered together.

The accounting lives in ``company/memorized.py`` and ``company/memorized_entry.py``.
"""
from bookflow.company import memorized, memorized_entry
from bookflow.company.memorized_models import (
    MemorizedCreateInput, MemorizedDeleteInput, MemorizedEnterInput, MemorizedEntryOutput,
    MemorizedGroupCreateInput, MemorizedGroupDeleteInput, MemorizedGroupEnterInput,
    MemorizedGroupListInput, MemorizedGroupListOutput, MemorizedGroupOutput,
    MemorizedGroupShowInput, MemorizedGroupUpdateInput, MemorizedGroupWriteOutput,
    MemorizedListInput, MemorizedListOutput, MemorizedOccurrenceActionInput, MemorizedOutput,
    MemorizedProcessInput, MemorizedShowInput, MemorizedUpdateInput, MemorizedWriteOutput,
    OccurrenceWriteOutput,
)
from bookflow.core.registry import Plan, command

_CREATE = (
    'Memorize a transaction: keep one create-or-post command and the exact input it takes, so'
    ' the same entry can be made again without retyping it. `command` is the registered command'
    ' to replay -- `invoice post`, `bill post`, `journal post` and the other create/post'
    ' commands; a command that is not one of those is refused and the error lists what is.'
    ' `payload` is that command\'s own input, exactly as you would send it, and it is validated'
    ' against that command now rather than on the day it is first due. Whatever the payload'
    ' does not set stays unset and is recomputed at every entry, so terms, due dates, prices,'
    ' tax and the document number follow today\'s records; the accounting date is always the'
    ' occurrence\'s own slot date and is never taken from the payload. A document number, an'
    ' `expected_version` or an `operation_key` in the payload is dropped with a warning,'
    ' because each of those identifies one entry that was already made. Names a payload gives'
    ' -- a customer, an account, an item -- are resolved once and stored as stable ids, so'
    ' renaming the customer later does not break the template. `frequency` and `start_date`'
    ' give it a schedule: leave `frequency` at `never` for a template you only ever enter by'
    ' hand. `mode` decides what a due slot does -- `on_demand` nothing, `remind` leaves it'
    ' waiting where you can see it, `enter_automatically` lets `memorized process` enter it.'
    ' `days_in_advance` enters it early while keeping the slot date as the accounting date.'
    ' `remaining_count` is how many more slots the schedule will create.'
)

_UPDATE = (
    'Change a memorized transaction: its name, its schedule, its mode, its group, or the input'
    ' it replays. Every change writes a new revision and leaves the old one readable, and an'
    ' occurrence that has already been entered keeps the revision it captured -- editing a'
    ' template never rewrites an entry that was already made, and never mints a second'
    ' occurrence for a slot that already has one. Fields you do not name are left alone;'
    ' `--clear` empties a nullable one. Changing `frequency` or `start_date` re-anchors the'
    ' schedule; changing neither leaves the next slot where it is. `status` set to `paused`'
    ' stops new slots being created without touching what is already pending or blocked.'
)

_PROCESS = (
    'Enter every memorized transaction that is due, oldest slot first. A slot is due when its'
    ' date, less the template\'s days in advance, has arrived in the company\'s own timezone --'
    ' so a host in one zone never enters a company\'s transaction a day early. A backlog is'
    ' entered as a backlog: each missed slot is entered at its own accounting date, not'
    ' collapsed onto today. Only templates whose mode is `enter_automatically` are entered;'
    ' one whose mode is `remind` gets a pending occurrence you can see and enter yourself.'
    ' Running this twice, or on a host that restarted twice in the same day, cannot enter the'
    ' same slot twice: an occurrence is identified by its template and its nominal slot, and'
    ' its entry carries a retry key that the command it replays already honours. An entry that'
    ' fails becomes a blocked occurrence carrying its reason, at its own date, which `memorized'
    ' retry` runs again and `memorized skip` sets aside; one failure never stops the template'
    ' or any other template. Document numbers are allocated only by an entry that succeeds.'
)

_ENTER = (
    'Enter one memorized transaction now, as an extra entry outside its schedule. `date` is the'
    ' accounting date and defaults to today in the company\'s timezone. This is a new entry with'
    ' its own identity every time it is run, which is what makes it useful for a one-off; use'
    ' `memorized process` for the scheduled ones, which are entered once per slot however often'
    ' it runs. A failure is recorded as a blocked occurrence rather than raised, so the reason'
    ' is visible on the template afterwards.'
)

_LIST = (
    'List memorized transactions with what each one owes: how many slots are due now, how many'
    ' entries are blocked, and when it last entered. `due_only` narrows it to the ones that need'
    ' attention. `as_of` answers the question for a date other than today, which is how you see'
    ' what next week will bring. Totals across the whole filter are returned beside the page.'
)

_SHOW = (
    'Show one memorized transaction: the command it replays, the input it replays with, which'
    ' fields are fixed by that input and which are recomputed at every entry, its schedule, the'
    ' next slots that will come due, and its recent occurrences with their results and reasons.'
    ' `references` is the derived dependency index -- every stable id the payload names, with'
    ' what that record looks like now. It is an index, not a guarantee: it shows a deactivated'
    ' account or a deleted class before the next slot arrives, and the command itself is still'
    ' what decides whether an entry may be made.'
)

_GROUP_CREATE = (
    'Create a memorized transaction group: an ordered collection of templates that can carry one'
    ' shared schedule. Give it a `frequency` and a `start_date` and the group drives its members,'
    ' each of which must then carry no schedule of its own; leave `frequency` at `never` and the'
    ' group is only an ordered set you can enter together on demand. Add templates to it with'
    ' `memorized create --group` or `memorized update --group`, each with its `group_ordinal`.'
)

_GROUP_ENTER = (
    'Enter every member of a group, in the order the group records, at one date. Each member'
    ' gets its own entry and its own result: this is not an all-or-nothing batch, so a member'
    ' that fails does not undo the members that already posted, and running it again for the'
    ' same date enters only the ones that did not. The membership and order captured for a date'
    ' are frozen when the run opens, so editing the group afterwards cannot rewrite what that'
    ' run did.'
)

DESCRIPTIONS = {
    'memorized create': _CREATE,
    'memorized update': _UPDATE,
    'memorized delete': (
        'Take a memorized transaction off the list. The template stops, no further slots are'
        ' created, and it leaves every default listing. The entries it already made are posted'
        ' history and stay exactly where they are, with their link back to this template intact,'
        ' because this system never erases what was entered.'),
    'memorized show': _SHOW,
    'memorized list': _LIST,
    'memorized enter': _ENTER,
    'memorized process': _PROCESS,
    'memorized retry': (
        'Try a blocked memorized entry again, at the date it was always for. The retry reuses'
        ' the occurrence\'s own retry key and the exact template revision it captured, so it can'
        ' neither post a second document nor quietly enter a newer version of the template than'
        ' the one that was due. A retry that fails again updates the reason and stays blocked.'),
    'memorized skip': (
        'Set a memorized entry aside without entering it. The occurrence stays on the record as'
        ' skipped, with its date and its reason, so a skipped month is visible rather than'
        ' missing. Skipping does not create another slot and does not change the schedule.'),
    'memorized-group create': _GROUP_CREATE,
    'memorized-group update': (
        'Change a group: its name, its shared schedule, its mode or its status. Giving a group a'
        ' schedule requires that none of its members already carries one. A change here never'
        ' rewrites a run that has already opened: each run froze its membership and order when'
        ' it started.'),
    'memorized-group delete': (
        'Take an empty memorized group off the list. A group that still has members is refused,'
        ' naming them, so no template is left pointing at a group that is gone; move or delete'
        ' its members first.'),
    'memorized-group show': (
        'Show one group: its shared schedule, its members in their order with what each owes,'
        ' the next slots due, and its recent runs with every member\'s own result.'),
    'memorized-group list': 'List memorized transaction groups with their schedule and how many members each has.',
    'memorized-group enter': _GROUP_ENTER,
}

ERRORS = {
    'memorized create': ['E_VALIDATION', 'E_NAME_TAKEN', 'E_RECORD_NOT_FOUND'],
    'memorized update': ['E_VALIDATION', 'E_NAME_TAKEN', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'],
    'memorized delete': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION'],
    'memorized enter': ['E_RECORD_NOT_FOUND', 'E_VALIDATION'],
    'memorized process': ['E_RECORD_NOT_FOUND', 'E_VALIDATION'],
    'memorized retry': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION'],
    'memorized skip': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION'],
    'memorized show': ['E_RECORD_NOT_FOUND', 'E_VALIDATION'],
    'memorized list': ['E_VALIDATION'],
    'memorized-group create': ['E_VALIDATION', 'E_NAME_TAKEN'],
    'memorized-group update': ['E_VALIDATION', 'E_NAME_TAKEN', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'],
    'memorized-group delete': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_ACTIVE_DEPENDENTS', 'E_VALIDATION'],
    'memorized-group enter': ['E_RECORD_NOT_FOUND', 'E_VALIDATION'],
    'memorized-group show': ['E_RECORD_NOT_FOUND', 'E_VALIDATION'],
    'memorized-group list': ['E_VALIDATION'],
}


def _write(name, verb, model, output_model, prepare, apply_fn, positional, version_source=None):
    def planner(inp, ctx, s):
        plan = prepare(s, ctx, inp, verb)
        plan.data['command_name'] = name
        return plan

    cmd = command(
        name, scope='company', description=DESCRIPTIONS[name], input_model=model,
        output_model=output_model, writes={'company'}, required_role='standard',
        capability='ledger.post', accepts_idempotency_key=True, clearable=verb == 'update',
        positional=positional, version_source=version_source, error_codes=ERRORS[name])(planner)
    cmd.applier(apply_fn)
    return cmd


def _entry(name, verb, model, output_model, positional):
    """The verbs that dispatch other commands own their own transactions and retry record."""
    def planner(inp, ctx, s):
        from bookflow.core import idempotency
        plan = memorized_entry.prepare_entry(s, ctx, inp, verb)
        plan.data['command_name'] = name
        plan.data['input_hash'] = idempotency.input_hash(inp.model_dump(mode='json'), s.company_id)
        return plan

    cmd = command(
        name, scope='company', description=DESCRIPTIONS[name], input_model=model,
        output_model=output_model, writes={'company'}, required_role='standard',
        capability='ledger.post', accepts_idempotency_key=True, positional=positional,
        error_codes=ERRORS[name])(planner)
    cmd.applier(memorized_entry.apply_entry)
    return cmd


def _read(name, model, output_model, reader, positional):
    def planner(inp, ctx, s):
        return Plan(reader(s, inp))

    return command(
        name, scope='company', description=DESCRIPTIONS[name], input_model=model,
        output_model=output_model, required_role='member', capability='ledger.read',
        positional=positional, error_codes=ERRORS[name])(planner)


memorized_create = _write('memorized create', 'create', MemorizedCreateInput, MemorizedWriteOutput,
                          memorized.prepare, memorized.apply_write, [])
memorized_update = _write('memorized update', 'update', MemorizedUpdateInput, MemorizedWriteOutput,
                          memorized.prepare, memorized.apply_write, ['memorized'],
                          ('memorized show', 'memorized', 'version'))
memorized_delete = _write('memorized delete', 'delete', MemorizedDeleteInput, MemorizedWriteOutput,
                          memorized.prepare, memorized.apply_write, ['memorized'],
                          ('memorized show', 'memorized', 'version'))
memorized_enter = _entry('memorized enter', 'enter', MemorizedEnterInput, MemorizedEntryOutput, ['memorized'])
memorized_process = _entry('memorized process', 'process', MemorizedProcessInput, MemorizedEntryOutput, [])
memorized_retry = _entry('memorized retry', 'retry', MemorizedOccurrenceActionInput, OccurrenceWriteOutput, ['occurrence'])
memorized_skip = _entry('memorized skip', 'skip', MemorizedOccurrenceActionInput, OccurrenceWriteOutput, ['occurrence'])
memorized_show = _read('memorized show', MemorizedShowInput, MemorizedOutput, memorized.show, ['memorized'])
memorized_list = _read('memorized list', MemorizedListInput, MemorizedListOutput, memorized.page, [])

memorized_group_create = _write('memorized-group create', 'create', MemorizedGroupCreateInput,
                                MemorizedGroupWriteOutput, memorized.prepare_group,
                                memorized.apply_group_write, [])
memorized_group_update = _write('memorized-group update', 'update', MemorizedGroupUpdateInput,
                                MemorizedGroupWriteOutput, memorized.prepare_group,
                                memorized.apply_group_write, ['memorized_group'],
                                ('memorized-group show', 'memorized_group', 'version'))
memorized_group_delete = _write('memorized-group delete', 'delete', MemorizedGroupDeleteInput,
                                MemorizedGroupWriteOutput, memorized.prepare_group,
                                memorized.apply_group_write, ['memorized_group'],
                                ('memorized-group show', 'memorized_group', 'version'))
memorized_group_enter = _entry('memorized-group enter', 'group-enter', MemorizedGroupEnterInput,
                               MemorizedEntryOutput, ['memorized_group'])
memorized_group_show = _read('memorized-group show', MemorizedGroupShowInput, MemorizedGroupOutput,
                             memorized.group_show, ['memorized_group'])
memorized_group_list = _read('memorized-group list', MemorizedGroupListInput, MemorizedGroupListOutput,
                             memorized.group_page, [])

MEMORIZED_COMMANDS = [
    memorized_create, memorized_update, memorized_delete, memorized_show, memorized_list,
    memorized_enter, memorized_process, memorized_retry, memorized_skip,
    memorized_group_create, memorized_group_update, memorized_group_delete,
    memorized_group_show, memorized_group_list, memorized_group_enter,
]
