"""Shared payment preparation commands. Financial verbs register when implemented."""
from bookflow.core.registry import command, Plan
from bookflow.company import schema as c, document_effects as effects
from bookflow.company import payment_selection as selection, payment_queries as query
from bookflow.company.payment_models import (
    SelectionCreateInput, SelectionUpdateInput, SelectionClearInput,
    SelectionShowInput, SelectionItemsInput, SelectionQueryInput,
)
from bookflow.company.payment_outputs import (
    SelectionOutput, SelectionWriteOutput, SelectionItemsOutput, SelectionPageOutput,
)

from bookflow.company import payments, payment_operations
from bookflow.company.payment_models import PaymentReceiveInput, PaymentApplyInput, PaymentShowInput
from bookflow.company.payment_models import PaymentUnapplyInput, PaymentVoidInput, PaymentUpdateInput
from bookflow.company.payment_outputs import PaymentWriteOutput, PaymentOutput


def _financial(verb, model):
    def planner(inp, ctx, s):
        plan = payments.prepare(s, ctx, inp, verb)
        if verb in ('update', 'void') and plan.preview.changed and not plan.data.get('recovered'):
            from bookflow.company.deposit_dependencies import require_unclaimed
            require_unclaimed(s, plan.data['header']['id'])
        if s.dry_run and not plan.data.get('recovered'):
            from bookflow.company.payment_pages import preview_output
            plan.preview = preview_output(s, ctx, inp, plan)
        return plan
    cmd = command('payment ' + verb, scope='company',
        description={
            'receive': 'Record new cash and apply it across compatible customer/job invoices; retain unapplied owned credit with derived exact-party AR ownership and immutable invoice applications.',
            'apply': 'Apply existing payment credit to its exact-party invoices without ledger posting.',
            'unapply': 'Reverse selected active applications and their current allocations at original dates; retain owned credit without ledger posting.',
            'void': 'Void an unapplied receipt with exact original-date ledger reversals; applications must be explicitly unapplied first.',
            'update': 'Correct receipt content with immutable replacement postings, fixed job ownership and complete source allocation restatement.',
        }[verb],
        input_model=model, output_model=PaymentWriteOutput, writes={'company'}, required_role='standard', capability='ledger.post',
        accepts_idempotency_key=True, positional=[] if verb == 'receive' else ['payment'],
        error_codes=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_APPLICATION_CAPACITY', 'E_APPLICATION_INCOMPATIBLE',
            'E_APPLICATION_INACTIVE', 'E_PAYMENT_OPERATION_KEY_REUSED', 'E_SELECTION_CONSUMED', 'E_PREVIEW_STALE',
            'E_PERIOD_CLOSED', 'E_INACTIVE_REFERENCE', 'E_DUPLICATE_NUMBER', 'E_AMOUNT_PRECISION', 'E_VALUE_RANGE',
            'E_REASON_REQUIRED', 'E_HAS_APPLICATIONS']+(['E_DEPOSIT_DEPENDENCY'] if verb in ('update','void') else [])+(['E_RECOVERY_PENDING'] if verb in ('receive','apply') else []))(planner)
    cmd.ledger = True
    cmd.permanent_recovery = lambda inp, ctx, s: payment_operations.recover(inp, ctx, s, 'payment ' + verb)
    cmd.applier(payments.apply)
    return cmd


payment_receive = _financial('receive', PaymentReceiveInput)
payment_apply = _financial('apply', PaymentApplyInput)
payment_unapply = _financial('unapply', PaymentUnapplyInput)
payment_void = _financial('void', PaymentVoidInput)
payment_update = _financial('update', PaymentUpdateInput)


@command('payment show', scope='company', description='Show a receipt revision separately from current owned credit and application capacity.',
    input_model=PaymentShowInput, output_model=PaymentOutput, required_role='member', capability='ledger.read',
    positional=['payment'], error_codes=['E_RECORD_NOT_FOUND'])
def payment_show(inp, ctx, s):
    from bookflow.company.payment_dependencies import issue
    output = payments.show(s, inp)
    output.settlement_guard = issue(s, 'payment', output.id)
    return Plan(output)


from bookflow.company import payment_preparation
from bookflow.company.payment_models import PaymentInvoicesInput, PaymentSuggestInput, PaymentCalculateInput, PaymentQueryInput
from bookflow.company.payment_outputs import PaymentCandidatesOutput, PaymentCalculationOutput, PaymentPageOutput


def _preparation(verb, model, output, planner):
    @command('payment ' + verb, scope='company',
        description=('Find invoices this payer can pay. ' if verb == 'invoices' else '') +
            'Read complete compatible payment candidates or calculate shared amount origins; page bounds never limit receipt intent.',
        input_model=model, output_model=output, required_role='member', capability='ledger.read',
        error_codes=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_QUERY_STALE', 'E_APPLICATION_INCOMPATIBLE', 'E_INACTIVE_REFERENCE', 'E_AMOUNT_PRECISION'] + (['E_PAYMENT_PROFILE_INVALID'] if verb == 'query' else []))
    def plan(inp, ctx, s):
        return Plan(output(**planner(s, inp)))
    return plan


payment_invoices = _preparation('invoices', PaymentInvoicesInput, PaymentCandidatesOutput, payment_preparation.invoices)
payment_suggest = _preparation('suggest', PaymentSuggestInput, PaymentCalculationOutput, payment_preparation.suggest)
payment_calculate = _preparation('calculate', PaymentCalculateInput, PaymentCalculationOutput, payment_preparation.calculate)
payment_query = _preparation('query', PaymentQueryInput, PaymentPageOutput, payment_preparation.payment_page)


from bookflow.company.payment_models import PaymentHistoryInput, ApplicationShowInput, ApplicationHistoryInput
from bookflow.company.payment_outputs import ApplicationOutput, SettlementHistoryOutput
from bookflow.company import payment_history


@command('payment history', scope='company', description='Page receipt revisions and immutable settlement operation, application and allocation history in audit order.',
    input_model=PaymentHistoryInput, output_model=SettlementHistoryOutput, required_role='member', capability='ledger.read',
    positional=['payment'], error_codes=['E_RECORD_NOT_FOUND', 'E_QUERY_STALE'])
def payment_history_read(inp, ctx, s):
    return Plan(SettlementHistoryOutput(**payment_history.payment_history(s, inp)))


@command('application show', scope='company', description='Inspect an original application or exact inverse separately from its current live allocations and payment/invoice state.',
    input_model=ApplicationShowInput, output_model=ApplicationOutput, required_role='member', capability='ledger.read',
    positional=['application'], error_codes=['E_RECORD_NOT_FOUND'])
def application_show(inp, ctx, s):
    return Plan(ApplicationOutput(**payment_history.application_show(s, inp)))


@command('application history', scope='company', description='Page an application and all exact inverse and allocation restatement evidence in immutable audit order.',
    input_model=ApplicationHistoryInput, output_model=SettlementHistoryOutput, required_role='member', capability='ledger.read',
    positional=['application'], error_codes=['E_RECORD_NOT_FOUND', 'E_QUERY_STALE'])
def application_history(inp, ctx, s):
    return Plan(SettlementHistoryOutput(**payment_history.application_history(s, inp)))


from bookflow.company.payment_models import PaymentPreviewItemsInput, PaymentOperationShowInput
from bookflow.company.payment_outputs import PaymentEffectItemsOutput, PaymentOperationOutput
from bookflow.company import payment_pages


@command('payment preview items', scope='company', description='Read prospective receipt/application effect pages by pure recomputation of exact original intent and facts; stale pages fail without writes.',
    input_model=PaymentPreviewItemsInput, output_model=PaymentEffectItemsOutput, required_role='standard', capability='ledger.post',
    error_codes=['E_RECORD_NOT_FOUND', 'E_PREVIEW_STALE', 'E_QUERY_STALE', 'E_VERSION_CONFLICT', 'E_APPLICATION_CAPACITY',
                 'E_APPLICATION_INCOMPATIBLE', 'E_SELECTION_CONSUMED', 'E_PERIOD_CLOSED', 'E_INACTIVE_REFERENCE'])
def payment_preview_items(inp, ctx, s):
    return Plan(payment_pages.items(s, ctx, inp))


@command('payment operation show', scope='company', description='Recover the canonical original permanent request, including omissions and reason, alongside historical effect and current credit.',
    input_model=PaymentOperationShowInput, output_model=PaymentOperationOutput, required_role='member', capability='ledger.read',
    positional=['operation_key'], error_codes=['E_RECORD_NOT_FOUND'])
def payment_operation_show(inp, ctx, s):
    import json
    row, request = payment_pages.operation(s, inp)
    original = request['original_request']
    output = json.loads(row['effect_snapshot'])
    invoice_correction = row['command'] == 'invoice update'
    return Plan(PaymentOperationOutput(operation_id=row['id'], operation_key=row['operation_key'],
        audit_event_id=row['audit_event_id'], request_schema_version=row['request_schema_version'], canonical_hash=row['request_hash'],
        provided_fields=original['provided_fields'], context_provided_fields=original['context_provided_fields'],
        execution=json.loads(row['execution_snapshot']),
        request=dict(command=original['command'], input=dict(original['input'], operation_key=row['operation_key']), context=original['context']),
        original=output['settlement'] if invoice_correction else output,
        current=query.invoice_current(s, output['id']) if invoice_correction else payments.current_output(s, output['id'])))


from bookflow.company.payment_models import PaymentOperationItemsInput


@command('payment operation items', scope='company', description='Page the complete immutable request/application/allocation effects of a permanent operation; authorize the complete owning graph before any page.',
    input_model=PaymentOperationItemsInput, output_model=PaymentEffectItemsOutput, required_role='member', capability='ledger.read',
    positional=['operation_key'], error_codes=['E_RECORD_NOT_FOUND', 'E_QUERY_STALE'])
def payment_operation_items(inp, ctx, s):
    import json
    operation, _ = payment_pages.operation(s, inp)
    rows = effects.rows(s, c.payment_operation_items, c.payment_operation_items.c.operation_id == operation['id'],
                       c.payment_operation_items.c.kind == inp.kind, order=c.payment_operation_items.c.ordinal)
    values = [json.loads(row['item_snapshot']) for row in rows]
    return Plan(PaymentEffectItemsOutput(**query.page(s, 'payment operation items', inp, values,
        facts=[operation['id'], inp.kind, values]), projection='committed', committed=True, kind=inp.kind))


from bookflow.company.payment_models import PaymentSettlementInput, InvoiceSettlementInput
from bookflow.company.payment_outputs import InvoiceSettlementOutput, InvoiceSettlementReadOutput, PaymentSettlementOutput


@command('payment settlement', scope='company', description='Page all current exact-party credit components or active applications, including capacity committed at future dates.',
    input_model=PaymentSettlementInput, output_model=PaymentSettlementOutput, required_role='member', capability='ledger.read',
    positional=['payment'], error_codes=['E_RECORD_NOT_FOUND', 'E_QUERY_STALE'])
def payment_settlement(inp, ctx, s):
    from bookflow.company.payment_history import payment
    return Plan(PaymentSettlementOutput(**payment(s, inp)))


@command('invoice settlement', scope='company', description='Show current invoice gross, applied and due with separate concurrency and commercial revision identities.',
    input_model=InvoiceSettlementInput, output_model=InvoiceSettlementReadOutput, required_role='member', capability='ledger.read',
    positional=['invoice'], error_codes=['E_RECORD_NOT_FOUND', 'E_QUERY_STALE'])
def invoice_settlement(inp, ctx, s):
    from bookflow.company.payment_dependencies import issue
    from bookflow.company.payment_history import invoice
    output = InvoiceSettlementReadOutput(**invoice(s, inp))
    output.settlement_guard = issue(s, 'invoice', output.invoice_id)
    return Plan(output)


from bookflow.company.payment_models import SettlementChangesInput
from bookflow.company.payment_outputs import SettlementChangesOutput


@command('payment settlement changes', scope='company',
    description='Inspect actual intervening settlement events from an authenticated owned audit baseline; unknown history is explicit.',
    input_model=SettlementChangesInput, output_model=SettlementChangesOutput, required_role='member', capability='ledger.read',
    error_codes=['E_RECORD_NOT_FOUND', 'E_PREVIEW_STALE', 'E_QUERY_STALE'])
def settlement_changes(inp, ctx, s):
    from bookflow.company.payment_dependencies import compare, issue
    result = compare(s, inp.guard)
    saved = result['saved']
    # Age is display data and must not invalidate the fixed fact continuation.
    stable = [{k: v for k, v in row.items() if k != 'age_seconds'} for row in result['changes']]
    page = query.page(s, 'payment settlement changes', inp, result['changes'],
        facts=[result['current']['digest'], result['unknown_record_ids'], stable])
    return Plan(SettlementChangesOutput(**page, unknown_history=result['unknown_history'],
        unknown_record_ids=result['unknown_record_ids'], settlement_guard=issue(s, saved['owner_type'], saved['owner_id'])))


def _write(verb, model):
    def plan(inp, ctx, s):
        return selection.prepare(s, ctx, inp, verb)
    cmd = command('payment selection ' + verb, scope='company',
        description='Prepare a shared nonposting payment draft with immutable header/row amount origins and complete invoice selection history.',
        input_model=model, output_model=SelectionWriteOutput, writes={'company'},
        required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
        positional=[] if verb == 'create' else ['selection'],
        version_source=None if verb == 'create' else ('payment selection show', 'selection', 'version'),
        error_codes=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_SELECTION_CONSUMED',
                     'E_APPLICATION_INCOMPATIBLE', 'E_PREVIEW_STALE', 'E_INACTIVE_REFERENCE', 'E_AMOUNT_PRECISION', 'E_RECOVERY_PENDING'])(plan)
    cmd.ledger = True
    cmd.applier(selection.apply)
    cmd.authorize_input = selection.authorize_input
    cmd.replay = selection.replay
    return cmd


selection_create = _write('create', SelectionCreateInput)
selection_update = _write('update', SelectionUpdateInput)
selection_clear = _write('clear', SelectionClearInput)


@command('payment selection show', scope='company', description='Read a shared payment draft or an exact immutable revision with saved amount origins.',
    input_model=SelectionShowInput, output_model=SelectionOutput, required_role='member', capability='ledger.read',
    positional=['selection'], error_codes=['E_RECORD_NOT_FOUND', 'E_RECOVERY_PENDING'])
def selection_show(inp, ctx, s):
    return Plan(selection.show(s, inp))


@command('payment selection items', scope='company', description='Page the complete selected invoice manifest at a pinned draft revision.',
    input_model=SelectionItemsInput, output_model=SelectionItemsOutput, required_role='member', capability='ledger.read',
    positional=['selection'], error_codes=['E_RECORD_NOT_FOUND', 'E_QUERY_STALE'])
def selection_items(inp, ctx, s):
    header = selection.resolve(s, inp.selection)
    revision, context, items = selection.saved(s, header, inp.revision)
    values = [dict(row, currency=context['currency']) for row in items]
    return Plan(SelectionItemsOutput(**query.page(s, 'payment selection items', inp, values, facts=revision['manifest_hash'])))


@command('payment selection query', scope='company', description='Page shared drafts by creation time and identity; protection covers their complete history.',
    input_model=SelectionQueryInput, output_model=SelectionPageOutput, required_role='member', capability='ledger.read',
    error_codes=['E_QUERY_STALE', 'E_RECOVERY_PENDING'])
def selection_query(inp, ctx, s):
    return Plan(SelectionPageOutput(**selection.query_page(s, inp)))
