"""`bulk-edit`: change many customers, vendors, items or accounts at once, as one reversible batch (R107 increment 6,
design/bulk-import.md §7). The work lives in ``company/bulk_edit.py``.

Capability `bulk-edit` (reads at member, writes at standard), checked beside each record's own `update` capability:
every record changes through its ordinary update, validated, permission-checked and audited as a typed edit, so the
grid grants nothing. A reversal is an edit too, under the caller's own update grant.
"""
from bookflow.company import bulk_edit
from bookflow.company import bulk_edit_models as m
from bookflow.core.registry import Applied, Plan, Touched, command

_C = ('E_VALIDATION', 'E_RECORD_NOT_FOUND')
_PREVIEW = (
    'Preview a grid edit of many records of one list (customer, vendor, item, account), changing nothing in the '
    'books: pick records by the list\'s own query `filter`, `query` text or named `records`; give cell `changes`, a '
    '`paste`d block and column `operations` (set, clear, copy_down, replace, add, multiply, percent, set_from; a '
    'computed amount rounds half-up to the currency\'s places or to `round`). Returns the diff (each record\'s '
    'fields before and after), money totals before and after, and each record its update would refuse, with the '
    'command\'s code. Then `bulk-edit apply`. Before applying an edit that sets how the business works from now on '
    '(new terms or prices), show its exact diff and totals to your principal once. Example: {"kind": "item", '
    '"filter": ["category=Fixtures"], "operations": [{"op": "percent", "field": "price", "by": "5", '
    '"round": "0.05"}]}.')
_APPLY = (
    'Apply a previewed grid edit: one ordinary update per record carrying the version the preview read, each its own '
    'audit event, all one batch. A record someone changed meanwhile merges when they changed other fields, and is '
    'refused (conflict, naming who and when) when they changed the same one; the rest go on (all or nothing undoes '
    'them instead). Returns each record\'s outcome. Example: {"run": "01J..."}.')
_REVERSE = (
    'Undo a grid edit as one unit: every record it changed gets its exact prior values back through its update. '
    'Refused (E_BATCH_BLOCKED), naming each record and who changed it when, if any was edited again since; '
    '`skip_changed` restores the rest and leaves those (reversed_partly). --dry-run shows the plan and a fingerprint. '
    'Example: {"run": "01J..."}.')


def plan_columns(inp, ctx, s) -> Plan:
    return Plan(m.ColumnsOutput(**bulk_edit.column_list(s, inp.kind)))


def plan_grid(inp, ctx, s) -> Plan:
    return Plan(m.GridOutput(**bulk_edit.grid(s, ctx, inp)))


def plan_preview(inp, ctx, s) -> Plan:
    prepared = bulk_edit.prepare_preview(s, ctx, inp)
    rows = prepared['rows']
    out = m.RunOutput(
        run={'id': prepared['run_id'], 'kind': inp.kind, 'state': 'previewed', 'atomic': inp.atomic,
             'label': prepared['label'], 'reason': ctx.reason, 'selection': prepared['selection'],
             'asked': prepared['operations']},
        counts={'selected': prepared['selected'], 'unchanged': prepared['unchanged'],
                'change': sum(r['status'] == 'change' for r in rows),
                'refused': sum(r['status'] == 'refused' for r in rows)},
        totals=prepared['totals'],
        rows=[{**{k: r[k] for k in ('seq', 'record_type', 'record_id', 'label', 'version', 'status', 'changes',
                                    'code', 'message')}, 'outcome': None} for r in rows[:bulk_edit.ROWS_SHOWN]],
        next_cursor=rows[bulk_edit.ROWS_SHOWN - 1]['seq'] if len(rows) > bulk_edit.ROWS_SHOWN else None,
        next=f"bulk-edit apply {prepared['run_id']}" if any(r['status'] == 'change' for r in rows) else None)
    return Plan(out, {'prepared': prepared})


def apply_preview(plan: Plan, ctx, s) -> Applied:
    prepared = plan.data['prepared']
    bulk_edit.apply_preview(s, ctx, prepared)
    touched = [Touched('bulk_edit_run', prepared['run_id'], 'create', None, 1,
                       {'kind': prepared['kind'].name, 'records': prepared['selected'],
                        'rows': len(prepared['rows'])}, db='company')]
    return Applied(plan.preview, touched, f"previewed {prepared['label']}")


def _finalized(s, ctx, what, work, model) -> Applied:
    """Each record commits through its own nested update (as `import run` does): the dispatcher's own transaction is
    closed first, and each update writes its own audit event."""
    from bookflow.commands.import_cmds import _finalized as finalized
    return finalized(s, ctx, what, work, model)


def plan_apply(inp, ctx, s) -> Plan:
    return Plan(m.RunOutput(**bulk_edit.show(s, ctx, inp.run)), {'input': inp})


def apply_apply(plan: Plan, ctx, s) -> Applied:
    def work():
        out = bulk_edit.apply(s, ctx, plan.data['input'].run)
        out.pop('notes', None)
        return out
    return _finalized(s, ctx, 'bulk-edit.apply', work, m.RunOutput)


def _reverse_out(out: dict) -> dict:
    return {k: out.get(k) for k in m.ReverseOutput.model_fields} | {'skipped': out.get('skipped') or []}


def plan_reverse(inp, ctx, s) -> Plan:
    if s.dry_run:
        return Plan(m.ReverseOutput(**_reverse_out(bulk_edit.reverse(s, ctx, inp, dry_run=True))), {'input': inp})
    bulk_edit.run_read(s, inp.run)
    return Plan(m.ReverseOutput(run=inp.run, batch={}, state='live', reversed_now=0, steps=[], blockers=[], skipped=[],
                                fingerprint=None, reversal_batch=None, event_id=None), {'input': inp})


def apply_reverse(plan: Plan, ctx, s) -> Applied:
    return _finalized(s, ctx, 'bulk-edit.reverse',
                      lambda: _reverse_out(bulk_edit.reverse(s, ctx, plan.data['input'], dry_run=False)),
                      m.ReverseOutput)


def plan_show(inp, ctx, s) -> Plan:
    return Plan(m.RunOutput(**bulk_edit.show(s, ctx, inp.run, outcome=inp.outcome, limit=inp.limit,
                                             cursor=inp.cursor)))


def plan_query(inp, ctx, s) -> Plan:
    return Plan(m.QueryOutput(**bulk_edit.query(s, ctx, inp)))


def _read(name, description, inp, out, planner, *, positional=(), errors=_C):
    return command(name, scope='company', description=description, input_model=inp, output_model=out,
                   required_role='member', capability='bulk-edit', positional=list(positional),
                   error_codes=list(errors))(planner)


def _write(name, description, inp, out, planner, applier, *, positional=(), errors=_C):
    cmd = command(name, scope='company', description=description, input_model=inp, output_model=out,
                  writes={'company'}, required_role='standard', capability='bulk-edit', positional=list(positional),
                  error_codes=list(errors))(planner)
    cmd.applier(applier)
    return cmd


bulk_edit_columns = _read('bulk-edit columns', 'The columns a grid edit of a list can change: each field with its '
                          'type, choices and the list a reference names. Example: {"kind": "item"}.',
                          m.ColumnsInput, m.ColumnsOutput, plan_columns, positional=('kind',),
                          errors=('E_VALIDATION',))
bulk_edit_grid = _read('bulk-edit grid', 'The records a selection holds (the list\'s query filter, search text or '
                       'named records) with the chosen columns\' current values, as the grid shows them. Example: '
                       '{"kind": "customer", "filter": ["terms=Net 30"], "columns": ["terms_id", "credit_limit"]}.',
                       m.GridInput, m.GridOutput, plan_grid, positional=('kind',))
bulk_edit_preview = _write('bulk-edit preview', _PREVIEW, m.PreviewInput, m.RunOutput, plan_preview, apply_preview,
                           positional=('kind',))
bulk_edit_apply = _write('bulk-edit apply', _APPLY, m.ApplyInput, m.RunOutput, plan_apply, apply_apply,
                         positional=('run',), errors=('E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'))
bulk_edit_reverse = _write('bulk-edit reverse', _REVERSE, m.ReverseInput, m.ReverseOutput, plan_reverse,
                           apply_reverse, positional=('run',),
                           errors=('E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_BATCH_BLOCKED', 'E_VERSION_CONFLICT'))
bulk_edit_show = _read('bulk-edit show', 'What a grid edit changed: per record, each field before and after, its '
                       'outcome (updated, merged, conflict, refused), money totals, and whether it can be reversed.',
                       m.ShowInput, m.RunOutput, plan_show, positional=('run',))
bulk_edit_query = _read('bulk-edit query', 'List grid edits, newest first, by list or state, with their counts.',
                        m.QueryInput, m.QueryOutput, plan_query, errors=('E_VALIDATION',))
