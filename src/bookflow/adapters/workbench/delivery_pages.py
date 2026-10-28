"""Document delivery in the browser (R112 increment 1): the Email panel, the Outbox, a message and its history, and
the email settings with their templates.

``/delivery/compose`` is the Email panel opened from an invoice, sales receipt, estimate, statement row or overdue
notice: To, Cc, Subject and body filled from the kind's template (a picker reloads them), the PDF to preview, and
Save draft, Send and Export .eml. A person's Send is release and go in one tap. ``/delivery`` is the Outbox with its
tabs and checkboxes; ``/delivery/{message}`` shows one message, its frozen files and its history.
``/delivery/settings`` holds the reply-to and copy-to addresses, the templates, and the connections (none: nothing
leaves this machine). Every form posts through htmx, Export too: the answering page carries the file as a link that
the page downloads at once (cookie-authenticated writes need the header only htmx sends), or says why a message was
held instead.
"""
from __future__ import annotations

import base64
from urllib.parse import quote, urlencode

from fastapi import Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError

_PASS = ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER')
KINDS = ('invoice', 'sales-receipt', 'estimate', 'statement', 'notice')
KIND_LABEL = {'invoice': 'Invoice', 'sales-receipt': 'Sales receipt', 'estimate': 'Estimate',
              'statement': 'Statement', 'notice': 'Overdue notice'}
TABS = (('waiting', 'Waiting for your go'), ('ready', 'Ready'), ('sent', 'Sent'), ('attention', 'Needs attention'),
        ('draft', 'Drafts'), ('cancelled', 'Cancelled'), ('all', 'All'))
CONNECTION = ('No sending connection is set up. Nothing leaves this machine: messages wait here. Export one to '
              'send it from your own mail program, then mark it sent.')
COMPOSE_NOTE = ('No sending connection is set up. This will wait in the outbox. Export it to send it yourself.')


def _connection_note(name):
    return CONNECTION if name == 'none' else (f'Messages with a go are sent through the connection {name}; one '
                                              'waiting for a go stays here until a person approves it.')


def compose_url(company_id: str, kind: str, **identity) -> str:
    return f"/c/{quote(str(company_id), safe='')}/delivery/compose?" + urlencode(
        {'kind': kind, **{k: v for k, v in identity.items() if v}})


def _addresses(text):
    return [part.strip() for part in (text or '').replace(';', ',').split(',') if part.strip()]


def _identity(source):
    return {k: source.get(k) for k in ('kind', 'document', 'customer', 'date_from', 'date_to') if source.get(k)}


def _exported(out):
    """What an export answers in the page: the warnings (held messages say why) and the file as a data link, or no
    file when every message was held."""
    file = None
    if out.get('content_base64'):
        file = {'filename': out['filename'], 'media_type': out['media_type'],
                'href': f"data:{out['media_type']};base64,{out['content_base64']}"}
    return {'result': ' '.join(out.get('warnings') or []) or 'Exported.', 'export': file}


def mount(app, *, render, run, credential, page_error, role_allows):
    def headers(form, default=None):
        reason = (form.get('ctx:reason') or '').strip() or default
        return {'X-Bookflow-Reason': reason} if reason else None

    def company(request, company_id):
        return run(request, 'company show', {}, company_id)

    def allowed(request, shown, name):
        return role_allows(name, shown, credential(request))

    def failed(request, e, page, *args, **kw):
        if e.code in _PASS:
            raise e
        return page(request, *args, error=e.to_dict(), status_code=200 if request.headers.get('HX-Request') else 400,
                    **kw)

    # ------------------------------------------------------------ the Email panel

    def compose_page(request, company_id, source, *, values=None, error=None, status_code=200, result=None):
        shown = company(request, company_id)
        kind = source.get('kind')
        if kind not in KINDS:
            raise BookflowError('E_VALIDATION', message='Choose what to email.',
                                details={'fields': [{'field': 'kind', 'problem': 'one of ' + ', '.join(KINDS)}]})
        identity = _identity(source)
        templates = run(request, 'delivery-template query', {'kind': kind}, company_id)['rows']
        preview_input = dict(identity, **({'template': source['template']} if source.get('template') else {}))
        preview = run(request, 'delivery-template preview', preview_input, company_id)
        existing = None
        source_id = preview['source_id']
        if kind != 'statement':
            rows = run(request, 'delivery query', {'status': 'all', 'kind': kind, 'source': source_id, 'limit': 5},
                       company_id)['rows']
            existing = next((r for r in rows if r['status'] != 'cancelled'), None)
        customer_id = preview['customer_id']
        recipients = {'to': ', '.join(preview.get('to') or []), 'cc': ', '.join(preview.get('cc') or [])}
        values = values or {}
        from bookflow.adapters.workbench.document_print import document_url, statement_url
        if kind == 'statement':
            print_url = statement_url(company_id, preview['source_id'], source['date_from'], source['date_to'])
        else:
            print_url = document_url(company_id, kind, preview['source_id'])
        return render('delivery_compose.html', request, status_code=status_code, company=shown,
                      company_id=shown['company_id'], kind=kind, kind_label=KIND_LABEL[kind], identity=identity,
                      templates=templates, preview=preview, existing=existing, values=values, error=error,
                      result=result, recipients=recipients, print_url=print_url, note=COMPOSE_NOTE,
                      customer_id=customer_id, source_id=source_id,
                      can_write=allowed(request, shown, 'delivery draft'))

    @app.get('/c/{company_id}/delivery/compose')
    def delivery_compose(request: Request, company_id: str):
        try:
            return compose_page(request, company_id, dict(request.query_params))
        except BookflowError as e:
            return page_error(request, e)

    def compose_submit(request, company_id, form):
        action = form.get('action') or 'save'
        identity = _identity(form)
        raw = dict(identity, to=_addresses(form.get('to')), cc=_addresses(form.get('cc')))
        if form.get('again') == 'on':
            raw['again'] = True
        for key in ('subject', 'body', 'template'):
            if (form.get(key) or '').strip():
                raw[key] = form[key].replace('\r\n', '\n') if key == 'body' else form[key].strip()
        try:
            if action == 'refresh' and form.get('existing'):
                number = form['existing']
                run(request, 'delivery update', {'message': number, 'refresh': True}, company_id,
                    headers=headers(form, 'Attached the document as it is now'))
                return message_page(request, company_id, number, result='Attached the document as it is now.')
            draft = run(request, 'delivery draft', raw, company_id, headers=headers(form, 'Email to customer'))
            number = draft['number']
            if draft.get('existing'):
                changes = {k: raw[k] for k in ('to', 'cc', 'subject', 'body') if k in raw}
                current = {'to': draft['to'], 'cc': draft['cc'], 'subject': draft['subject'], 'body': draft['body']}
                changes = {k: v for k, v in changes.items() if current.get(k) != v}
                if changes and draft['status'] in ('draft', 'held'):
                    run(request, 'delivery update', {'message': number, **changes}, company_id,
                        headers=headers(form, 'Edited the email'))
            if action == 'export':
                done = _exported(run(request, 'delivery export', {'messages': [number]}, company_id,
                                     headers=headers(form, 'Exported to send by hand')))
                return message_page(request, company_id, number, **done)
            result = f'Message {number} saved as a draft.'
            if action == 'send':
                sent = run(request, 'delivery send', {'messages': [number]}, company_id,
                           headers=headers(form, 'Send the email'))
                message = sent['messages'][0]
                result = f"Message {number}: {message['status_label']}. " + ' '.join(
                    w for w in sent.get('warnings') or [] if not w.startswith('No sending connection'))
        except BookflowError as e:
            return failed(request, e, compose_page, company_id, dict(form), values=dict(form))
        return message_page(request, company_id, number, result=result)

    @app.post('/c/{company_id}/delivery/compose')
    async def delivery_compose_post(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(compose_submit, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)

    # ------------------------------------------------------------ the Outbox

    def outbox_page(request, company_id, *, error=None, status_code=200, result=None, export=None):
        shown = company(request, company_id)
        tab = request.query_params.get('status') or 'live'
        query = {'status': tab if tab in dict(TABS) or tab == 'live' else 'live', 'limit': 100}
        for key in ('kind', 'customer', 'batch'):
            if request.query_params.get(key):
                query[key] = request.query_params[key]
        listing = run(request, 'delivery query', query, company_id)
        batches = run(request, 'delivery batch-query', {'state': 'unfinished', 'limit': 20}, company_id)['rows']
        states = {}
        for row in listing['rows']:
            if row['kind'] == 'invoice' and row['source_ids']:
                try:
                    invoice = run(request, 'invoice show', {'invoice': row['source_ids'][0]}, company_id)
                    states[row['id']] = (invoice.get('settlement_current') or {}).get('status') or invoice['status']
                except BookflowError:
                    states[row['id']] = None
        return render('delivery_outbox.html', request, status_code=status_code, company=shown,
                      company_id=shown['company_id'], listing=listing, tabs=TABS, tab=query['status'],
                      filters=query, kinds=KINDS, kind_label=KIND_LABEL, states=states, error=error, result=result,
                      batches=batches,
                      export=export,
                      connection=_connection_note(listing.get('connection', 'none')),
                      can_write=allowed(request, shown, 'delivery send'),
                      can_mark=credential(request) is not None)

    @app.get('/c/{company_id}/delivery')
    def delivery_outbox(request: Request, company_id: str):
        try:
            return outbox_page(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    def act(request, company_id, form, numbers):
        action = form.get('action') or ''
        if not numbers:
            raise BookflowError('E_VALIDATION', message='Tick the messages first.',
                                details={'fields': [{'field': 'messages', 'problem': 'tick at least one message'}]})
        if action == 'export':
            return _exported(run(request, 'delivery export', {'messages': numbers}, company_id,
                                 headers=headers(form, 'Exported to send by hand')))
        if action == 'approve':  # a person's go (R187: delivery.send)
            out = run(request, 'delivery approve', {'messages': numbers}, company_id, headers=headers(form, 'Go'))
            return f"Approved {', '.join(m['number'] for m in out['messages']) or 'nothing new'}. " + ' '.join(
                w for w in out.get('warnings') or [] if not w.startswith('No sending connection'))
        if action == 'send':
            out = run(request, 'delivery send', {'messages': numbers}, company_id, headers=headers(form, 'Send'))
            return f"Released {', '.join(m['number'] for m in out['messages'])}. " + ' '.join(
                w for w in out.get('warnings') or [] if not w.startswith('No sending connection'))
        done = []
        for number in numbers:
            if action == 'mark-sent':
                raw = {'message': number, 'method': form.get('method') or 'mail-program'}
                if (form.get('sent_at') or '').strip():
                    raw['sent_at'] = form['sent_at'].strip()
                out = run(request, 'delivery mark-sent', raw, company_id, headers=headers(form, 'Sent by hand'))
            elif action == 'cancel':
                out = run(request, 'delivery cancel', {'message': number}, company_id, headers=headers(form))
            elif action == 'retry':
                out = run(request, 'delivery retry', {'message': number}, company_id,
                          headers=headers(form, 'Not in the sent mail; try again'))
            else:
                raise BookflowError('E_VALIDATION', message='Choose an action.',
                                    details={'fields': [{'field': 'action', 'problem': 'unknown action'}]})
            done.append(f"{out['number']}: {out['status_label']}")
        return '; '.join(done) + '.'

    def outbox_submit(request, company_id, form, numbers):
        try:
            result = act(request, company_id, form, numbers)
        except BookflowError as e:
            return failed(request, e, outbox_page, company_id)
        if isinstance(result, dict):
            return outbox_page(request, company_id, **result)
        return outbox_page(request, company_id, result=result)

    @app.post('/c/{company_id}/delivery')
    async def delivery_outbox_post(request: Request, company_id: str):
        data = await request.form()
        form, numbers = dict(data), list(data.getlist('message'))
        try:
            return await run_in_threadpool(outbox_submit, request, company_id, form, numbers)
        except BookflowError as e:
            return page_error(request, e)

    # ------------------------------------------------------------ Email later (increment 3)

    def flag_fragment(request, company_id, kind, document, *, error=None):
        shown = company(request, company_id)
        state = None
        try:
            queue = run(request, 'delivery queue', {'kind': kind, 'document': document}, company_id)
            queued = next((r for r in queue['rows'] if r['document_id'] == document), None)
            state = {'checked': queued is not None, 'why': queued['why'] if queued else None,
                     'problem': queued['problem'] if queued else None, 'flag': queue.get('flag')}
        except BookflowError as e:
            if e.code in _PASS:
                raise
            error = error or e.to_dict()
        message = None
        if state is not None and not state['checked']:
            rows = run(request, 'delivery query', {'status': 'all', 'kind': kind, 'source': document, 'limit': 5},
                       company_id)['rows']
            message = next((r for r in rows if r['status'] != 'cancelled'), None)
        return render('delivery_flag.html', request, company_id=shown['company_id'], kind=kind, document=document,
                      state=state, message=message, error=error, can_write=allowed(request, shown, 'delivery flag'))

    @app.get('/c/{company_id}/delivery/flag')
    def delivery_flag(request: Request, company_id: str):
        try:
            return flag_fragment(request, company_id, request.query_params.get('kind') or '',
                                 request.query_params.get('document') or '')
        except BookflowError as e:
            return page_error(request, e)

    def flag_submit(request, company_id, form):
        kind, document = form.get('kind') or '', form.get('document') or ''
        error = None
        try:
            # Unticking clears the flag back to neutral; only "Never email this" writes a lasting opt-out.
            raw = {'kind': kind, 'document': document}
            if form.get('never') == '1':
                command, reason = 'delivery unflag', 'Never email this'
            elif form.get('email_later') == 'on':
                command, reason = 'delivery flag', 'Email later'
            else:
                command, reason = 'delivery unflag', 'Email later cleared'
                raw['clear'] = True
            run(request, command, raw, company_id, headers=headers(form, reason))
        except BookflowError as e:
            if e.code in _PASS:
                raise
            error = e.to_dict()
        return flag_fragment(request, company_id, kind, document, error=error)

    @app.post('/c/{company_id}/delivery/flag')
    async def delivery_flag_post(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(flag_submit, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)

    # ------------------------------------------------------------ batches (increment 3)

    def batch_input(source):
        selection = source.get('selection') or 'queued'
        raw = {}
        if selection == 'statement':
            raw['kind'] = 'statement'
            for key in ('period', 'date_from', 'date_to', 'balance_over'):
                if (source.get(key) or '').strip():
                    raw[key] = source[key].strip()
            if source.get('method') == 'email':
                raw['method'] = 'email'
        elif selection == 'notice':
            raw.update(kind='notice', ready=True)
        else:
            raw[selection if selection in ('queued', 'unsent') else 'queued'] = True
            if source.get('kind') in ('invoice', 'sales-receipt', 'estimate'):
                raw['kind'] = source['kind']
            for key in ('date_from', 'date_to'):
                if (source.get(key) or '').strip():
                    raw[key] = source[key].strip()
            if source.get('combine') == 'on':
                raw['combine'] = True
        for key in ('customer', 'customer_type'):
            if (source.get(key) or '').strip():
                raw[key] = source[key].strip()
        return selection, raw

    def batch_page(request, company_id, source, *, error=None, result=None, batch=None, status_code=200,
                   stopped=False):
        """The selection with its preview, or one batch's view (``batch``): a refusal on a batch's continue, send or
        cancel keeps that view with the refusal in place (``stopped`` halts the automatic loop, leaving Continue)."""
        shown = company(request, company_id)
        selection, raw = batch_input(source)
        preview = None
        if batch is None and source.get('preview') != 'no':
            try:
                preview = run(request, 'delivery batch', raw, company_id, dry_run=True)
            except BookflowError as e:
                if e.code in _PASS:
                    raise
                error = error or e.to_dict()
        listing = None
        if batch is not None and batch.get('number'):
            listing = run(request, 'delivery query', {'batch': batch['number'], 'status': 'all', 'limit': 500},
                          company_id)
        return render('delivery_batch.html', request, status_code=status_code, company=shown,
                      company_id=shown['company_id'], selection=selection, values=source, preview=preview,
                      batch=batch, listing=listing, error=error, result=result, kind_label=KIND_LABEL,
                      stopped=stopped or error is not None,
                      can_write=allowed(request, shown, 'delivery batch'), is_person=credential(request) is not None)

    @app.get('/c/{company_id}/delivery/batch')
    def delivery_batch(request: Request, company_id: str):
        try:
            source = dict(request.query_params)
            if source.get('batch'):  # back to a batch: from the Outbox, or a closed tab
                batch = run(request, 'delivery batch-show', {'batch': source['batch']}, company_id)
                return batch_page(request, company_id, source, batch=batch, stopped=True)
            return batch_page(request, company_id, source)
        except BookflowError as e:
            return page_error(request, e)

    def batch_submit(request, company_id, data):
        form = {k: v for k, v in data.items()}
        action = form.get('action') or 'draft'
        try:
            if action == 'continue':
                raw = {'batch': form['batch']}
                if form.get('fingerprint'):
                    raw['fingerprint'] = form['fingerprint']
                batch = run(request, 'delivery batch', raw, company_id, headers=headers(form, 'Batch of emails'))
            elif action == 'send':
                out = run(request, 'delivery send', {'batch': form['batch']}, company_id,
                          headers=headers(form, 'Send the batch'))
                batch = run(request, 'delivery batch-show', {'batch': form['batch']}, company_id)
                return batch_page(request, company_id, form, batch=batch, result=f"Released {out['count']}. " + ' '.join(
                    w for w in out.get('warnings') or [] if not w.startswith('No sending connection')))
            elif action == 'cancel':
                batch = run(request, 'delivery batch-cancel', {'batch': form['batch']}, company_id,
                            headers=headers(form))
                return batch_page(request, company_id, form, batch=batch, result=(
                    f"Cancelled batch {batch['number']}: {len(batch['cancelled'])} message(s). "
                    + ' '.join(batch.get('warnings') or [])))
            else:
                _, raw = batch_input(form)
                keep = set(data.getlist('keep'))
                raw['exclude'] = [key for key in data.getlist('key') if key not in keep]
                if form.get('fingerprint'):
                    raw['fingerprint'] = form['fingerprint']
                    raw['seen'] = list(data.getlist('seen'))
                if form.get('send') == 'on':
                    raw['send'] = True
                batch = run(request, 'delivery batch', raw, company_id, headers=headers(form, 'Batch of emails'))
        except BookflowError as e:
            if e.code in _PASS:
                raise
            status = 200 if request.headers.get('HX-Request') else 400
            if action in ('continue', 'send', 'cancel') and form.get('batch'):
                try:
                    batch = run(request, 'delivery batch-show', {'batch': form['batch']}, company_id)
                except BookflowError:
                    batch = None
                if batch is not None:
                    return batch_page(request, company_id, form, batch=batch, error=e.to_dict(),
                                      status_code=status)
            return batch_page(request, company_id, form, error=e.to_dict(), status_code=status)
        if batch['state'] == 'nothing':
            return batch_page(request, company_id, form, result=' '.join(batch.get('warnings') or []))
        return batch_page(request, company_id, form, batch=batch)

    @app.post('/c/{company_id}/delivery/batch')
    async def delivery_batch_post(request: Request, company_id: str):
        data = await request.form()
        try:
            return await run_in_threadpool(batch_submit, request, company_id, data)
        except BookflowError as e:
            return page_error(request, e)

    # ------------------------------------------------------------ one message

    def message_page(request, company_id, number, *, error=None, status_code=200, result=None, values=None,
                     export=None):
        shown = company(request, company_id)
        message = run(request, 'delivery show', {'message': number}, company_id)
        history = run(request, 'delivery history', {'message': number}, company_id)
        return render('delivery_message.html', request, status_code=status_code, company=shown,
                      company_id=shown['company_id'], message=message, history=history, error=error, result=result,
                      export=export,
                      values=values or {}, connection=_connection_note(message['connection']),
                      kind_label=KIND_LABEL, can_write=allowed(request, shown, 'delivery update'),
                      is_person=credential(request) is not None)

    @app.get('/c/{company_id}/delivery/{number}')
    def delivery_message(request: Request, company_id: str, number: str):
        try:
            return message_page(request, company_id, number)
        except BookflowError as e:
            return page_error(request, e)

    @app.get('/c/{company_id}/delivery/{number}/file/{position}')
    def delivery_file(request: Request, company_id: str, number: str, position: int):
        try:
            shown = run(request, 'delivery show', {'message': number, 'include_files': True}, company_id)
            doc = next((d for d in shown['documents'] if d['position'] == position), None)
            if doc is None or not doc.get('content_base64'):
                raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'delivery_file',
                                                                   'selector': str(position)})
            return Response(content=base64.b64decode(doc['content_base64']), media_type=doc['media_type'], headers={
                'Content-Disposition': f'inline; filename="{doc["file_name"]}"', 'Cache-Control': 'no-store',
                'X-Content-Type-Options': 'nosniff'})
        except BookflowError as e:
            return page_error(request, e)

    def message_submit(request, company_id, number, form):
        action = form.get('action') or ''
        try:
            if action == 'update':
                raw = {'message': number, 'to': _addresses(form.get('to')), 'cc': _addresses(form.get('cc')),
                       'subject': (form.get('subject') or '').strip(),
                       'body': (form.get('body') or '').replace('\r\n', '\n')}
                run(request, 'delivery update', raw, company_id, headers=headers(form, 'Edited the email'))
                result = 'Saved.'
            elif action == 'refresh':
                run(request, 'delivery update', {'message': number, 'refresh': True}, company_id,
                    headers=headers(form, 'Attached the document as it is now'))
                result = 'Attached the document as it is now.'
            else:
                result = act(request, company_id, form, [number])
                if isinstance(result, dict):
                    return message_page(request, company_id, number, **result)
        except BookflowError as e:
            return failed(request, e, message_page, company_id, number, values=dict(form))
        return message_page(request, company_id, number, result=result)

    @app.post('/c/{company_id}/delivery/{number}')
    async def delivery_message_post(request: Request, company_id: str, number: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(message_submit, request, company_id, number, form)
        except BookflowError as e:
            return page_error(request, e)

    # ------------------------------------------------------------ settings and templates

    def settings_page(request, company_id, *, error=None, status_code=200, result=None, values=None, editing=None):
        shown = company(request, company_id)
        templates = run(request, 'delivery-template query', {'include_inactive': True}, company_id)['rows']
        fields = run(request, 'delivery-template fields', {}, company_id)['fields']
        connections = run(request, 'delivery-connection query', {}, company_id)
        return render('delivery_settings.html', request, status_code=status_code, company=shown,
                      company_id=shown['company_id'], info=shown.get('info', {}), templates=templates, fields=fields,
                      kinds=KINDS, kind_label=KIND_LABEL, error=error, result=result, values=values or {},
                      editing=editing, connections=connections, can_admin=allowed(request, shown, 'company update'),
                      can_write=allowed(request, shown, 'delivery-template create'))

    @app.get('/c/{company_id}/delivery-settings')
    def delivery_settings(request: Request, company_id: str):
        try:
            editing = request.query_params.get('template')
            return settings_page(request, company_id, editing=editing)
        except BookflowError as e:
            return page_error(request, e)

    def settings_submit(request, company_id, form):
        action = form.get('action') or ''
        try:
            if action == 'addresses':
                raw = {k: (form.get(k) or '').strip() or None for k in ('delivery_reply_to', 'delivery_copy_to',
                                                                         'delivery_auto_flag_from')}
                run(request, 'company update', raw, company_id, headers=headers(form, 'Email settings'))
                result = 'Email addresses saved.'
            elif action == 'template':
                raw = {k: form.get(k) or '' for k in ('name', 'subject', 'body')}
                raw['body'] = raw['body'].replace('\r\n', '\n')
                if form.get('template'):
                    if form.get('is_default') == 'on':
                        raw['is_default'] = True
                    out = run(request, 'delivery-template update', {'template': form['template'], **raw}, company_id,
                              headers=headers(form, 'Email template'))
                else:
                    out = run(request, 'delivery-template create', {'kind': form.get('kind'), **raw,
                              'is_default': form.get('is_default') == 'on'}, company_id,
                              headers=headers(form, 'Email template'))
                result = f"Template {out['name']} saved."
            elif action == 'deactivate':
                out = run(request, 'delivery-template deactivate', {'template': form.get('template')}, company_id,
                          headers=headers(form, 'Email template not used'))
                result = f"Template {out['name']} deactivated."
            else:
                return settings_page(request, company_id)
        except BookflowError as e:
            return failed(request, e, settings_page, company_id, values=dict(form),
                          editing=form.get('template') or ('new' if action == 'template' else None))
        return settings_page(request, company_id, result=result)

    @app.post('/c/{company_id}/delivery-settings')
    async def delivery_settings_post(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(settings_submit, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)

    # ------------------------------------------------------------ recurring schedules (increment 4)

    FREQUENCIES = (('daily', 'Every day'), ('weekly', 'Every week'), ('every_other_week', 'Every other week'),
                   ('every_four_weeks', 'Every four weeks'), ('twice_a_month', 'Twice a month'),
                   ('monthly', 'Every month'), ('every_other_month', 'Every other month'),
                   ('quarterly', 'Every quarter'), ('twice_a_year', 'Twice a year'), ('annually', 'Every year'))
    PERIODS = (('previous-month', 'The previous month'), ('month-to-date', 'The month to date'),
               ('since-last-run', 'Since the last run'))

    def schedules_page(request, company_id, *, error=None, status_code=200, result=None, values=None, editing=None):
        shown = company(request, company_id)
        listing = run(request, 'delivery-schedule query', {}, company_id)
        current = None
        if editing and editing != 'new':
            current = run(request, 'delivery-schedule show', {'schedule': editing}, company_id)
        return render('delivery_schedules.html', request, status_code=status_code, company=shown,
                      company_id=shown['company_id'], listing=listing, error=error, result=result,
                      values=values or {}, editing=editing, current=current, kinds=KINDS, kind_label=KIND_LABEL,
                      frequencies=FREQUENCIES, periods=PERIODS,
                      can_write=allowed(request, shown, 'delivery-schedule create'),
                      can_live=allowed(request, shown, 'delivery-schedule live'))

    @app.get('/c/{company_id}/delivery-schedules')
    def delivery_schedules(request: Request, company_id: str):
        try:
            return schedules_page(request, company_id, editing=request.query_params.get('edit'))
        except BookflowError as e:
            return page_error(request, e)

    def schedule_input(form, *, new):
        kind = form.get('kind')
        out = {'kind': kind, 'frequency': form.get('frequency') or None, 'start_date': form.get('start_date') or None,
               'mode': form.get('mode') or 'draft', 'combine': form.get('combine') == 'on',
               'template': form.get('template') or None}
        if form.get('name'):
            out['name'] = ' '.join(form['name'].split())
        what = form.get('what') or ''
        if kind == 'statement':
            out.update(period=form.get('period') or None, balance_over=(form.get('balance_over') or '').strip() or None,
                       method='email' if form.get('email_only') == 'on' else None)
        elif kind == 'notice':
            out['ready'] = True
        elif what in ('queued', 'unsent'):
            out[what] = True
        if form.get('stop_date'):
            out['stop_date'] = form['stop_date']
        elif not new:
            out['no_end'] = not form.get('remaining_runs')
        if form.get('remaining_runs'):
            out['remaining_runs'] = form['remaining_runs']
        if form.get('catch_up_days'):
            out['catch_up_days'] = form['catch_up_days']
        if form.get('run_time'):
            out['run_time'] = form['run_time']
        if not new:
            # Every selection field is sent, so a change of kind or selection clears what no longer applies.
            for k in ('queued', 'unsent', 'ready', 'period', 'balance_over', 'method'):
                out.setdefault(k, None)
            out['customer'] = form.get('customer') or None
        return out

    def live_page(request, company_id, schedule, *, error=None, status_code=200):
        shown = company(request, company_id)
        out = run(request, 'delivery-schedule live', {'schedule': schedule}, company_id, dry_run=True)
        return render('delivery_schedule_live.html', request, status_code=status_code, company=shown,
                      company_id=shown['company_id'], schedule=out, check=out['check'], error=error)

    @app.get('/c/{company_id}/delivery-schedules/{schedule}/live')
    def delivery_schedule_live(request: Request, company_id: str, schedule: str):
        try:
            return live_page(request, company_id, schedule)
        except BookflowError as e:
            if e.code in _PASS:
                raise
            return schedules_page(request, company_id, error=e.to_dict(), status_code=400)

    def schedules_submit(request, company_id, form):
        action = form.get('action') or ''
        name = form.get('schedule') or ''
        try:
            if action == 'create':
                out = run(request, 'delivery-schedule create', schedule_input(form, new=True), company_id,
                          headers=headers(form, 'Email schedule'))
                result = f"Schedule {out['name']} saved. " + ' '.join(out['warnings'])
            elif action == 'update':
                body = dict(schedule_input(form, new=False), schedule=name)
                body['expected_version'] = int(form['version']) if form.get('version') else None
                out = run(request, 'delivery-schedule update', body, company_id,
                          headers=headers(form, 'Email schedule changed'))
                result = f"Schedule {out['name']} saved. " + ' '.join(out['warnings'])
            elif action in ('pause', 'resume', 'run'):
                out = run(request, f'delivery-schedule {action}', {'schedule': name}, company_id,
                          headers=headers(form, {'pause': 'Stop the schedule', 'resume': 'Resume the schedule',
                                                 'run': 'Run the schedule now'}[action]))
                ran = out.get('ran') or []
                result = {'pause': f"Schedule {out['name']} stopped.", 'resume': f"Schedule {out['name']} resumed.",
                          'run': f"Schedule {out['name']}: " + ('; '.join(
                              f"{r['slot_date']} {r['state']}" + (f", {r['drafted']} drafted" if r['drafted'] else '')
                              for r in ran) or 'nothing ran') + '.'}[action]
                if out['warnings']:
                    result += ' ' + ' '.join(out['warnings'])
            elif action == 'live':
                try:
                    out = run(request, 'delivery-schedule live', {'schedule': name, 'confirm': form.get('confirm')},
                              company_id, headers=headers(form, 'Make the schedule live'))
                except BookflowError as e:
                    if e.code in _PASS:
                        raise
                    return live_page(request, company_id, name, error=e.to_dict(),
                                     status_code=200 if request.headers.get('HX-Request') else 400)
                result = f"Schedule {out['name']} is live. " + ' '.join(out['warnings'])
            else:
                return schedules_page(request, company_id)
        except BookflowError as e:
            return failed(request, e, schedules_page, company_id, values=dict(form),
                          editing=name or ('new' if action == 'create' else None))
        return schedules_page(request, company_id, result=result.strip())

    @app.post('/c/{company_id}/delivery-schedules')
    async def delivery_schedules_post(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(schedules_submit, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)
