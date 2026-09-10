"""Serving a printable copy of the four documents a customer receives.

Printing in Bookflow is producing the document's PDF and handing it to the browser: the
browser's own Print command then prints it, and Save prints it to a file. There is no
second HTML layout to keep in step, because the same `bookflow.documents.render` that
answers these routes is what a later attach-to-record or mail command calls.

Each route reads through `run`, so the acting principal's permissions and company
isolation apply exactly as they do on the record page. A member who cannot read the
invoice cannot print it.
"""
from __future__ import annotations

from urllib.parse import quote, urlencode

from fastapi import FastAPI, Request
from fastapi.responses import Response

from bookflow.core.errors import BookflowError
from bookflow.documents import render

STATEMENT_PATH = "/report/statement/print"


def document_url(company_id: str, noun: str, record_id: str) -> str:
    return (f"/c/{quote(str(company_id), safe='')}/{noun}/"
            f"{quote(str(record_id), safe='')}/print")


def statement_url(company_id: str, customer_id: str, date_from: str, date_to: str) -> str:
    return f"/c/{quote(str(company_id), safe='')}{STATEMENT_PATH}?" + urlencode(
        {"customer": customer_id, "date_from": date_from, "date_to": date_to})


def _pdf(rendered) -> Response:
    """Inline, so the browser opens its own viewer and its Print command is one click.

    The filename still travels, so Save keeps a name a person recognises. Nothing is
    cached: these bytes are a customer's financial record on a shared workstation.
    """
    return Response(
        content=rendered.content, media_type=rendered.media_type,
        headers={"Content-Disposition": f'inline; filename="{rendered.filename}"',
                 "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def install(app: FastAPI, *, run, page_error) -> None:
    """Register the print routes. Called before the generic verb route, which would
    otherwise read `print` as a command name and answer `unknown command`."""

    def serve(request: Request, company_id: str, kind: str, identity):
        try:
            rendered = render(lambda name, raw, company: run(request, name, raw, company),
                              company_id, kind, identity)
        except BookflowError as err:
            return page_error(request, err, company_id=company_id)
        return _pdf(rendered)

    @app.get("/c/{company_id}/invoice/{record_id}/print")
    def invoice_print(company_id: str, record_id: str, request: Request):
        return serve(request, company_id, "invoice", {"document": record_id})

    @app.get("/c/{company_id}/sales-receipt/{record_id}/print")
    def sales_receipt_print(company_id: str, record_id: str, request: Request):
        return serve(request, company_id, "sales-receipt", {"document": record_id})

    @app.get("/c/{company_id}/estimate/{record_id}/print")
    def estimate_print(company_id: str, record_id: str, request: Request):
        return serve(request, company_id, "estimate", {"document": record_id})

    @app.get("/c/{company_id}" + STATEMENT_PATH)
    def statement_print(company_id: str, request: Request):
        # A statement is one customer's account over one period. Which three values that
        # takes is the renderer's rule, and it says so itself when one is missing.
        return serve(request, company_id, "statement",
                     {key: request.query_params.get(key)
                      for key in ("customer", "date_from", "date_to")})
