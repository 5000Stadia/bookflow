"""To-dos, overdue notices and the Reminders list: the work the host's scheduler and a company's people leave each other (R111).

The scheduler itself is not a command: it runs inside `bookflow serve` (``company/scheduler.py``),
entering automatic memorized transactions, assigning to-dos and generating overdue notices without a
person pressing a button. These commands are how a person or an agent sees and answers that work.
"""
from bookflow.company import notices, reminders, todos
from bookflow.company.scheduling_models import (
    NoticeDismissInput, NoticeGenerateInput, NoticeGenerateOutput, NoticeListInput, NoticeListOutput,
    NoticeMarkSentInput, NoticeOutput, NoticeShowInput, NoticeWriteOutput, RemindersInput, RemindersOutput,
    TodoCompleteInput, TodoCreateInput, TodoListInput, TodoListOutput, TodoOutput, TodoShowInput,
    TodoUpdateInput, TodoWriteOutput,
)
from bookflow.core.registry import Plan, command

_TODO_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND']

DESCRIPTIONS = {
    'todo create': (
        'Create a to-do: something to be done by a date, assigned to a person or an agent -- the anchor\'s To Do'
        ' note. `title` says what; `notes` the details; `due_date` defaults to today; `priority` is high, medium'
        ' (the default) or low. `assignee` is a username or user id of a member of this company, or "me"; omitted,'
        ' the to-do is yours. An agent assigned a to-do finds it with `todo list --mine`. `link_type` and `link`'
        ' say what it is about: a customer, a vendor, a document (transaction id) or a memorized transaction.'),
    'todo update': (
        'Change a to-do: title, details, due date, priority, assignee, what it is about, or its status (open, or'
        ' inactive when it no longer needs doing). Fields you do not name stay as they are; null clears notes, the'
        ' assignee or the link. Mark it done with `todo complete`.'),
    'todo complete': (
        'Mark a to-do done. It records who completed it and when, leaves the open lists, and stays in'
        ' `todo list --status done`. A done to-do can be opened again with `todo update --status open`.'),
    'todo show': 'Show one to-do: what, when, for whom, about what, and who made and completed it.',
    'todo list': (
        'List to-dos, soonest due first and high priority first within a day. Open ones by default; `--mine` lists'
        ' only those assigned to whoever asks (how an agent picks up its work), `--assignee` someone else\'s,'
        ' `--due-by` those due by a date, `--status` done, inactive or all. A to-do the scheduler assigned (an'
        ' automatic memorized entry that needs a person) carries its `source_ref`.'),
    'notice generate': (
        'Generate overdue notices now, by the company\'s overdue notice preferences -- what the scheduler in'
        ' `bookflow serve` does by itself on the notice schedule. One notice per customer or job whose open invoices'
        ' and statement charges at least `overdue_notice_days` past due total at least the minimum (and whose whole'
        ' balance is above zero), for this period of the schedule (the month, or the week from Monday). A customer'
        ' that already has a notice for the period -- ready, sent or dismissed -- gets no second one. Nothing is'
        ' sent: each notice waits in `notice list` with a printable statement or letter (`print_path`).'),
    'notice list': (
        'List overdue notices: ready to send by default, or sent, dismissed or all. Each carries the customer, the'
        ' overdue balance and documents captured when it was generated, the letter text, and `print_path`, the'
        ' printable PDF (the customer statement, or the letter). Bookflow never sends a notice itself.'),
    'notice show': 'Show one overdue notice: whom it is to, what was overdue, the letter text and where to print it.',
    'notice mark-sent': (
        'Record that a ready notice has been sent -- printed and mailed, emailed by hand, or otherwise (`method`).'
        ' Bookflow did not send it; this records that a person (or a delivery step outside Bookflow) did.'),
    'notice dismiss': (
        'Set a ready notice aside without sending it, with a note saying why. It stays on the record as dismissed,'
        ' and no new notice is generated for that customer until the next notice period.'),
    'reminders': (
        'What needs doing, across the company, as of today (or `as_of`) -- the anchor\'s Reminders list. One'
        ' heading per kind with its count and total: memorized transactions due (reminder slots and missed ones'
        ' left for a person), memorized entries that need a person (blocked: a closed period, an inactive account,'
        ' a permission the owner lost), to-dos due (`--mine` for yours), overdue notices ready to send, overdue'
        ' invoices, and bills to pay within `bills_days_ahead` days. Each heading lists up to `limit` lines. An'
        ' agent asking "what needs doing today?" starts here, then `todo list --mine`. A heading is shown only to a'
        ' reader its own list command would answer -- to-dos need `note`, notices `customer`, overdue invoices and'
        ' bills `reports` -- and a heading left out takes its count and total with it.'),
}


def _read(name, model, output_model, reader, positional, capability, errors=None):
    def planner(inp, ctx, s):
        return Plan(reader(s, inp))
    return command(name, scope='company', description=DESCRIPTIONS[name], input_model=model,
                   output_model=output_model, required_role='member', capability=capability,
                   positional=positional, error_codes=errors or ['E_VALIDATION', 'E_RECORD_NOT_FOUND'])(planner)


def _todo_write(verb, model, positional):
    name = 'todo ' + verb

    def planner(inp, ctx, s):
        return todos.prepare(s, ctx, inp, verb)
    cmd = command(name, scope='company', description=DESCRIPTIONS[name], input_model=model,
                  output_model=TodoWriteOutput, writes={'company'}, required_role='standard', capability='note',
                  accepts_idempotency_key=True, positional=positional, clearable=verb == 'update',
                  version_source=None if verb == 'create' else ('todo show', 'todo', 'version'),
                  error_codes=_TODO_ERRORS + ([] if verb == 'create' else ['E_VERSION_CONFLICT']))(planner)
    cmd.applier(todos.apply)
    return cmd


def _notice_write(verb, model):
    name = 'notice ' + verb

    def planner(inp, ctx, s):
        return notices.prepare_write(s, ctx, inp, verb)
    cmd = command(name, scope='company', description=DESCRIPTIONS[name], input_model=model,
                  output_model=NoticeWriteOutput, writes={'company'}, required_role='standard', capability='customer',
                  accepts_idempotency_key=True, positional=['notice'], version_source=('notice show', 'notice', 'version'),
                  error_codes=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'])(planner)
    cmd.applier(notices.apply_write)
    return cmd


def plan_notice_generate(inp, ctx, s):
    return notices.prepare_generate(s, ctx, inp)


todo_create = _todo_write('create', TodoCreateInput, [])
todo_update = _todo_write('update', TodoUpdateInput, ['todo'])
todo_complete = _todo_write('complete', TodoCompleteInput, ['todo'])
todo_show = _read('todo show', TodoShowInput, TodoOutput, todos.show, ['todo'], 'note')
todo_list = _read('todo list', TodoListInput, TodoListOutput, todos.page, [], 'note')

notice_generate = command(
    'notice generate', scope='company', description=DESCRIPTIONS['notice generate'], input_model=NoticeGenerateInput,
    output_model=NoticeGenerateOutput, writes={'company'}, required_role='standard', capability='customer',
    accepts_idempotency_key=True, error_codes=['E_VALIDATION'])(plan_notice_generate)
notice_generate.applier(notices.apply_generate)
notice_list = _read('notice list', NoticeListInput, NoticeListOutput, notices.page, [], 'customer')
notice_show = _read('notice show', NoticeShowInput, NoticeOutput, notices.show, ['notice'], 'customer')
notice_mark_sent = _notice_write('mark-sent', NoticeMarkSentInput)
notice_dismiss = _notice_write('dismiss', NoticeDismissInput)

reminders_list = _read('reminders', RemindersInput, RemindersOutput, reminders.reminders, [], 'ledger.read',
                       errors=['E_VALIDATION'])

SCHEDULING_COMMANDS = [todo_create, todo_update, todo_complete, todo_show, todo_list,
                       notice_generate, notice_list, notice_show, notice_mark_sent, notice_dismiss, reminders_list]
