"""Importing records in the browser (R107 increment 3, design/bulk-import.md §9 Browser).

One page of steps, each reachable again: **File** (choose, drop or paste; a pasted table shows at once), **Check**
(the columns against their fields with samples beside, every problem at once grouped by column with row numbers and
the cell text, and what must be decided: formats, a saved mapping, each unknown reference), **Preview** (counts per
outcome and a sample: the one confirmation of a lasting change), **Run** (progress by slice; stop and resume, and a
reload resumes), **Done** (counts, the refused rows to download, "Save this mapping as", Undo as one unit).

The page runs the registry's own commands (`import check`, `preview`, `decide`, `run`, `cancel`, `reverse`,
`errors`, `template`, `import-mapping save`), so the browser does what the CLI and MCP do and nothing else. A chosen
file, or a pasted table, is kept as an attachment on the company first (`attachment add company_info`, as the CLI
and agents attach a file), and the import reads it by id through the same reader (text in any encoding, or .xlsx).
Every form posts through htmx (cookie-authenticated writes need the header only htmx sends), and a refusal answers
200 with the error in place, since htmx does not swap a 4xx answer. Downloads (refused rows, templates) are reads,
served as plain links.
"""
from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import Request
from starlette.exceptions import HTTPException
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError

MAX_BYTES = 16_000_000  # the commands' own `content` limit; the .xlsx reader bounds what a workbook unpacks to
MAX_POST = MAX_BYTES + 2_000_000  # the file plus the page's own fields (a column's choice, each answer)
MAX_FIELDS = 4000  # three or four fields a column of the file, and the answers: about a thousand columns
SLICE_ROWS, SLICE_SECONDS = 100, 4  # one browser request runs about this much, then the page asks for the next
REVERSE_SLICE = 200
_PASS = ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER')
STEPS = (('file', 'File'), ('check', 'Check'), ('preview', 'Preview'), ('run', 'Run'), ('done', 'Done'))
OUTCOMES = {
    'create': 'Will be created', 'update': 'Will be updated', 'created': 'Created', 'updated': 'Updated',
    'unchanged': 'Already the same', 'exists': 'Already there', 'already_in': 'Imported before',
    'differs': 'Differs from the record', 'skipped': 'Skipped', 'refused': 'Refused', 'waiting': 'Waiting on a decision',
    'not_run': 'Not run yet', 'missing': 'No record to update', 'rolled_back': 'Undone',
}
DONE_STATES = ('done', 'cancelled', 'rolled_back', 'failed')
_FILE_TYPES = '.csv,.tsv,.txt,.xlsx,text/csv,text/tab-separated-values,text/plain'


def field_label(path: str) -> str:
    """`billing_address.city` -> "Billing address: city"; `terms_id` -> "Terms"."""
    parts = [p[:-3] if p.endswith('_id') else p for p in path.replace('[]', '').split('.')]
    words = [p.replace('_', ' ') for p in parts]
    text = ': '.join(words)
    return text[:1].upper() + text[1:]


def kind_label(name: str) -> str:
    return {'customer': 'Customers and jobs', 'item': 'Items', 'account': 'Accounts', 'class': 'Classes',
            'sales-rep': 'Sales reps', 'other-name': 'Other names'}.get(
        name, name.replace('-', ' ').capitalize() + ('es' if name.endswith('s') else 's'))


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _upload_name(name: str | None, default: str) -> str:
    name = re.sub(r'[\x00-\x1f\x7f/\\]', '_', (name or '').rsplit('/', 1)[-1].rsplit('\\', 1)[-1]).strip(' .')
    return (name or default)[:200]


def mount(app, *, render, run, credential, page_error, host, make_context, role_allows):
    from bookflow.core import registry

    def company(request, company_id):
        return run(request, 'company show', {}, company_id)

    def reason(form, default):
        text = (form.get('ctx:reason') or '').strip()
        return {'X-Bookflow-Reason': text or default}

    def attach(request, shown, kind: str, name: str, media_type: str, data: bytes) -> dict:
        """Keep the file as the import's own file, through `attachment add import_file` (a transfer, as the CLI
        does): never one of the company's attachments, read only by those who may read the kind's records, and
        replaced by this person's next file of the kind unless its import landed records."""
        from bookflow.adapters.http.published_transfer import PublishedTransfer
        from bookflow.core.dispatch import guard
        cmd = registry.get('attachment add')
        cred = credential(request)
        ctx = make_context(request, cred).model_copy(update={'reason': f'File to import ({kind})',
                                                             'client_name': 'bookflow-workbench',
                                                             'idempotency_key': None})
        raw = {'record_type': 'import_file', 'record_id': shown['company_id'], 'original_filename': name,
               'media_type': media_type, 'caption': kind}
        transfer = guard(lambda: PublishedTransfer(host, cmd, raw, ctx, cred.user_id, cred.login,
                                                   shown['company_id'], 'option', False, credential=cred),
                         cred.hub_admin)
        try:
            for start in range(0, len(data), 1 << 20):
                transfer.body.write(data[start:start + (1 << 20)])
            out = guard(transfer.finish_input, cred.hub_admin)
        finally:
            transfer.close()
        link = out.get('link') or {}
        return {'attachment': out['attachment']['id'], 'link': link.get('id'), 'link_version': link.get('version')}

    # ------------------------------------------------------------ what each step shows

    def fields_of(request, company_id, kind):
        template = run(request, 'import template', {'kind': kind}, company_id)
        return [{'path': f['path'], 'label': field_label(f['path']), 'required': f.get('required')}
                for f in template['fields']]

    def page(request, company_id, *, step='file', status_code=200, **ctx):
        shown = company(request, company_id)
        kinds = run(request, 'import kinds', {}, company_id)['kinds']
        kind = ctx.pop('kind', None) or request.query_params.get('kind') or 'customer'
        if kind not in {k['kind'] for k in kinds}:
            kind = 'customer'
        ctx.setdefault('values', {})
        if step == 'check' and ctx.get('check') and 'fields' not in ctx:
            ctx['fields'] = fields_of(request, company_id, kind)
            ctx['problem_groups'] = problem_groups(ctx['check']['problems'])
        response = render('import.html', request, status_code=status_code, company=shown,
                          company_id=shown['company_id'], kinds=[dict(k, label=kind_label(k['kind'])) for k in kinds],
                          kind=kind, kind_label=kind_label(kind), step=step, steps=STEPS, outcomes=OUTCOMES,
                          file_types=_FILE_TYPES, max_mb=MAX_BYTES // 1_000_000, field_label=field_label,
                          can_write=role_allows('import preview', shown, credential(request)),
                          done_states=DONE_STATES, counts_view=counts_view, rows_text=rows_text,
                          due_days=due_days, made_count=made_count, **ctx)
        run_id = (ctx.get('result') or {}).get('run', {}).get('id') if isinstance(
            (ctx.get('result') or {}).get('run'), dict) else None
        if run_id:  # a reload comes back to this run (and resumes it)
            response.headers['HX-Push-Url'] = f"/c/{quote(shown['company_id'], safe='')}/import?run={run_id}"
        return response

    def run_page(request, company_id, run_id, **ctx):
        """A stored run, at the step its state belongs to: a reload lands here and carries on."""
        result = run(request, 'import show', {'run': run_id}, company_id)
        state = result['run']['state']
        step = ('preview' if state in ('previewed', 'waiting') else 'run' if state in ('running', 'stopped')
                else 'done')
        batch = result['run'].get('batch') or {}
        if batch.get('state') in ('reversed', 'reversed_partly'):
            step = 'done'  # undone: nothing to resume
        ctx.setdefault('auto', step == 'run' and state == 'running' and not ctx.get('paused'))
        if batch.get('state') == 'reversing' and 'undo' not in ctx:
            ctx['undo'] = {'state': 'reversing', 'auto': True}
        ctx.setdefault('sample', result.get('sample') or [])  # a reload of the preview keeps its sample
        return page(request, company_id, step=step, kind=result['run']['kind'], result=result, **ctx)

    @app.get('/c/{company_id}/import')
    def import_page(request: Request, company_id: str):
        try:
            if request.query_params.get('run'):
                return run_page(request, company_id, request.query_params['run'])
            return page(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    @app.get('/c/{company_id}/import/{run_id}/refused.csv')
    def refused_rows(request: Request, company_id: str, run_id: str):
        try:
            out = run(request, 'import errors', {'run': run_id}, company_id)
        except BookflowError as e:
            return page_error(request, e)
        return _csv(out['content'], out['file_name'])

    @app.get('/c/{company_id}/import/template/{kind}.csv')
    def template_file(request: Request, company_id: str, kind: str):
        try:
            out = run(request, 'import template', {'kind': kind}, company_id)
        except BookflowError as e:
            return page_error(request, e)
        return _csv(out['content'], f'{kind} import template.csv')

    # ------------------------------------------------------------ the steps' posts

    def file_input(request, shown, form, upload):
        """The file to read: a new upload or paste (kept as the import's file first), else the one already chosen."""
        kind = form.get('kind') or 'customer'
        if upload is not None:
            name = _upload_name(getattr(upload, 'filename', None), 'file to import.csv')
            data = upload.file.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise BookflowError('E_VALIDATION', message=(
                    f'The file is over {MAX_BYTES // 1_000_000} MB. Split it into smaller files and import each.'),
                    details={'fields': [{'field': 'file', 'problem': 'too large'}]})
            if not data:
                raise BookflowError('E_VALIDATION', message='The chosen file is empty.',
                                    details={'fields': [{'field': 'file', 'problem': 'empty'}]})
            media = getattr(upload, 'content_type', None) or 'application/octet-stream'
            media = media if re.fullmatch(r'[\w.+-]+/[\w.+-]+', media) else 'application/octet-stream'
            return {**attach(request, shown, kind, name, media, data), 'file_name': name, 'fresh': True}
        pasted = (form.get('paste') or '').strip('\r\n')
        if pasted.strip():
            data = pasted.encode('utf-8')
            if len(data) > MAX_BYTES:
                raise BookflowError('E_VALIDATION', message='The pasted table is too large; save it as a file.',
                                    details={'fields': [{'field': 'paste', 'problem': 'too large'}]})
            return {**attach(request, shown, kind, 'pasted table.tsv', 'text/tab-separated-values', data),
                    'file_name': 'pasted table', 'fresh': True}
        if form.get('attachment'):
            return {'attachment': form['attachment'], 'file_name': form.get('file_name') or ''}
        raise BookflowError('E_VALIDATION', message='Choose a file, or paste a table, first.',
                            details={'fields': [{'field': 'file', 'problem': 'choose a file or paste a table'}]})

    def mapping_input(form) -> dict:
        """The check's input beyond the file: columns changed by hand, formats, a saved mapping, the source. They
        carry over to a corrected file (a column it lacks is dropped from them)."""
        raw: dict = {}
        columns = {}
        by_hand = 'mapping' in form and not form['mapping']  # "Map the columns below by hand": every column, as shown
        for key, header in form.items():
            if key.startswith('colname:'):
                n = key.split(':', 1)[1]
                now, was = form.get(f'col:{n}', ''), form.get(f'was:{n}', '')
                if by_hand or now != was or form.get(f'given:{n}'):
                    columns[header] = now or None  # an empty choice is a deliberate "not imported", and kept
        if columns:
            raw['columns'] = columns
        for key in ('decimal', 'date_order', 'mapping'):
            if form.get(key):
                raw[key] = form[key]
        if (form.get('source') or '').strip() and not raw.get('mapping'):
            raw['source'] = form['source'].strip()
        return raw

    def check(request, company_id, shown, form, upload, *, error=None):
        kind = form.get('kind') or 'customer'
        chosen = file_input(request, shown, form, upload)
        raw = {'kind': kind, 'attachment': chosen['attachment'], **mapping_input(form)}
        try:
            while True:
                try:
                    out = run(request, 'import check', raw, company_id)
                    break
                except BookflowError as e:
                    header = (e.details or {}).get('header')
                    if (e.details or {}).get('field') != 'columns' or header not in raw.get('columns', {}):
                        raise
                    raw['columns'].pop(header)  # a corrected file without a column mapped by hand
                    if not raw['columns']:
                        raw.pop('columns')
        except BookflowError:
            if chosen.get('fresh') and chosen.get('link'):
                prune(request, company_id, chosen)  # a file the check refuses (not a table) is not kept
            raise
        return page(request, company_id, step='check', kind=kind, check=out, chosen=chosen, raw=raw,
                    values={k: v for k, v in form.items() if k in ('source', 'ctx:reason')}, error=error,
                    answers=answers_of(form), error_ref=((error or {}).get('details') or {}).get('ref'))

    def prune(request, company_id, chosen):
        try:
            run(request, 'attachment unlink', {'link': chosen['link'], 'expected_version': chosen['link_version']},
                company_id, headers={'X-Bookflow-Reason': 'The file to import was refused'})
        except BookflowError:
            pass  # the refusal itself is what the person needs to see

    def answers_of(form) -> dict:
        """The answers posted, by (field, file text), so a page shown again keeps every one."""
        out = {}
        for key, value in form.items():
            if key.startswith('ref:'):
                n = key.split(':', 1)[1]
                out[(form.get(f'reffield:{n}') or '', form.get(f'refvalue:{n}') or '')] = {
                    'action': value, 'target': form.get(f'reftarget:{n}') or '', 'days': form.get(f'refdays:{n}')}
        return out

    def decisions_of(form) -> dict:
        """The unknown references answered on the page, as `import decide`'s answers: each names its field and the
        file text exactly, so a text holding "=" or unknown in two columns is decided like any other."""
        answers = []
        for key, value in form.items():
            if not key.startswith('ref:') or value not in ('map', 'create', 'skip'):
                continue
            n = key.split(':', 1)[1]
            answer = {'field': form.get(f'reffield:{n}') or '', 'value': form.get(f'refvalue:{n}') or '',
                      'action': value}
            if value == 'map':
                answer['target'] = (form.get(f'reftarget:{n}') or '').strip() or None
            elif value == 'create' and form.get(f'reftarget_kind:{n}') == 'term':
                answer['input'] = {'kind': 'standard',
                                   'due_days': _int(form.get(f'refdays:{n}'), due_days(answer['value']))}
            answers.append({k: v for k, v in answer.items() if v is not None})
        out: dict = {'answers': answers} if answers else {}
        if answers and form.get('remember') == 'on':
            out['remember'] = True
        return out

    def preview(request, company_id, shown, form):
        kind = form.get('kind') or 'customer'
        chosen = file_input(request, shown, form, None)
        raw = {'kind': kind, 'attachment': chosen['attachment'], 'file_name': chosen['file_name'][:255] or None,
               **mapping_input(form)}
        if form.get('partial') == 'on':
            raw['partial'] = True
        raw = {k: v for k, v in raw.items() if v is not None}
        out = run(request, 'import preview', raw, company_id, headers=reason(form, f'Import {kind_label(kind).lower()}'))
        sample = out.get('sample') or []
        answers = decisions_of(form)
        if out['run']['state'] == 'waiting' and answers:
            try:
                out = run(request, 'import decide', {'run': out['run']['id'], **answers}, company_id,
                          headers=reason(form, 'Decide unknown references'))
            except BookflowError:
                # The run was made only to take these answers: one is wrong, so it goes, and the Check step comes
                # back with every answer as given and the refusal beside the one it names.
                run(request, 'import cancel', {'run': out['run']['id']}, company_id,
                    headers=reason(form, 'An answer was refused'))
                raise
        return page(request, company_id, step='preview', kind=kind, result=out, sample=sample)

    def act(request, company_id, form, upload):
        shown = company(request, company_id)
        action = form.get('action') or 'check'
        run_id = form.get('run') or ''
        if action == 'check':
            return check(request, company_id, shown, form, upload)
        if action in ('preview', 'preview-partial'):
            if action == 'preview-partial':
                form['partial'] = 'on'  # Kabe's rule is all or nothing; leaving the bad rows out is asked for
            return preview(request, company_id, shown, form)
        if action == 'decide':
            answers = decisions_of(form)
            if not answers:
                raise BookflowError('E_VALIDATION', message='Answer each unknown value: map, create or skip it.',
                                    details={'fields': [{'field': 'decisions', 'problem': 'nothing decided'}]})
            out = run(request, 'import decide', {'run': run_id, **answers}, company_id,
                      headers=reason(form, 'Decide unknown references'))
            return page(request, company_id, step='preview', kind=out['run']['kind'], result=out,
                        sample=out.get('sample') or [])
        if action in ('start', 'continue'):
            out = run(request, 'import run', {'run': run_id, 'limit': SLICE_ROWS, 'seconds': SLICE_SECONDS},
                      company_id, headers=reason(form, 'Import'))
            state = out['run']['state']
            step = 'run' if state in ('running', 'stopped') else 'done'
            return page(request, company_id, step=step, kind=out['run']['kind'], result=out,
                        auto=state == 'running')
        if action == 'pause':
            return run_page(request, company_id, run_id, paused=True, auto=False)
        if action == 'cancel':
            before = run(request, 'import show', {'run': run_id}, company_id)
            made = made_count(before['counts'])
            run(request, 'import cancel', {'run': run_id}, company_id, headers=reason(form, 'Cancel the import'))
            noun = kind_label(before['run']['kind']).lower() if made != 1 else 'record'
            text = ('Import cancelled; nothing had been made.' if not made else
                    f'Import cancelled. The {made} {noun} it had made were undone: all or nothing lands none.'
                    if before['run']['atomic'] else
                    f'Import cancelled. The {made} {noun} already made stay until you undo them below.')
            return run_page(request, company_id, run_id, message=text)
        if action == 'save-mapping':
            name = (form.get('save_as') or '').strip()
            if not name:
                raise BookflowError('E_VALIDATION', message='Name the mapping (where the file comes from).',
                                    details={'fields': [{'field': 'save_as', 'problem': 'required'}]})
            saved = run(request, 'import-mapping save', {'source': name, 'from_run': run_id}, company_id,
                        headers=reason(form, 'Save the import mapping'))
            version = saved['mapping'].get('version')
            text = (f"Saved as {saved['source']['name']!r} (version {version}). A file with the same columns now "
                    'maps itself.' if saved['saved'] else
                    f"{saved['source']['name']!r} already holds this mapping (version {version}).")
            return run_page(request, company_id, run_id, message=text)
        if action == 'undo-plan':
            out = run(request, 'import reverse', {'run': run_id, 'limit': REVERSE_SLICE}, company_id,
                      headers=reason(form, 'Undo the import'), dry_run=True)
            return run_page(request, company_id, run_id, undo={'state': 'planned', 'plan': out}, auto=False)
        if action == 'undo':
            raw = {'run': run_id, 'limit': REVERSE_SLICE}
            if form.get('fingerprint'):
                raw['fingerprint'] = form['fingerprint']
            out = run(request, 'import reverse', raw, company_id, headers=reason(form, 'Undo the import'))
            return run_page(request, company_id, run_id, auto=False, undo={
                'state': out['state'], 'auto': out['state'] == 'reversing', 'result': out})
        raise BookflowError('E_VALIDATION', message='Unknown step.',
                            details={'fields': [{'field': 'action', 'problem': 'unknown'}]})

    def submit(request, company_id, form, upload):
        try:
            return act(request, company_id, form, upload)
        except BookflowError as e:
            if e.code in _PASS:
                raise
            return failed(request, company_id, form, e)

    def failed(request, company_id, form, e):
        """The refusal in place, on the step it came from (200: htmx swaps it)."""
        error = e.to_dict()
        action = form.get('action') or 'check'
        run_id = form.get('run')
        if action == 'undo-plan' or action == 'undo':
            blockers = (e.details or {}).get('blockers') or []
            return run_page(request, company_id, run_id, auto=False, undo={'state': 'blocked', 'error': error,
                                                                           'blockers': blockers})
        if run_id:
            try:
                return run_page(request, company_id, run_id, error=error)
            except BookflowError:
                pass
        if form.get('attachment') and action in ('check', 'preview', 'preview-partial'):
            try:  # the file stays chosen: show its check again with the refusal above it
                shown = company(request, company_id)
                return check(request, company_id, shown, form, None, error=error)
            except BookflowError:
                pass
        kind = form.get('kind') or 'customer'
        return page(request, company_id, step='file', kind=kind, error=error,
                    values={k: v for k, v in form.items() if k in ('source', 'ctx:reason', 'paste')})

    def refused_early(request, company_id, message, field):
        error = BookflowError('E_VALIDATION', message=message, details={'fields': [{'field': field,
                                                                                    'problem': message}]})
        return page(request, company_id, step='file', kind=request.query_params.get('kind'), error=error.to_dict())

    @app.post('/c/{company_id}/import')
    async def import_post(request: Request, company_id: str):
        too_large = (f'The file is over {MAX_BYTES // 1_000_000} MB. Split it into smaller files and import each.')
        if _int(request.headers.get('content-length'), 0) > MAX_POST:
            return await run_in_threadpool(refused_early, request, company_id, too_large, 'file')
        body, size = [], 0
        async for chunk in request.stream():  # the body is read up to the limit and no further
            size += len(chunk)
            if size > MAX_POST:
                return await run_in_threadpool(refused_early, request, company_id, too_large, 'file')
            body.append(chunk)
        request._body = b''.join(body)  # Starlette parses the form from this, already read
        try:
            data = await request.form(max_files=1, max_fields=MAX_FIELDS, max_part_size=MAX_BYTES + 1)
        except HTTPException as e:
            if e.status_code != 400:
                raise
            return await run_in_threadpool(refused_early, request, company_id, (
                f'The form was refused ({e.detail}): the file is too wide to map on this page. Keep only the columns '
                'to import, or use `bookflow import check` with `columns`.'), 'file')
        try:
            form, upload = {}, None
            for key, value in data.multi_items():
                if isinstance(value, str):
                    form[key] = value
                elif key == 'file' and getattr(value, 'filename', None):
                    upload = value
            try:
                return await run_in_threadpool(submit, request, company_id, form, upload)
            except BookflowError as e:
                return page_error(request, e)
        finally:
            await data.close()


def _csv(content: str, file_name: str) -> Response:
    """A CSV download with a byte order mark, so a spreadsheet reads its accents as written."""
    fallback = re.sub(r'[^A-Za-z0-9._ -]', '_', file_name).strip(' .') or 'import.csv'
    return Response(('﻿' + content).encode('utf-8'), media_type='text/csv; charset=utf-8', headers={
        'Content-Disposition': f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(file_name, safe="")}',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


def counts_view(counts: dict, answered: bool = False) -> list[dict]:
    """Counts in outcome order; once every unknown reference is answered, rows that waited on one run with it."""
    order = list(OUTCOMES)
    labels = dict(OUTCOMES, waiting='Will run with your answers') if answered else OUTCOMES
    return [{'key': k, 'label': labels.get(k, k.replace('_', ' ')), 'value': v}
            for k, v in sorted(counts.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99) if v]


def problem_groups(problems: list[dict]) -> list[dict]:
    """The check's problems by column, in the file's column order (the mapping's own problems first)."""
    out: list[dict] = []
    for g in problems:
        column = g.get('column') or ''
        if not out or out[-1]['column'] != column:
            out.append({'column': column, 'entries': []})
        found = re.match(r'No column maps to (\S+), which', g.get('message') or '')
        if g.get('code') == 'unmapped_required' and found:  # the page's own words: the picker is right below
            g = dict(g, message=f'No column is chosen for {field_label(found.group(1))}, which every record needs. '
                                'Choose it under Columns below.')
        out[-1]['entries'].append(g)
    for group in out:  # what a collapsed group says: its problems (every cell) and the rows they are in
        group['count'] = sum(len(e.get('rows') or ()) or 1 for e in group['entries'])
        group['rows'] = len({r for e in group['entries'] for r in e.get('rows') or ()})
    return out


def rows_text(rows: list[int], shown: int = 12) -> str:
    text = ', '.join(str(r) for r in rows[:shown])
    return text + (f' and {len(rows) - shown} more' if len(rows) > shown else '')


def made_count(counts: dict) -> int:
    """Records a run made or changed so far: what Undo would reverse."""
    return counts.get('created', 0) + counts.get('updated', 0)


def due_days(text: str) -> int:
    """A new term's days until due, from its own words: "Net45" 45, "Due in 60" 60, "Due on receipt" 0; else 30."""
    found = re.search(r'\d+', text or '')
    if found:
        return min(int(found.group()), 999)
    if re.search(r'receipt|\bcod\b|cash on delivery|immediate|on delivery|upon', (text or '').casefold()):
        return 0
    return 30
