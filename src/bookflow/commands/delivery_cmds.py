"""Document delivery (R112 increment 1): email templates, drafting a message with its PDF frozen, the outbox, the
`.eml` export and marking a hand-sent message sent. Nothing here sends: no sending connection exists in this
build, so every message waits in the outbox. The work lives in ``company/delivery.py`` and
``company/delivery_templates.py``; the contract is design/document-delivery.md.
"""
from bookflow.company import delivery as d
from bookflow.company import delivery_models as m
from bookflow.company import delivery_templates as t
from bookflow.core.registry import Plan, command

_VERSION = ('delivery show', 'message', 'version')
_RESOURCES = (('ledger.read', 'member'), ('customer-work', 'member'))
_TAIL = ['E_IDEMPOTENCY_MISMATCH', 'E_DIRECTIVE_NOT_FOUND', 'E_DIRECTIVE_INACTIVE']

_DRAFT = (
    'Draft one email to a customer with the document attached as a PDF, frozen now (file name, size and SHA-256 '
    "recorded): an invoice, sales-receipt or estimate (`document`), a statement (`customer`, `date_from`, `date_to`) "
    "or a ready overdue notice (`document`). To and Cc come from the customer, Bcc from the company's copy-to; subject "
    "and body from the kind's default template unless given. A second draft of the same document returns the first "
    'while it is in the outbox; one already sent needs `again`. Nothing is sent. '
    'Example: {"kind": "invoice", "document": "1007"}.')
_UPDATE = ('Edit a draft or held message: To, Cc, Bcc, subject or body. The attachments stay as drafted unless '
           '`refresh`, which attaches the document as it is now and releases a changed-document hold.')
_SEND = (
    "Release drafts to the outbox (ready). A person's release is also their go; an agent's waits for one. No sending "
    'connection exists yet, so a ready message waits there; export it to send it yourself. A message whose document '
    'changed or was voided is held instead.')
_CANCEL = 'Set an unsent message aside, with --reason. Final; draft again for a new one.'
_MARK = (
    "Record that a person sent the message from their own mail program (shown 'Sent by hand', never delivered). "
    'For an overdue notice it also marks the notice sent. A person\'s act.')
_EXPORT = (
    'Export messages as RFC 5322 .eml files (X-Unsent: 1) with their frozen PDFs, to open and send in a mail program: '
    'one message gives a .eml, several a .zip. Sends nothing; a stale message is held instead of exported.')
_SHOW = 'Show a message: recipients, subject, body, attached files, state, and whether a document changed since.'
_QUERY = 'The outbox, newest first: by status tab, kind, customer, source document or date drafted.'
_HISTORY = 'Every event of a message, in order: who drafted, edited, released, exported, marked sent or cancelled.'


def plan_delivery_draft(inp, ctx, s) -> Plan:
    return d.prepare_draft(s, ctx, inp)


def plan_delivery_update(inp, ctx, s) -> Plan:
    return d.prepare_update(s, ctx, inp)


def plan_delivery_send(inp, ctx, s) -> Plan:
    return d.prepare_send(s, ctx, inp)


def plan_delivery_cancel(inp, ctx, s) -> Plan:
    return d.prepare_cancel(s, ctx, inp)


def plan_delivery_mark_sent(inp, ctx, s) -> Plan:
    return d.prepare_mark_sent(s, ctx, inp)


def plan_delivery_export(inp, ctx, s) -> Plan:
    return d.prepare_export(s, ctx, inp)


def plan_delivery_show(inp, ctx, s) -> Plan:
    return Plan(d.show(s, inp, ctx))


def plan_delivery_query(inp, ctx, s) -> Plan:
    return Plan(d.query(s, inp))


def plan_delivery_history(inp, ctx, s) -> Plan:
    return Plan(d.history(s, inp))


def _write(name, description, inp, out, planner, applier, *, errors, positional=(), version=None, role='standard'):
    cmd = command(name, scope='company', description=description, input_model=inp, output_model=out,
                  writes={'company'}, required_role=role, capability='customer', accepts_idempotency_key=True,
                  positional=list(positional), version_source=version, error_codes=[*errors, *_TAIL])(planner)
    cmd.applier(applier)
    cmd.resource_requirements = _RESOURCES
    return cmd


def _read(name, description, inp, out, planner, *, errors, positional=(), resources=_RESOURCES):
    cmd = command(name, scope='company', description=description, input_model=inp, output_model=out,
                  required_role='member', capability='customer', positional=list(positional),
                  error_codes=errors)(planner)
    cmd.resource_requirements = resources
    return cmd


delivery_draft = _write('delivery draft', _DRAFT, m.DraftInput, m.MessageOutput, plan_delivery_draft, d.apply_draft,
                        positional=('kind', 'document'),
                        errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_WORK_DEPENDENCY'])
delivery_update = _write('delivery update', _UPDATE, m.UpdateInput, m.MessageOutput, plan_delivery_update,
                         d.apply_single, positional=('message',), version=_VERSION,
                         errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_WORK_DEPENDENCY', 'E_VERSION_CONFLICT'])
delivery_send = _write('delivery send', _SEND, m.SendInput, m.MessagesOutput, plan_delivery_send, d.apply_send,
                       errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_WORK_DEPENDENCY', 'E_VERSION_CONFLICT'])
delivery_cancel = _write('delivery cancel', _CANCEL, m.CancelInput, m.MessageOutput, plan_delivery_cancel,
                         d.apply_single, positional=('message',), version=_VERSION,
                         errors=['E_RECORD_NOT_FOUND', 'E_WORK_DEPENDENCY', 'E_VERSION_CONFLICT', 'E_REASON_REQUIRED'])
delivery_mark_sent = _write('delivery mark-sent', _MARK, m.MarkSentInput, m.MessageOutput, plan_delivery_mark_sent,
                            d.apply_single, positional=('message',), version=_VERSION,
                            errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_WORK_DEPENDENCY', 'E_VERSION_CONFLICT',
                                    'E_PERMISSION'])
delivery_export = _write('delivery export', _EXPORT, m.ExportInput, m.ExportOutput, plan_delivery_export,
                         d.apply_export,
                         errors=['E_RECORD_NOT_FOUND', 'E_WORK_DEPENDENCY', 'E_VERSION_CONFLICT'])
delivery_show = _read('delivery show', _SHOW, m.MessageShowInput, m.MessageOutput, plan_delivery_show,
                      errors=['E_RECORD_NOT_FOUND'], positional=('message',))
delivery_query = _read('delivery query', _QUERY, m.QueryInput, m.MessagePageOutput, plan_delivery_query,
                       errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])
delivery_history = _read('delivery history', _HISTORY, m.MessageSelector, m.HistoryOutput, plan_delivery_history,
                         errors=['E_RECORD_NOT_FOUND'], positional=('message',))


# ---------------------------------------------------------------- templates

def plan_template_create(inp, ctx, s) -> Plan:
    return t.prepare_create(s, ctx, inp)


def plan_template_update(inp, ctx, s) -> Plan:
    return t.prepare_update(s, ctx, inp)


def plan_template_deactivate(inp, ctx, s) -> Plan:
    return t.prepare_deactivate(s, ctx, inp)


def plan_template_show(inp, ctx, s) -> Plan:
    return Plan(t.show(s, inp))


def plan_template_query(inp, ctx, s) -> Plan:
    return Plan(t.query(s, inp))


def plan_template_fields(inp, ctx, s) -> Plan:
    return Plan(t.fields(inp))


def plan_template_preview(inp, ctx, s) -> Plan:
    return Plan(t.preview(s, ctx, inp))


_TEMPLATE_VERSION = ('delivery-template show', 'template', 'version')
_FIELDS_HELP = ('Merge fields are a closed list per kind (`delivery-template fields`), written {{customer.name}}; '
                '{{if document.po_number}}...{{end}} shows text only when the field has a value. An unknown field is '
                'refused, naming it.')

delivery_template_create = _write(
    'delivery-template create', 'Create an email template for a kind: a subject and a plain-text body. '
    + _FIELDS_HELP + ' Example: {"kind": "invoice", "name": "Short", "subject": "Invoice {{document.number}}", '
    '"body": "Attached."}.', m.TemplateCreateInput, m.TemplateOutput, plan_template_create, t.apply_create,
    errors=['E_VALIDATION'])
delivery_template_update = _write(
    'delivery-template update', "Change a template's name, subject or body, or make it its kind's default (the old "
    'default stops being one).', m.TemplateUpdateInput, m.TemplateOutput, plan_template_update, t.apply_update,
    positional=('template',), version=_TEMPLATE_VERSION,
    errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'])
delivery_template_deactivate = _write(
    'delivery-template deactivate', 'Stop offering a template; a default cannot be deactivated.',
    m.TemplateDeactivateInput, m.TemplateOutput, plan_template_deactivate, t.apply_deactivate,
    positional=('template',), version=_TEMPLATE_VERSION,
    errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'])
for _cmd in (delivery_template_create, delivery_template_update, delivery_template_deactivate):
    _cmd.resource_requirements = ()
delivery_template_show = _read('delivery-template show', 'Show an email template.', m.TemplateSelector,
                               m.TemplateOutput, plan_template_show, errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION'],
                               positional=('template',), resources=())
delivery_template_query = _read('delivery-template query', 'List email templates by kind, the default first.',
                                m.TemplateQueryInput, m.TemplatePageOutput, plan_template_query, errors=[],
                                resources=())
delivery_template_fields = _read('delivery-template fields', 'The merge fields a template may use, by kind.',
                                 m.TemplateFieldsInput, m.FieldsOutput, plan_template_fields, errors=[],
                                 resources=())
delivery_template_preview = _read(
    'delivery-template preview', 'Fill a template from a named document without drafting anything; empty fields '
    'are listed.', m.TemplatePreviewInput, m.PreviewOutput, plan_template_preview,
    errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE'], positional=('kind', 'document'),
    resources=())


# ---------------------------------------------------------------- the queue and the transport seam (increment 2)

from bookflow.company import delivery_connections as cn  # noqa: E402
from bookflow.company import delivery_dispatch as dq  # noqa: E402
from bookflow.company import delivery_queue_models as qm  # noqa: E402

_APPROVE = ("A person's go for ready messages waiting for one (an agent's release): by number, or all_waiting. A "
            'message leaves only with a go, and only through a sending connection. A person\'s act. '
            'Example: {"messages": ["M-1001"]}.')
_RETRY = ('Put a failed message, or one held because an attempt ended without an answer, back in the outbox. Check '
          'the sent mail first: if it went, mark it sent instead. A person\'s retry is their go; an agent\'s waits.')
_RUN = ('One dispatch pass now: claim due messages with a go, send each through the sending connection, record what '
        'it said (sent, try again later, failed, or held when the answer is unknown). With no connection it claims '
        'nothing. The host does this on its own about once a minute.')
_CONNECTION = ('A sending connection: where mail goes, under keys held in the keyring or environment (secret_ref), '
               'never in Bookflow. A person\'s act.')


def plan_delivery_approve(inp, ctx, s) -> Plan:
    return dq.prepare_approve(s, ctx, inp)


def plan_delivery_retry(inp, ctx, s) -> Plan:
    return dq.prepare_retry(s, ctx, inp)


def plan_delivery_run(inp, ctx, s) -> Plan:
    return dq.prepare_run(s, ctx, inp)


# R187 converts the people-only check (catalog PEOPLE_ONLY) to the capability delivery.send.
delivery_approve = _write('delivery approve', _APPROVE, qm.ApproveInput, m.MessagesOutput, plan_delivery_approve,
                          dq.apply_many, errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_WORK_DEPENDENCY',
                                                 'E_VERSION_CONFLICT', 'E_PERMISSION'])
delivery_retry = _write('delivery retry', _RETRY, qm.RetryInput, m.MessageOutput, plan_delivery_retry, d.apply_single,
                        positional=('message',), version=_VERSION,
                        errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_WORK_DEPENDENCY', 'E_VERSION_CONFLICT'])
delivery_run = _write('delivery run', _RUN, qm.RunInput, qm.RunOutput, plan_delivery_run, dq.apply_run, errors=[])


def plan_connection_add(inp, ctx, s) -> Plan:
    return cn.prepare_add(s, ctx, inp)


def plan_connection_update(inp, ctx, s) -> Plan:
    return cn.prepare_update(s, ctx, inp)


def plan_connection_enable(inp, ctx, s) -> Plan:
    return cn.prepare_enable(s, ctx, inp)


def plan_connection_disable(inp, ctx, s) -> Plan:
    return cn.prepare_disable(s, ctx, inp)


def plan_connection_remove(inp, ctx, s) -> Plan:
    return cn.prepare_remove(s, ctx, inp)


def plan_connection_check(inp, ctx, s) -> Plan:
    return cn.prepare_check(s, ctx, inp)


def plan_connection_show(inp, ctx, s) -> Plan:
    return Plan(cn.show(s, inp))


def plan_connection_query(inp, ctx, s) -> Plan:
    return Plan(cn.query(s, inp))


_CONNECTION_VERSION = ('delivery-connection show', 'connection', 'version')


def _connection_write(name, description, inp, planner, *, errors, positional=('connection',), version=_CONNECTION_VERSION):
    # Admin and people-only (catalog PEOPLE_ONLY); R187 converts this to the capability delivery.connect.
    cmd = command(name, scope='company', description=_CONNECTION + ' ' + description, input_model=inp,
                  output_model=qm.ConnectionOutput, writes={'company'}, required_role='admin', capability='company',
                  accepts_idempotency_key=True, positional=list(positional), version_source=version,
                  error_codes=[*errors, 'E_PERMISSION', *_TAIL])(planner)
    cmd.applier(cn.apply)
    return cmd


delivery_connection_add = _connection_write(
    'delivery-connection add', 'Add one, disabled, of a kind this build registers (none yet: every kind is refused, '
    'naming the kinds available).', qm.ConnectionAddInput, plan_connection_add, errors=['E_VALIDATION'],
    positional=(), version=None)
delivery_connection_update = _connection_write(
    'delivery-connection update', 'Change its name, settings, secret_ref or daily limit.', qm.ConnectionUpdateInput,
    plan_connection_update, errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'])
delivery_connection_enable = _connection_write(
    'delivery-connection enable', 'Let it send from this machine (a copy or restore elsewhere stays disabled).',
    qm.ConnectionWriteInput, plan_connection_enable, errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'])
delivery_connection_disable = _connection_write(
    'delivery-connection disable', 'Stop sending through it; ready messages wait.', qm.ConnectionWriteInput,
    plan_connection_disable, errors=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'])
delivery_connection_remove = _connection_write(
    'delivery-connection remove', 'Remove it (kept for the history).', qm.ConnectionWriteInput,
    plan_connection_remove, errors=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'])
delivery_connection_check = _connection_write(
    'delivery-connection check', 'Ask its transport whether it can connect with its secret; the answer is kept.',
    qm.ConnectionSelector, plan_connection_check, errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND'], version=None)


def _connection_read(name, description, inp, out, planner, *, errors, positional=()):
    return command(name, scope='company', description=description, input_model=inp, output_model=out,
                   required_role='member', capability='company', positional=list(positional),
                   error_codes=errors)(planner)


delivery_connection_show = _connection_read(
    'delivery-connection show', 'Show a sending connection: kind, settings, where its secret is (never the secret), '
    'whether it is enabled here.', qm.ConnectionSelector, qm.ConnectionOutput, plan_connection_show,
    errors=['E_RECORD_NOT_FOUND'], positional=('connection',))
delivery_connection_query = _connection_read(
    'delivery-connection query', 'The sending connections, and the transport kinds this build registers (none: '
    'nothing leaves this machine).', qm.ConnectionQueryInput, qm.ConnectionPageOutput, plan_connection_query,
    errors=[])


# ---------------------------------------------------------------- flags, the queue and batches (increment 3)

from bookflow.company import delivery_batch_models as bm  # noqa: E402
from bookflow.company import delivery_batches as db  # noqa: E402

_FLAG = ("Mark a document Email later (the anchor's checkbox): `delivery queue` lists it until a message carries it. "
         'Example: {"kind": "invoice", "document": "1007"}.')
_UNFLAG = ("Mark a document never to email: an explicit opt-out, which wins over the company's auto-flag date. "
           'clear removes the flag instead (back to neutral).')
_QUEUE = ('The documents to be emailed: flagged Email later, or dated on or after the auto-flag date for a customer '
          'who prefers email, with no live or sent message; each with its To or "no address". method mail lists '
          'the customers who prefer mail.')
_BATCH = (
    'Draft many messages in one act: queued (to-be-emailed documents), unsent with a kind (filters date_from, '
    'date_to, customer_type, customer), kind statement with period and balance_over (one per customer with a '
    'balance), or kind notice with ready. Always run it with dry_run first and show the person who gets what and '
    'who is skipped and why; then run it with the dry run\'s fingerprint (a changed set is refused). It drafts 10 per '
    'call and returns next: call again with it until done. '
    'send releases them too; combine puts a customer\'s documents in one message. To repeat it every month or day, '
    'use delivery-schedule create. Example: {"kind": "statement", '
    '"period": "2026-09", "balance_over": "0", "method": "email"}.')
_BATCH_QUERY = ("The batches: unfinished (still drafting, or with drafts to send or messages waiting for a go) or all, "
                "each with the commands that continue or cancel it.")
_BATCH_SHOW = "A batch: its selection, who gets what (with each message), who was skipped and why, and how far it is."
_BATCH_CANCEL = ('Cancel a batch as one unit, with --reason: every message it drafted that was never released or '
                 'exported is cancelled; the ones that block (released, exported, sent) are named and stay.')
_BATCH_VERSION = ('delivery batch-show', 'batch', 'version')


def plan_delivery_flag(inp, ctx, s) -> Plan:
    return db.prepare_flag(s, ctx, inp, True)


def plan_delivery_unflag(inp, ctx, s) -> Plan:
    return db.prepare_flag(s, ctx, inp, None if inp.clear else False)


def plan_delivery_batch_query(inp, ctx, s) -> Plan:
    return Plan(db.query(s, inp))


def plan_delivery_queue(inp, ctx, s) -> Plan:
    return Plan(db.queue(s, ctx, inp))


def plan_delivery_batch(inp, ctx, s) -> Plan:
    return db.prepare_batch(s, ctx, inp)


def plan_delivery_batch_show(inp, ctx, s) -> Plan:
    return Plan(db.show(s, inp))


def plan_delivery_batch_cancel(inp, ctx, s) -> Plan:
    return db.prepare_cancel(s, ctx, inp)


delivery_flag = _write('delivery flag', _FLAG, bm.FlagInput, bm.FlagOutput, plan_delivery_flag, db.apply_flag,
                       positional=('kind', 'document'),
                       errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'])
delivery_unflag = _write('delivery unflag', _UNFLAG, bm.UnflagInput, bm.FlagOutput, plan_delivery_unflag,
                         db.apply_flag, positional=('kind', 'document'),
                         errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'])
delivery_queue = _read('delivery queue', _QUEUE, bm.QueueInput, bm.QueueOutput, plan_delivery_queue,
                       errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])
# R187: `send` releases with a go only for an actor holding delivery.send (today: a person).
delivery_batch = _write('delivery batch', _BATCH, bm.BatchInput, bm.BatchOutput, plan_delivery_batch,
                        db.apply_batch,
                        errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_WORK_DEPENDENCY'])
delivery_batch_query = _read('delivery batch-query', _BATCH_QUERY, bm.BatchQueryInput, bm.BatchQueryOutput,
                             plan_delivery_batch_query, errors=['E_VALIDATION'])
delivery_batch_show = _read('delivery batch-show', _BATCH_SHOW, bm.BatchSelector, bm.BatchOutput,
                            plan_delivery_batch_show, errors=['E_RECORD_NOT_FOUND'], positional=('batch',))
delivery_batch_cancel = _write('delivery batch-cancel', _BATCH_CANCEL, bm.BatchCancelInput, bm.BatchOutput,
                               plan_delivery_batch_cancel, db.apply_cancel, positional=('batch',),
                               version=_BATCH_VERSION,
                               errors=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_REASON_REQUIRED'])


# ---------------------------------------------------------------- recurring schedules (increment 4)

from bookflow.company import delivery_schedule_models as sm  # noqa: E402
from bookflow.company import delivery_schedules as ds  # noqa: E402

_SCHEDULE_VERSION = ('delivery-schedule show', 'schedule', 'version')
_SCHEDULE_CREATE = (
    'Save a recurring batch: kind and selection as delivery batch takes them (statements take a period word), a '
    'frequency from start_date (a month-end start stays at month end), optional stop_date or remaining_runs. Each '
    'slot runs once on the host; after downtime only the latest missed slot runs, within catch_up_days (7). mode send '
    'is a draft until a person makes it live. Missing details are refused, never guessed. Example: {"name": '
    '"Month-end statements", "kind": "statement", "period": "month-to-date", "balance_over": "0", "frequency": '
    '"monthly", "start_date": "2026-10-31", "mode": "send"}.')
_SCHEDULE_UPDATE = ('Change a schedule. A change to what or when it sends returns a live one to draft, unless a person '
                    "passes the changed schedule's check fingerprint as confirm (dry_run shows it).")
_SCHEDULE_LIVE = (
    "Make a send schedule live: its runs then release with your go and pause if you lose it. One check first: run it "
    'with dry_run, show the person the check (selection, cadence, first run, who it would reach today), then run it '
    "with the check's fingerprint as confirm. A person's act.")
_SCHEDULE_PAUSE = 'Stop a schedule: nothing runs until it is resumed.'
_SCHEDULE_RESUME = ('Run a stopped or paused schedule again from today; slots that passed are skipped. A paused send '
                    'schedule comes back as a draft.')
_SCHEDULE_RUN = ('Do now what the host does on its tick: claim the due slot (once) and draft a chunk of its run, or '
                 'continue an open run. Nothing if nothing is due.')
_SCHEDULE_SHOW = 'A schedule, its state and recent runs; check includes the details a person confirms to make it live.'
_SCHEDULE_QUERY = 'The schedules, with cadence, next slot, state and last run.'


def plan_schedule_create(inp, ctx, s) -> Plan:
    return ds.prepare_create(s, ctx, inp)


def plan_schedule_update(inp, ctx, s) -> Plan:
    return ds.prepare_update(s, ctx, inp)


def plan_schedule_live(inp, ctx, s) -> Plan:
    return ds.prepare_live(s, ctx, inp)


def plan_schedule_pause(inp, ctx, s) -> Plan:
    return ds.prepare_pause(s, ctx, inp)


def plan_schedule_resume(inp, ctx, s) -> Plan:
    return ds.prepare_resume(s, ctx, inp)


def plan_schedule_run(inp, ctx, s) -> Plan:
    return ds.prepare_run(s, ctx, inp)


def plan_schedule_show(inp, ctx, s) -> Plan:
    return Plan(ds.show(s, ctx, inp))


def plan_schedule_query(inp, ctx, s) -> Plan:
    return Plan(ds.query(s, inp))


_SCHED_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_WORK_DEPENDENCY']
delivery_schedule_create = _write('delivery-schedule create', _SCHEDULE_CREATE, sm.ScheduleCreateInput,
                                  sm.ScheduleOutput, plan_schedule_create, ds.apply_create,
                                  errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND'])
# R187: confirm (staying live through a change) needs automate.delivery; today a person.
delivery_schedule_update = _write('delivery-schedule update', _SCHEDULE_UPDATE, sm.ScheduleUpdateInput,
                                  sm.ScheduleOutput, plan_schedule_update, ds.apply_update, positional=('schedule',),
                                  version=_SCHEDULE_VERSION, errors=[*_SCHED_ERRORS, 'E_PERMISSION'])
# R187: people-only until the capability automate.delivery exists.
delivery_schedule_live = _write('delivery-schedule live', _SCHEDULE_LIVE, sm.ScheduleLiveInput, sm.ScheduleOutput,
                                plan_schedule_live, ds.apply_act, positional=('schedule',), version=_SCHEDULE_VERSION,
                                errors=[*_SCHED_ERRORS, 'E_PERMISSION'])
delivery_schedule_pause = _write('delivery-schedule pause', _SCHEDULE_PAUSE, sm.ScheduleActInput, sm.ScheduleOutput,
                                 plan_schedule_pause, ds.apply_act, positional=('schedule',),
                                 version=_SCHEDULE_VERSION, errors=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'])
delivery_schedule_resume = _write('delivery-schedule resume', _SCHEDULE_RESUME, sm.ScheduleActInput,
                                  sm.ScheduleOutput, plan_schedule_resume, ds.apply_act, positional=('schedule',),
                                  version=_SCHEDULE_VERSION, errors=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'])
delivery_schedule_run = _write('delivery-schedule run', _SCHEDULE_RUN, sm.ScheduleSelector, sm.ScheduleOutput,
                               plan_schedule_run, ds.apply_run, positional=('schedule',), errors=_SCHED_ERRORS)
delivery_schedule_show = _read('delivery-schedule show', _SCHEDULE_SHOW, sm.ScheduleShowInput, sm.ScheduleOutput,
                               plan_schedule_show, errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION'],
                               positional=('schedule',))
delivery_schedule_query = _read('delivery-schedule query', _SCHEDULE_QUERY, sm.ScheduleQueryInput,
                                sm.SchedulePageOutput, plan_schedule_query, errors=['E_VALIDATION'])
