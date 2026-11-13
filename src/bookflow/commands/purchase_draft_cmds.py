"""Shared non-posting bill draft commands."""
from bookflow.company import purchase_drafts as d, purchase_draft_models as m
from bookflow.core.registry import Plan, command


def plan_create(inp, ctx, s):
    return d.prepare(s, ctx, inp, 'create')


def plan_update(inp, ctx, s):
    return d.prepare(s, ctx, inp, 'update')


def plan_cancel(inp, ctx, s):
    return d.prepare(s, ctx, inp, 'cancel')


def plan_show(inp, ctx, s):
    return Plan(d.output(s, d.row(s, inp.draft)))


def plan_preview(inp, ctx, s):
    value = d.output(s, d.row(s, inp.draft))
    _, preview, _ = d.calculate(s, value.payload, ctx, value.operation, value.original_id, value.original_version, value.kind)
    return Plan(value.model_copy(update={'preview': preview}))


def plan_list(inp, ctx, s):
    return Plan(d.listing(s, inp))


def _write(verb, model, planner, description):
    cmd = command('purchase-draft ' + verb, scope='company', description=description,
        input_model=model, output_model=m.DraftOutput, writes={'company'}, required_role='standard',
        capability='approval.prepare', accepts_idempotency_key=True,
        positional=[] if verb == 'create' else ['draft'],
        version_source=None if verb == 'create' else ('purchase-draft show', 'draft', 'version'),
        error_codes=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_APPROVAL_STATE',
                     'E_REASON_REQUIRED', 'E_IDEMPOTENCY_MISMATCH', 'E_INACTIVE_REFERENCE', 'E_AMOUNT_PRECISION', 'E_VALUE_RANGE'])(planner)
    cmd.resource_requirements = (('ledger.read', 'member'),)
    cmd.permanent_recovery = lambda inp, ctx, s: d.recover(inp, ctx, s, verb)
    cmd.applier(d.apply)
    return cmd


def _read(verb, model, output, planner, description):
    cmd = command('purchase-draft ' + verb, scope='company', description=description,
        input_model=model, output_model=output, required_role='standard', capability='approval.read',
        positional=[] if verb == 'list' else ['draft'],
        error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_INACTIVE_REFERENCE'])(planner)
    cmd.resource_requirements = (('ledger.read', 'member'),)
    return cmd


purchase_draft_create = _write('create', m.CreateInput, plan_create,
    'Save a native purchase draft with a reason and permanent operation_key. The draft posts nothing: no AP, '
    'ledger, inventory or purchase consumption. Use --dry-run for an exact unsaved preview. '
    'kind is bill or purchase_order; operation is create or correct with original_id and original_version. Sources and exact amounts are captured without reservations. Restore awaits native bill restoration. Another preparer can resume its shared ID.')
purchase_draft_update = _write('update', m.UpdateInput, plan_update,
    'Replace all commercial fields of a saved purchase draft, carrying its exact expected_version, a new operation_key '
    'and reason. Omitted optional fields reset to defaults. Prior revisions remain readable; a cancelled draft refuses.')
purchase_draft_cancel = _write('cancel', m.CancelInput, plan_cancel,
    'Cancel a saved purchase draft with expected_version, operation_key and reason. Preserve every revision and attribution; '
    'cancellation has no accounting effect and cannot be undone.')
purchase_draft_show = _read('show', m.DraftInput, m.DraftOutput, plan_show,
    'Show the saved purchase draft, exact captured preview and immutable revision history, including cancelled drafts.')
purchase_draft_preview = _read('preview', m.DraftInput, m.DraftOutput, plan_preview,
    'Revalidate a saved purchase draft and calculate its exact current total without saving or posting. The captured '
    'revision history stays unchanged. Use create --dry-run to preview a bill before saving.')
purchase_draft_list = _read('list', m.ListInput, m.ListOutput, plan_list,
    'List saved purchase drafts in stable ID order, optionally by lifecycle state. Continue with next_cursor; '
    'a cursor belongs to its state filter. These records never appear in AP aging or posted bill lists.')

PURCHASE_DRAFT_COMMANDS = [purchase_draft_create, purchase_draft_update, purchase_draft_cancel,
                           purchase_draft_show, purchase_draft_preview, purchase_draft_list]
