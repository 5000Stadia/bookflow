"""Landed cost (R104 increment 6): one page for freight waiting to be spread and spreading it.

The page lists the unallocated charges (Landed Cost Clearing lines of bills, checks, card charges and vendor
credits), an allocation form -- charges ticked, target receipts by document, a basis, date and reason -- whose
Preview is the command's dry run, showing each target's share split into what stays in stock and what was
issued before the allocation date, and the allocations with Void. With no clearing account yet it offers to
make one. Every write goes through its command (`landed-cost setup`, `post`, `void`) with a reason.
"""
from __future__ import annotations

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.clock import today_iso
from bookflow.core.errors import BookflowError

TARGETS = 5


def mount(app, *, render, run, credential, page_error, role_allows):
    def page(request, company_id, *, values=None, result=None, preview=None, error=None, status_code=200):
        company = run(request, 'company show', {}, company_id)
        if not role_allows('landed-cost charges', company, credential(request)):
            raise BookflowError('E_PERMISSION', details={'capability': 'ledger.read', 'required_role': 'member'})
        charges = run(request, 'landed-cost charges', {}, company_id)
        allocations = run(request, 'landed-cost query', {'kind': None, 'limit': 25}, company_id)['rows']
        return render('landed_cost.html', request, status_code=status_code, company=company,
                      company_id=company['company_id'], charges=charges, allocations=allocations,
                      values=values or {}, result=result, preview=preview, error=error, targets=TARGETS,
                      can_write=role_allows('landed-cost post', company, credential(request)), today=today_iso())

    @app.get('/c/{company_id}/inventory/landed-cost')
    def landed_page(request: Request, company_id: str):
        try:
            return page(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    def _allocation(form):
        charges = [{'charge': key[len('charge:'):], **({'amount': form[f'amount:{key[len("charge:"):]}'].strip()}
                                                        if (form.get(f'amount:{key[len("charge:"):]}') or '').strip() else {})}
                   for key in form if key.startswith('charge:') and form[key] == 'on']
        basis = (form.get('basis') or 'quantity').strip()
        targets = []
        for n in range(TARGETS):
            document = (form.get(f'target_{n}') or '').strip()
            if not document:
                continue
            target = {'document': document}
            if (form.get(f'item_{n}') or '').strip():
                target['item'] = form[f'item_{n}'].strip()
            if basis == 'amounts' and (form.get(f'share_{n}') or '').strip():
                target['amount'] = form[f'share_{n}'].strip()
            targets.append(target)
        if not charges:
            raise BookflowError('E_VALIDATION', message='Tick at least one charge to allocate.',
                                details={'fields': [{'field': 'charges', 'problem': 'tick a charge'}]})
        if not targets:
            raise BookflowError('E_VALIDATION', message='Name at least one receipt to spread the charge over.',
                                details={'fields': [{'field': 'targets', 'problem': 'name a receipt'}]})
        raw = {'charges': charges, 'targets': targets, 'basis': basis}
        for key in ('date', 'memo'):
            if (form.get(key) or '').strip():
                raw[key] = form[key].strip()
        return raw

    def submit(request, company_id, form):
        action = form.get('action') or ''
        headers = {'X-Bookflow-Reason': form['ctx:reason'].strip()} if (form.get('ctx:reason') or '').strip() else None
        preview = None
        try:
            if action == 'setup':
                out = run(request, 'landed-cost setup', {}, company_id, headers=headers)
                result = out['message']
            elif action in ('preview', 'post'):
                raw = _allocation(form)
                if action == 'preview':
                    preview = run(request, 'landed-cost post', raw, company_id, headers=headers, dry_run=True)['allocation']
                    return page(request, company_id, values=dict(form), preview=preview)
                out = run(request, 'landed-cost post', raw, company_id, headers=headers)['allocation']
                result = (f"Allocation {out['number']} spread {out['total']['amount']} on {out['date']}: "
                          + '; '.join(f"{t['target_document'] or t['account_name']} {t['amount']['amount']}"
                                      for t in out['targets']) + '.')
            elif action.startswith('void:'):
                number = action.split(':', 1)[1]
                out = run(request, 'landed-cost void', {'allocation': number}, company_id, headers=headers)['allocation']
                result = f"Voided allocation {out['number']}; its charges are unallocated again."
            else:
                return page(request, company_id)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                return page_error(request, e)
            return page(request, company_id, values=dict(form), error=e.to_dict(), status_code=400)
        return page(request, company_id, result=result)

    @app.post('/c/{company_id}/inventory/landed-cost')
    async def landed_submit(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(submit, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)


def record_panel(request, run, company_id, out, record_id):
    """A receipt, bill, check or card charge's stock lines with their landed cost and landed unit cost, or None.

    Read through `landed-cost query` and `landed-cost show`, so the page shows what an agent reads."""
    from decimal import Decimal
    transaction_id = out.get('transaction_id') or out.get('id') or record_id
    try:
        listed = run(request, 'landed-cost query', {'kind': None, 'status': 'posted', 'document': record_id}, company_id)['rows']
    except BookflowError:
        return None
    rows, numbers = {}, []
    for allocation in listed:
        shown = run(request, 'landed-cost show', {'allocation': allocation['id']}, company_id)['allocation']
        mine = [line for line in shown['lines'] if line['target_transaction_id'] == transaction_id and not line['reversed']]
        if not mine:
            continue
        numbers.append(shown['number'])
        for line in mine:
            row = rows.setdefault(line['target_movement_id'], dict(item_name=line['item_name'], quantity=line['target_quantity'],
                                                                   minor=0, currency=line['amount']['currency']))
            row['minor'] += line['amount']['minor_units']
    if not rows:
        return None
    out_rows = []
    for row in rows.values():
        amount = Decimal(row['minor']) / 100
        quantity = Decimal(row['quantity'] or '0')
        unit = f'{(amount / quantity).quantize(Decimal("0.0001"))}' if quantity else ''
        out_rows.append(dict(item_name=row['item_name'], quantity=row['quantity'], unit=unit,
                             amount={'amount': f'{amount:.2f}', 'currency': row['currency'], 'minor_units': row['minor']}))
    return {'rows': out_rows, 'numbers': numbers}
