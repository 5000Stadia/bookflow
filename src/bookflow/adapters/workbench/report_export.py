"""Saving the report on the screen as a file a spreadsheet opens.

The file is `report export`'s own output -- the command every surface runs, over the one
renderer in `bookflow.documents.report_csv` -- so the download, the CLI's `report <name> --csv` and an
agent's MCP call carry the same text. This module only links to it with the page's own
filters, reads those filters back off the link, and serves the command's text with a byte
order mark, because a spreadsheet opening a CSV without one reads an accented name as mojibake.

It runs through ``run``, like every other workbench route, so the acting principal's
permissions and company isolation apply exactly as they do on the report page. A member who
cannot read the report cannot export it.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

from fastapi import FastAPI, Request
from fastapi.responses import Response

from bookflow.adapters.workbench.forms import translate
from bookflow.adapters.workbench.transaction_detail import collection_fields
from bookflow.core import registry
from bookflow.core.errors import BookflowError
# The renderer's names, for the print view and the tests that read them from here.
from bookflow.documents.report_csv import (  # noqa: F401
    ATTEMPTS, MEDIA_TYPE, PAGE, PAGES, _guard, _readable, cell, columns, document, filename,
    is_report, preamble, read_all, reports, row_model,
)

# The last path segment of the download, and the extension a spreadsheet recognises.
SEGMENT = "export.csv"


def export_url(company_id: str, verb: str, inputs) -> str:
    """Where the report on the screen is downloaded from, carrying its own filters.

    The filters are written in the report form's own encoding -- the same ``f:`` leaves and
    the same repeated-control keys the Next-page button carries -- so there is one spelling
    of a report's filters in this workbench and not a second one invented here.

    The continuation and the page size are deliberately dropped: the file is the whole
    report, so it starts at the first row and reads at the command's own maximum.
    """
    fields: dict[str, str] = {}
    for key, value in (inputs or {}).items():
        if key in ("cursor", "limit") or value is None:
            continue
        if isinstance(value, list):
            fields.update(collection_fields(key, value))
        else:
            fields[f"f:{key}"] = str(value).lower() if isinstance(value, bool) else str(value)
    query = urlencode(fields)
    return (f"/c/{quote(str(company_id), safe='')}/report/{quote(verb, safe='')}/{SEGMENT}"
            + (f"?{query}" if query else ""))


def inputs_from(cmd, params) -> dict[str, Any]:
    """The report's own filters, read off the query the download link carried.

    Read by the form translator the report page itself submits through, so a flag, a
    number and a repeated filter arrive as the same values the report page would have
    sent -- and a filter that gains a type tomorrow is read correctly here on the same day.
    """
    raw, _headers, _preview = translate(cmd, {key: params[key] for key in params}, None)
    # The file is the whole report from its first row, whatever a hand-written link says.
    raw.pop("cursor", None)
    raw.pop("limit", None)
    return raw


def install(app: FastAPI, *, run, page_error) -> None:
    """Register the export route. Called before the generic `<noun>/<record>/<verb>`
    route, which would otherwise read `export.csv` as a command name."""

    @app.get("/c/{company_id}/report/{verb}/" + SEGMENT, name="report-export")
    def report_export(company_id: str, verb: str, request: Request):
        cmd = registry.get(f"report {verb}")
        if not is_report(cmd):
            return page_error(request, BookflowError("E_USAGE", message=f"unknown report {verb}"),
                              company_id=company_id)
        try:
            exported = run(request, "report export",
                           {"report": verb, "filters": inputs_from(cmd, request.query_params)}, company_id)
        except BookflowError as err:
            return page_error(request, err, company_id=company_id)
        # A byte order mark, because a spreadsheet opening a CSV without one reads a
        # customer's accented name as mojibake. Nothing is cached: these are the books.
        return Response(
            content="\ufeff".encode("utf-8") + exported["content"].encode("utf-8"), media_type=exported["media_type"],
            headers={"Content-Disposition": f'attachment; filename="{exported["filename"]}"',
                     "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
