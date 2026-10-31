"""`import` and `batch`: bring list records in from a file, and reverse any batch as one unit (R107 increments 0 and 1,
design/bulk-import.md). The work lives in ``company/imports.py`` and ``company/batches.py``.

Capability `import` (reads at member, writes at standard), checked beside each row's own command capability: the
importer grants nothing, and every row is its command's ordinary input, validated, permission-checked and audited as
a typed one. A reversal runs the ordinary inverse commands under the caller's own grants.
"""
from bookflow.company import batches, import_mappings, imports
from bookflow.company import import_models as m
from bookflow.core.registry import Applied, Plan, Touched, command

_C = ('E_VALIDATION', 'E_RECORD_NOT_FOUND')
_PREVIEW = (
    'Read a file of records and say what importing it would do, writing nothing to the books. The file is `content` '
    '(a CSV, or cells pasted from a spreadsheet) or an `attachment`; its encoding, delimiter, money and date formats '
    'are detected. Columns map to the kind\'s create-command fields by header (`columns` overrides: '
    '`{"Cust Name": "name"}`); each row is keyed (name by default) so a re-run of the same `source` never '
    'duplicates, and runs through its command in dry-run. Returns counts, the mapping, unknown references with '
    'suggestions, the first refused rows with line, column, code and reason, and a sample of the exact inputs; then '
    '`import run`. Send it without dry_run: it writes nothing to the books either way, but only a real call saves '
    'the run that `import decide` and `import run` continue. Before running, an import that sets how the business '
    'works from then on (customers with their terms, a mapping next month\'s file will use) gets one check with the '
    'owner of its exact details from this preview: the counts and how each unknown value is mapped; a value with no '
    'plain match is asked about, never guessed. A plainly named small import just runs. '
    'Example: {"kind": "customer", "content": "Name,Email\\nAcme,ap@acme.test\\n"}.')
_RUN = (
    'Run the next slice of a previewed import: each row through its ordinary command, written with its audit event, '
    'its key and its batch effect in one transaction. Returns counts, `state` (running, stopped, done) and `next`; '
    'call again until done. A run advances only when called; after a crash it resumes at the first row without an '
    'outcome and repeats nothing. Rows revalidate as they run. Example: {"run": "01J..."}.')
_REVERSE = (
    'Undo everything an import run made, as one unit, through its batch: created records are renamed out of the way '
    'and deactivated (they leave the lists and stay in history; their keys are released so the corrected file '
    'imports again), updated records get their prior values back. Refused (E_BATCH_BLOCKED, every blocker named with '
    'what clears it) while a record was changed since or is in use; then nothing changes. --dry-run shows the plan '
    'and a fingerprint. Example: {"run": "01J..."}.')


def plan_import_kinds(inp, ctx, s) -> Plan:
    return Plan(m.ImportKindsOutput(**imports.kinds_list()))


def plan_import_template(inp, ctx, s) -> Plan:
    return Plan(m.ImportTemplateOutput(**imports.template(inp.kind)))


def plan_import_preview(inp, ctx, s) -> Plan:
    prepared = imports.prepare_preview(s, ctx, inp)
    preview = _preview_output(s, prepared)
    return Plan(preview, {'prepared': prepared})


def _preview_output(s, prepared) -> m.ImportRunOutput:
    from collections import Counter
    rows = prepared['rows']
    counts = dict(Counter(r.status for r in rows))
    refused = [{'row': r.row_no, 'line': r.line, 'status': r.status, 'code': r.code, 'message': r.message,
                'column': r.column, 'record_type': None, 'record_id': None}
               for r in rows if r.status == 'refused'][:imports.REFUSED_SHOWN]
    sample = [{'row': r.row_no, 'command': prepared['kind'].create if r.status == 'create' else prepared['kind'].update,
               'input': r.payload} for r in rows if r.status in ('create', 'update')][:imports.SAMPLE]
    table, mapping = prepared['table'], prepared['mapping']
    return m.ImportRunOutput(
        run={'id': prepared['run_id'], 'kind': prepared['kind'].name, 'mode': prepared['input'].mode,
             'state': 'waiting' if len(prepared['references']) > len(prepared['create']) else 'previewed', 'source': prepared['source'],
             'file_sha256': table.sha256, 'encoding': table.encoding, 'rows': len(rows),
             'atomic': not prepared['input'].partial},
        counts=counts, mapping=mapping.as_dict(table.headers), unknown_references=prepared['references'],
        decisions=mapping.decisions + [dict(r, decision='reference') for r in prepared['references']],
        refused=refused, sample=sample, notes=prepared['notes'], matched=prepared['matched'],
        dropped=prepared['dropped'], dropped_total=prepared['dropped_total'], next=None)


def apply_import_preview(plan: Plan, ctx, s) -> Applied:
    prepared = plan.data['prepared']
    out = imports.apply_preview(s, ctx, prepared)
    touched = [Touched('import_run', prepared['run_id'], 'create', None, 1,
                       {'kind': prepared['kind'].name, 'rows': len(prepared['rows'])}, db='company')]
    counts = ', '.join(f'{k} {v}' for k, v in sorted(out['counts'].items()))
    return Applied(m.ImportRunOutput(**out), touched, f"previewed an import of {len(prepared['rows'])} "
                                                       f"{prepared['kind'].name} rows: {counts}")


def plan_import_check(inp, ctx, s) -> Plan:
    return Plan(m.ImportCheckOutput(**imports.check(s, ctx, inp)))


def plan_import_decide(inp, ctx, s) -> Plan:
    plan = import_mappings.plan_decide(s, ctx, inp)
    out = imports.show(s, ctx, inp.run)
    return Plan(m.ImportRunOutput(**out), {'plan': plan})


def apply_import_decide(plan: Plan, ctx, s) -> Applied:
    p = plan.data['plan']
    out = import_mappings.apply_decide(s, ctx, p)
    run = p['run']
    told = ', '.join(f"{d['action']} {d['ref']['value']!r}" for d in p['decisions'])
    touched = [Touched('import_run', run['id'], 'update', run['version'], run['version'] + (p['left'] == 0),
                       {'decisions': [{'field': d['ref']['field'], 'value': d['ref']['value'], 'action': d['action'],
                                       'target': d['target']} for d in p['decisions']]}, db='company')]
    return Applied(m.ImportRunOutput(**out), touched, f'decided for import {run["id"]}: {told}')


def plan_mapping_save(inp, ctx, s) -> Plan:
    run = imports._run_read(s, inp.from_run)
    recipe = import_mappings.recipe_from_run(s, run, inp.source)
    plan = import_mappings.plan_save(s, ctx, inp.source, recipe, run['kind'])
    preview = (import_mappings.show(s, inp.source) if plan['action'] == 'unchanged' else
               {'source': {'name': inp.source, 'kind': run['kind']}, 'mapping': {**recipe, 'version': plan['version']},
                'saved': True})
    return Plan(m.ImportMappingOutput(**preview), {'plan': plan, 'recipe': recipe, 'run': run, 'source': inp.source})


def apply_mapping_save(plan: Plan, ctx, s) -> Applied:
    d = plan.data
    out = import_mappings.apply_save(s, ctx, d['source'], d['recipe'], d['run']['kind'], d['plan'], d['run']['id'])
    mapping = out['mapping']
    touched = [] if not out['saved'] else [
        Touched('import_mapping', mapping['id'], 'create', None, mapping['version'],
                {'source': out['source']['name'], 'version': mapping['version']}, db='company')]
    what = (f"saved import mapping {out['source']['name']!r} version {mapping['version']}" if out['saved']
            else f"import mapping {out['source']['name']!r} unchanged")
    return Applied(m.ImportMappingOutput(**out), touched, what)


def plan_mapping_show(inp, ctx, s) -> Plan:
    return Plan(m.ImportMappingOutput(**import_mappings.show(s, inp.source, inp.version)))


def plan_mapping_history(inp, ctx, s) -> Plan:
    return Plan(m.ImportMappingHistoryOutput(**import_mappings.history(s, inp.source)))


def plan_mapping_query(inp, ctx, s) -> Plan:
    return Plan(m.ImportMappingQueryOutput(**import_mappings.query(s, inp.kind, inp.active, inp.limit, inp.cursor)))


def plan_mapping_match(inp, ctx, s) -> Plan:
    from bookflow.company import import_kinds
    kind = import_kinds.kind(inp.kind)
    batches.require_read(s, f'{kind.noun} show')
    table, _ = imports.read_table(s, inp)
    return Plan(m.ImportMappingMatchOutput(
        kind=kind.name, fingerprint=import_mappings.fingerprint(table.headers), headers=table.headers,
        candidates=import_mappings.match(s, kind.name, table.headers, inp.source)))


def plan_mapping_deactivate(inp, ctx, s) -> Plan:
    source = import_mappings.plan_deactivate(s, inp.source)
    return Plan(m.ImportMappingOutput(**import_mappings.show(s, inp.source)), {'source': source})


def apply_mapping_deactivate(plan: Plan, ctx, s) -> Applied:
    out = import_mappings.apply_deactivate(s, plan.data['source'])
    touched = [Touched('import_source', out['source']['id'], 'update', None, out['source']['version'],
                       {'active': False}, db='company')]
    return Applied(m.ImportMappingOutput(**out), touched, f"deactivated import source {out['source']['name']!r}")


def plan_import_run(inp, ctx, s) -> Plan:
    run = imports._run_row(s, inp.run)
    return Plan(m.ImportRunOutput(**imports.show(s, ctx, run['id'])), {'input': inp})


def apply_import_run(plan: Plan, ctx, s) -> Applied:
    return _finalized(s, ctx, 'import.run', lambda: imports.run_slice(s, ctx, plan.data['input']),
                      m.ImportRunOutput)


def _finalized(s, ctx, what, work, model) -> Applied:
    """A command whose rows commit one by one through nested commands, as `cutover apply` does: the dispatcher's
    own transaction is closed first, and each nested command writes its own audit event."""
    from bookflow.core.dispatch import _upsert_principals
    with s.commits.operation(batches.OWNER, s.hub, s.company):
        try:
            _upsert_principals(s, ctx)
            s.commits.commit(s.company, batches.OWNER)
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
        out = model(**work())
    return Applied(out, [], what, finalized=True, audited=True)


def plan_import_cancel(inp, ctx, s) -> Plan:
    imports._run_row(s, inp.run)
    return Plan(m.ImportRunOutput(**imports.show(s, ctx, inp.run)), {'input': inp})


def apply_import_cancel(plan: Plan, ctx, s) -> Applied:
    return _finalized(s, ctx, 'import.cancel', lambda: imports.cancel(s, ctx, plan.data['input'].run),
                      m.ImportRunOutput)


def plan_import_reverse(inp, ctx, s) -> Plan:
    if s.dry_run:
        return Plan(m.BatchReverseOutput(**_reverse_out(imports.reverse(s, ctx, inp, dry_run=True))), {'input': inp})
    imports._run_row(s, inp.run)
    return Plan(m.BatchReverseOutput(batch={}, state='live', reversed_now=0, remaining=0, steps=[], blockers=[],
                                     fingerprint=None, reversal_batch=None, event_id=None), {'input': inp})


def _reverse_out(out: dict) -> dict:
    return {k: out.get(k) for k in m.BatchReverseOutput.model_fields}


def apply_import_reverse(plan: Plan, ctx, s) -> Applied:
    return _finalized(s, ctx, 'import.reverse',
                      lambda: _reverse_out(imports.reverse(s, ctx, plan.data['input'], dry_run=False)),
                      m.BatchReverseOutput)


def plan_import_show(inp, ctx, s) -> Plan:
    return Plan(m.ImportRunOutput(**imports.show(s, ctx, inp.run)))


def plan_import_query(inp, ctx, s) -> Plan:
    return Plan(m.ImportQueryOutput(**imports.query(s, ctx, inp)))


def plan_import_rows(inp, ctx, s) -> Plan:
    return Plan(m.ImportRowsOutput(**imports.rows(s, ctx, inp)))


def plan_import_errors(inp, ctx, s) -> Plan:
    return Plan(m.ImportErrorsOutput(**imports.errors(s, ctx, inp.run)))


def plan_batch_show(inp, ctx, s) -> Plan:
    return Plan(m.BatchOutput(batch=batches.show(s, ctx, inp.batch)))


def _batch_reverse(s, ctx, inp, *, dry_run):
    from bookflow.company import batch_delegates
    out = batch_delegates.reverse(s, ctx, inp, dry_run=dry_run)
    if out is None:
        out = batches.reverse(s, ctx, inp.batch, reason=ctx.reason, dry_run=dry_run,
                              limit=inp.limit, fingerprint=inp.fingerprint, require_work=True)
    out['next'] = f'batch reverse {inp.batch}' if out['remaining'] else None
    return _reverse_out(out)


def plan_batch_reverse(inp, ctx, s) -> Plan:
    # Preflight before the dispatcher's write transaction: even a first-time caller's
    # principal row must not be committed when the whole unit is blocked.
    return Plan(m.BatchReverseOutput(**_batch_reverse(s, ctx, inp, dry_run=True)), {'input': inp})


def apply_batch_reverse(plan, ctx, s) -> Applied:
    return _finalized(s, ctx, 'batch.reverse',
                      lambda: _batch_reverse(s, ctx, plan.data['input'], dry_run=False), m.BatchReverseOutput)


def plan_batch_query(inp, ctx, s) -> Plan:
    return Plan(m.BatchQueryOutput(**batches.query(s, ctx, kind=inp.kind, state=inp.state, scope=inp.scope,
                                                   limit=inp.limit, cursor=inp.cursor)))


def _read(name, description, inp, out, planner, *, positional=(), errors=_C):
    return command(name, scope='company', description=description, input_model=inp, output_model=out,
                   required_role='member', capability='import', positional=list(positional),
                   error_codes=list(errors))(planner)


def _write(name, description, inp, out, planner, applier, *, positional=(), errors=_C):
    cmd = command(name, scope='company', description=description, input_model=inp, output_model=out,
                  writes={'company'}, required_role='standard', capability='import', positional=list(positional),
                  error_codes=list(errors))(planner)
    cmd.applier(applier)
    return cmd


import_kinds = _read('import kinds', 'List what can be imported: each kind, the command each row becomes, its '
                     'required fields, default key, references and an example header.', m.ImportKindsInput,
                     m.ImportKindsOutput, plan_import_kinds, errors=())
import_template = _read('import template', 'A header and example row for one import kind, as CSV text, and every '
                        'field it maps (path, type, required, what a reference names). Example: {"kind": "customer"}.',
                        m.ImportTemplateInput, m.ImportTemplateOutput, plan_import_template, positional=('kind',),
                        errors=('E_VALIDATION',))
import_preview = _write('import preview', _PREVIEW, m.ImportPreviewInput, m.ImportRunOutput, plan_import_preview,
                        apply_import_preview, errors=('E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_IO',
                                                      'E_AMOUNT_PRECISION'))
import_run = _write('import run', _RUN, m.ImportRunInput, m.ImportRunOutput, plan_import_run, apply_import_run,
                    positional=('run',))
import_cancel = _write('import cancel', 'Stop an import run. All or nothing undoes what it made; a partial run keeps it '
                       '(`import reverse` undoes it).',
                       m.ImportRunSelector, m.ImportRunOutput, plan_import_cancel, apply_import_cancel,
                       positional=('run',))
import_reverse = _write('import reverse', _REVERSE, m.ImportReverseInput, m.BatchReverseOutput, plan_import_reverse,
                        apply_import_reverse, positional=('run',),
                        errors=('E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_BATCH_BLOCKED', 'E_VERSION_CONFLICT'))
import_show = _read('import show', 'Show an import run: counts, mapping, file digest, who and why, the first refused '
                    'rows and its batch.', m.ImportRunSelector, m.ImportRunOutput, plan_import_show,
                    positional=('run',))
import_query = _read('import query', 'List import runs, newest first, by kind or state, with their counts.',
                     m.ImportQueryInput, m.ImportQueryOutput, plan_import_query)
import_rows = _read('import rows', 'Page through an import run\'s rows by outcome (`--status refused`), with line, '
                    'column, code, reason and the record each made.', m.ImportRowsInput, m.ImportRowsOutput,
                    plan_import_rows, positional=('run',))
import_errors = _read('import errors', 'The refused rows of a run as a CSV file: their own columns plus code, message '
                      'and line, ready to correct and import again as the same source (only those rows import).',
                      m.ImportRunSelector, m.ImportErrorsOutput, plan_import_errors, positional=('run',))
batch_show = _read('batch show', 'Show a batch (an import run\'s writes, a reversal...): its effects, who made it, '
                   'whether it can be reversed now and what blocks it.', m.BatchShowInput, m.BatchOutput,
                   plan_batch_show, positional=('batch',))
batch_query = _read('batch query', 'List batches, newest first, each with `reversible` and its first blocker.',
                    m.BatchQueryInput, m.BatchQueryOutput, plan_batch_query)
import_check = _read('import check', 'Check a file before importing it, running no command: is every required field '
                     'mapped, does each cell fit its field (type, length, pattern), is any key repeated, does every '
                     'reference exist. Returns every problem at once, by column, with row numbers and the cell text, '
                     'and each character text cannot hold (dropped, by position). `import preview` starts with it. '
                     'Takes the same input as `import preview`. Example: {"kind": "customer", "content": "Name\\nAcme\\n"}.',
                     m.ImportCheckInput, m.ImportCheckOutput, plan_import_check,
                     errors=('E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_IO'))
import_decide = _write('import decide', 'Decide the unknown references a previewed run waits on: map text to an '
                       'existing record, create it when the run reaches it, or skip its rows; `remember` keeps the maps '
                       'in the source\'s saved mapping. The answers are the owner\'s: a suggestion is not one, so map '
                       'a value yourself only when the match is plain, and ask about the rest. '
                       'Example: {"run": "01J...", "map": ["Net 30 days=Net 30"], '
                       '"remember": true}.', m.ImportDecideInput, m.ImportRunOutput, plan_import_decide,
                       apply_import_decide, positional=('run',))
import_mapping_save = _write('import-mapping save', 'Save a run\'s mapping (columns, key, formats, constants, value '
                             'maps) under a source name, as a new version; a file with the same headers then maps '
                             'without asking. Example: {"source": "Acme export", "from_run": "01J..."}.',
                             m.ImportMappingSaveInput, m.ImportMappingOutput, plan_mapping_save, apply_mapping_save,
                             positional=('source',), errors=('E_VALIDATION', 'E_RECORD_NOT_FOUND',
                                                             'E_VERSION_CONFLICT'))
import_mapping_show = _read('import-mapping show', 'Show a saved source and its mapping (latest, or a version).',
                            m.ImportMappingShowInput, m.ImportMappingOutput, plan_mapping_show,
                            positional=('source',))
import_mapping_history = _read('import-mapping history', 'Every version of a source\'s mapping, newest first.',
                               m.ImportMappingSelector, m.ImportMappingHistoryOutput, plan_mapping_history,
                               positional=('source',))
import_mapping_query = _read('import-mapping query', 'List saved import sources by name.', m.ImportMappingQueryInput,
                             m.ImportMappingQueryOutput, plan_mapping_query, errors=())
import_mapping_match = _read('import-mapping match', 'How a file\'s headers match each saved mapping of its kind: '
                             'exact, probable (with the new and missing columns) or none.',
                             m.ImportMappingMatchInput, m.ImportMappingMatchOutput, plan_mapping_match,
                             errors=('E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_IO'))
import_mapping_deactivate = _write('import-mapping deactivate', 'Stop a source\'s mapping matching files; its '
                                   'versions stay readable.', m.ImportMappingSelector, m.ImportMappingOutput,
                                   plan_mapping_deactivate, apply_mapping_deactivate, positional=('source',),
                                   errors=('E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'))


batch_reverse = command(
    'batch reverse', scope='company', writes={'company'}, capability='import', required_role='member',
    input_model=m.BatchReverseInput, output_model=m.BatchReverseOutput, positional=['batch'],
    error_codes=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_BATCH_BLOCKED', 'E_VERSION_CONFLICT'],
    description=(
        'Confirm-first: reverse the entire batch, newest effects first, using your own grants for every inverse. '
        'Run --dry-run and check its exact list with your principal unless this reversal was plainly named. '
        'Every blocker is named and a blocked plan changes nothing. Later edits, dependent records, closed periods, '
        'reconciled payments and pending or printed checks can block. Sent mail, printed checks and exported files '
        'cannot be recalled. History stays. Returns a resumable slice; repeat `next` until reversed. '
        'A batch with nothing reversible says so. Example: {"batch": "01J..."}.'))(plan_batch_reverse)
batch_reverse.applier(apply_batch_reverse)
