"""The POS page (R114 increment 4, design/point-of-sale.md §3): ``/c/<company>/pos``.

The menu entry *Point of sale* under Customers, its Overview tile and a POS clerk's Home all land here, and only while
the company has POS on. Until the register screen arrives (increment 5) the page names the registers and says what is
coming; with POS off it answers the switch's own message, as `pos-register list` does.
"""
from __future__ import annotations

from fastapi import Request

from bookflow.core.errors import BookflowError


def mount(app, *, render, run, credential, page_error, role_allows):
    @app.get('/c/{company_id}/pos')
    def pos_page(request: Request, company_id: str):
        try:
            company = run(request, 'company show', {}, company_id)
            registers = run(request, 'pos-register list', {}, company_id)['items']
            cred = credential(request)
            return render('pos.html', request, company=company, company_id=company['company_id'], registers=registers,
                          can_receipt=role_allows('sales-receipt post', company, cred),
                          can_layout=role_allows('pos-register update', company, cred))
        except BookflowError as e:
            return page_error(request, e)
