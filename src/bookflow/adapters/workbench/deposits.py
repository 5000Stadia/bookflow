"""Ordinary browser entry to registered public deposit reads.

The pages render exactly what `deposit show` and `deposit items` return and
nothing else. Existing deposit writes keep their generated form routes. The detail page is the bounded summary alone: the composition is
fetched only when the reader asks for one of its item pages, and no page issues
a command per source row.

Admission to a deposit is all-or-nothing over its connected closure, so a reader
admitted to the deposit but not to something connected receives the shared
non-disclosing exact-read refusal. A link is therefore an attempt to open a
deposit, never a promise that it will open, and the refusal is rendered as one
actionable sentence that is identical for a record that is gone and for one this
reader may not see.
"""
from urllib.parse import quote, urlencode, urlsplit

from fastapi import Request

from bookflow.core.errors import BookflowError
from bookflow.core import registry
from bookflow.core.money import Money

KINDS = (("sources", "Contributing receipts"),
         ("additional", "Additional cash rows"),
         ("cash_allocations", "Cash allocations"))
KIND_LABELS = dict(KINDS)
PAGE_LIMIT = 50
LIST_LIMIT = 25
FILTERS = ("deposit_to", "status", "date_from", "date_to", "number", "q", "sort", "direction")
TOTAL_LABELS = (("source_total", "Contributing receipts"), ("positive_additional_total", "Additional cash in"),
                ("negative_additional_total", "Additional cash out"), ("subtotal", "Subtotal"),
                ("cash_back", "Cash back"), ("posting_total", "Posting total"), ("bank_total", "Revision bank total"))

def _list_return(request, company_id):
    base = f"/c/{quote(company_id, safe='')}/deposit"
    value = request.query_params.get("return_to", "")
    try:
        parsed = urlsplit(value)
    except ValueError:
        return base
    return value if not parsed.scheme and not parsed.netloc and parsed.path == base and not parsed.fragment else base


# The accepted disposition for a closure denial: one sentence, the same for a
# genuinely missing record and for a denied one, naming no connected record and
# asserting no specific cause.
UNAVAILABLE = ("Deposit details are unavailable. The record may no longer exist or your access "
               "may have changed. Return to the register or ask your company administrator for help.")

# What a reference group reads as when this reader is not admitted to it. Never
# blank, and never the value of some other reference.
REDACTED = "Not available to you"


def _decorate(value):
    """Give every wire amount and every reference its own rendered label.

    One pass over the document the command returned, so no amount reaches a page
    as raw minor units and no undisclosed reference can render as empty.
    """
    if isinstance(value, dict):
        if set(value) == {"minor_units", "currency"}:
            money = Money(value["minor_units"], value["currency"])
            value["display"] = money.amount
            value["label"] = str(money)
            value["negative"] = value["minor_units"] < 0
            value["zero"] = value["minor_units"] == 0
            return value
        if "disclosed" in value:
            value["redacted"] = not value["disclosed"]
            value["display_label"] = REDACTED if not value["disclosed"] else (
                value.get("label") or value.get("full_name") or value.get("name")
                or value.get("display_name") or "—")
        elif "available" in value and "group" in value:
            value["display_label"] = ((value.get("label") or "—") if value["available"]
                                      else "No longer in this company")
        for item in list(value.values()):
            _decorate(item)
    elif isinstance(value, list):
        for item in value:
            _decorate(item)
    return value


def _selection(request: Request) -> dict:
    """The revision and dated choice this reader made, kept across every link."""
    picked: dict = {}
    raw = request.query_params.get("revision_number")
    if raw:
        try:
            picked["revision_number"] = int(raw)
        except ValueError:
            raise BookflowError("E_VALIDATION", details={"fields": [
                {"field": "revision_number", "problem": "must be an integer"}]}) from None
    if request.query_params.get("as_of"):
        picked["as_of"] = request.query_params["as_of"]
    return picked


def _url(path: str, **params) -> str:
    kept = {name: value for name, value in params.items() if value not in (None, "")}
    return path + ("?" + urlencode(kept) if kept else "")


def _selection_label(selection: dict, kind: str | None, cursor: str | None) -> str:
    """What the reader chose, said back to them when a page cannot be shown."""
    parts = []
    if selection.get("revision_number"):
        parts.append(f"revision {selection['revision_number']}")
    if selection.get("as_of"):
        parts.append(f"as of {selection['as_of']}")
    if kind:
        parts.append(KIND_LABELS.get(kind, kind).lower())
    if cursor:
        parts.append("a later page")
    return ", ".join(parts)


def mount(app, *, render, run, credential, page_error, role_allows, form_page):
    """Install the deposit pages. Registered before the generic record routes."""

    @app.get("/c/{company_id}/deposit")
    def deposit_list(request: Request, company_id: str):
        path = f"/c/{quote(company_id, safe="")}/deposit"
        filters = {key: request.query_params.get(key, "") for key in FILTERS}
        filters["sort"] = filters["sort"] or "date"
        filters["direction"] = filters["direction"] or "desc"
        raw = {key: value for key, value in filters.items() if value != ""}
        cursor = request.query_params.get("cursor")
        raw["page"] = dict(limit=LIST_LIMIT, **({"cursor": cursor} if cursor else {}))
        restart = _url(path, **filters)
        shared = dict(company_id=company_id, list_url=path, filters=filters, restart_url=restart,
                      total_labels=TOTAL_LABELS, company_url=f"/c/{quote(company_id, safe="")}/")
        try:
            page = _decorate(run(request, "deposit query", raw, company_id))
        except BookflowError as exc:
            message = ("This results page has changed or its continuation is invalid. Restart with your retained filters."
                       if cursor and exc.code in {"E_QUERY_STALE", "E_VALIDATION"} else
                       "Deposits could not be loaded. Check your filters and try again; if this continues, ask your company administrator.")
            return render("deposit_list.html", request, page=None, error_code=exc.code, message=message,
                          status_code=409 if exc.code == "E_QUERY_STALE" else 400, **shared)
        return_to = _url(path, **filters, cursor=cursor)
        for row in page["items"]:
            row["url"] = _url(path + "/" + quote(row["selected"]["pin"]["deposit_id"], safe=""), return_to=return_to)
        return render("deposit_list.html", request, page=page, error_code=None, message=None,
                      next_url=_url(path, **filters, cursor=page["next_cursor"]) if page["next_cursor"] else None,
                      previous_url=_url(path, **filters, cursor=page["previous_cursor"]) if page["previous_cursor"] else None, **shared)

    def context(company_id: str, deposit_id: str, selection: dict, *,
                kind: str | None = None, cursor: str | None = None, return_to: str | None = None) -> dict:
        """Links every deposit page shares, all of them inside this company."""
        company = quote(company_id, safe="")
        detail = f"/c/{company}/deposit/{quote(deposit_id, safe='')}"
        return dict(
            company_id=company_id, deposit_id=deposit_id, selection=selection,
            kind=kind, redacted_text=REDACTED,
            selection_label=_selection_label(selection, kind, cursor),
            list_url=return_to or f"/c/{company}/deposit",
            detail_url=_url(detail, **selection, return_to=return_to),
            items_base=detail + "/items",
            company_url=f"/c/{company}/",
            accounts_url=f"/c/{company}/account",
            item_links=[dict(kind=name, label=label, current=(name == kind),
                             url=_url(detail + "/items", kind=name, **selection, return_to=return_to))
                        for name, label in KINDS])

    def unavailable(request: Request, company_id: str, deposit_id: str, selection: dict,
                    kind: str | None = None, cursor: str | None = None):
        """One rendering, for a record that is gone and for one this reader may not see."""
        return render("deposit_detail.html", request, status_code=404, detail=None,
                      unavailable=UNAVAILABLE, register_url=None,
                      retry_url=request.url.path + (
                          "?" + request.url.query if request.url.query else ""),
                      **context(company_id, deposit_id, selection, kind=kind, cursor=cursor, return_to=_list_return(request, company_id)))

    @app.get("/c/{company_id}/deposit/{deposit_id}")
    def deposit_detail(request: Request, company_id: str, deposit_id: str):
        if registry.get(f"deposit {deposit_id}") is not None:
            return form_page(request, company_id, "deposit", deposit_id, None)
        try:
            selection = _selection(request)
        except BookflowError as exc:
            return page_error(request, exc, company_id=company_id)
        try:
            detail = _decorate(run(request, "deposit show",
                                   dict(selection, deposit=deposit_id), company_id))
        except BookflowError as exc:
            if exc.code == "E_RECORD_NOT_FOUND":
                return unavailable(request, company_id, deposit_id, selection)
            return page_error(request, exc, company_id=company_id)
        # Every revision is its own callable `deposit show`; the dated choice
        # rides along so switching revisions does not silently drop it.
        path = f"/c/{quote(company_id, safe='')}/deposit/{quote(deposit_id, safe='')}"
        dated = {"as_of": selection["as_of"]} if "as_of" in selection else {}
        for revision in detail["revisions"]:
            revision["url"] = _url(path, revision_number=revision["revision_number"], **dated, return_to=_list_return(request, company_id))
        account = detail["selected"]["deposit_to"]
        register = (f"/c/{quote(company_id, safe='')}/account/"
                    f"{quote(account['id'], safe='')}/register") if account.get("id") else None
        return render("deposit_detail.html", request, detail=detail, unavailable=None,
                      register_url=register, retry_url=None,
                      **context(company_id, deposit_id, selection, return_to=_list_return(request, company_id)))

    @app.get("/c/{company_id}/deposit/{deposit_id}/items")
    def deposit_items(request: Request, company_id: str, deposit_id: str):
        kind = request.query_params.get("kind") or "sources"
        cursor = request.query_params.get("cursor") or None
        try:
            selection = _selection(request)
        except BookflowError as exc:
            return page_error(request, exc, company_id=company_id)
        shared = context(company_id, deposit_id, selection, kind=kind, cursor=cursor, return_to=_list_return(request, company_id))
        # `as_of` picks a dated bank effect on the summary; the composition of an
        # immutable revision has no such dimension. It rides the links instead,
        # so returning to the summary keeps the reader's whole selection.
        raw = {name: value for name, value in selection.items() if name != "as_of"}
        raw.update(deposit=deposit_id, kind=kind,
                   page=dict(limit=PAGE_LIMIT, **({"cursor": cursor} if cursor else {})))
        try:
            page = _decorate(run(request, "deposit items", raw, company_id))
        except BookflowError as exc:
            if exc.code == "E_RECORD_NOT_FOUND":
                return unavailable(request, company_id, deposit_id, selection,
                                   kind=kind, cursor=cursor)
            # A continuation this reader can no longer use: the collection moved
            # on, or the token itself is not one this page issued. Either way the
            # answer is to start the same collection again with the same
            # revision, rather than to lose what they picked.
            if exc.code == "E_QUERY_STALE" or (
                    exc.code == "E_VALIDATION" and exc.details.get("field") == "cursor"):
                return render("deposit_items.html", request, page=None, stale=True, next_url=None,
                              status_code=409 if exc.code == "E_QUERY_STALE" else 400,
                              restart_url=_url(shared["items_base"], kind=kind, **selection, return_to=shared["list_url"]),
                              **shared)
            return page_error(request, exc, company_id=company_id)
        return render("deposit_items.html", request, page=page, stale=False, restart_url=None,
                      next_url=_url(shared["items_base"], kind=kind,
                                    cursor=page["next_cursor"], **selection, return_to=shared["list_url"])
                      if page["next_cursor"] else None, **shared)
