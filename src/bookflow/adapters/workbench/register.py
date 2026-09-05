"""Account-register presentation. All reads and writes use shared commands."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from bookflow.core import registry
from bookflow.core.errors import BookflowError


def edit_projection(journal: dict[str, Any], account: dict[str, Any], label) -> dict[str, Any] | None:
    """Losslessly expose an expressible current revision; never flatten journals."""
    revision = journal["revision"]
    lines = revision["lines"]
    if (journal["status"] != "posted" or revision["id"] != journal["current_revision_id"]
            or revision.get("custom_fields_snapshot") or len(lines) < 2
            or lines[0]["account_id"] != account["id"]
            or any(line["account_id"] == account["id"] for line in lines[1:])
            or lines[0]["class_id"] is not None or lines[0].get("class_name") is not None
            or revision.get("name_type") is not None or revision.get("name_id") is not None
            or lines[0]["position"] != 1
            or lines[0]["description"] != revision["memo"]
            or any(line.get(field) is not None for line in lines for field in
                   ("original_minor_units", "original_currency", "rate_used", "rate_source"))):
        return None
    selected = lines[0]
    def party(line):
        return {"name_type": line["name_type"], "name_id": line["name_id"]} if line["name_id"] else None
    payload = dict(account=account["id"], journal=journal["id"], expected_version=journal["version"],
                   selected_line_id=selected["line_id"], date=revision["date"], number=revision["number"],
                   memo=revision["memo"], payee=party(selected),
                   direction="increase" if selected["side"] == account["normal_balance"] else "decrease",
                   amount=selected["amount"]["amount"])
    labels = {"payee": selected["party_name"] or ""}
    offset = lines[1]
    if (len(lines) == 2 and offset["description"] == revision["memo"]
            and party(offset) == party(selected)):
        payload.update(category=offset["account_id"], category_line_id=offset["line_id"], class_id=offset["class_id"])
        labels.update(category=label(offset["account_snapshot"]), class_id=offset["class_name"] or "")
    else:
        payload["allocations"] = [dict(
            line_id=line["line_id"], account=line["account_id"], amount=line["amount"]["amount"],
            direction="decrease" if line["side"] == account["normal_balance"] else "increase",
            memo=line["description"], party=party(line),
            class_mode="value" if line["class_id"] else "none",
            **({"class_id": line["class_id"]} if line["class_id"] else {}),
        ) for line in lines[1:]]
        labels["allocations"] = [dict(account=label(line["account_snapshot"]),
                                      party=line["party_name"] or "", class_id=line["class_name"] or "")
                                  for line in lines[1:]]
    return {"payload": payload, "labels": labels}


def install(app: FastAPI, *, render, run, credential, page_error) -> None:
    @app.get("/c/{company_id}/account/{record_id}/register", response_class=HTMLResponse)
    def account_register(company_id: str, record_id: str, request: Request):
        # Local import keeps the shared pages module free to install this route.
        from bookflow.adapters.workbench.pages import _role_allows, _reference_label
        try:
            company = run(request, "company show", {}, company_id)
            account = run(request, "account show", {"account": record_id}, company_id)
            cred = credential(request)
            info = company["info"]
            supported = account["statement_family"] == "balance_sheet"
            may_write = supported and account["active"] and account["currency"] == info["home_currency"]
            may_write = bool(may_write and (cmd := registry.get("register post"))
                             and _role_allows(cmd, company, hub_admin=cred.hub_admin))
            today = datetime.now(ZoneInfo(info["timezone"])).date().isoformat()
            edit = None
            if request.query_params.get("edit") and may_write:
                journal = run(request, "journal show", {"journal": request.query_params["edit"]}, company_id)
                edit = edit_projection(journal, account, lambda row: _reference_label("account", row, company))
                if edit is None:
                    suffix = "/update" if journal["status"] == "posted" else ""
                    return RedirectResponse(
                        f"/c/{company_id}/journal/{journal['id']}{suffix}",
                        status_code=303, headers={"Cache-Control": "no-store"},
                    )
            directions = [("decrease", "Payment"), ("increase", "Deposit")] if account["type"] == "bank" else (
                [("increase", "Charge"), ("decrease", "Payment")] if account["type"] == "credit_card" else
                [("increase", "Increase"), ("decrease", "Decrease")])
            config = dict(actor=cred.user_id, company=company_id, account=account["id"], today=today,
                          timezone=info["timezone"], currency=info["home_currency"], writable=may_write,
                          supported=supported, directions=directions, edit=edit,
                          date_from=request.query_params.get("date_from", today[:4] + "-01-01"),
                          date_to=request.query_params.get("date_to", today))
            response = render("register.html", request, company_id=company_id, noun="account", account=account,
                              account_label=_reference_label("account", account, company), config=config,
                              gl_url=f"/c/{company_id}/report/general-ledger?" + urlencode({"f:account": account["id"]}))
            response.headers["Cache-Control"] = "no-store"
            return response
        except BookflowError as err:
            return page_error(request, err)
