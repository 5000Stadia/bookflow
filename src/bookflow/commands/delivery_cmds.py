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
_UPDATE = 'Edit a draft or held message: To, Cc, Bcc, subject or body. The attachments stay as drafted.'
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
    return Plan(d.show(s, inp))


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
                         d.apply_export, role='member',
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
