"""Workbench pages, generated from the registry and NOUN_META (row 3 plan, The workbench)."""

from __future__ import annotations

import json
import secrets
import threading
import time
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from jinja2 import Environment, FileSystemLoader, select_autoescape

from bookflow.adapters.workbench import forms as F
from bookflow.core import registry
from bookflow.core.errors import BookflowError
from bookflow.core.models import list_columns
from bookflow.hub.access import ROLE_FOR_REQUIRED, ROLE_RANK
from bookflow.storage import migrate

HERE = Path(__file__).parent
LAST_COMPANY = "bookflow_company"
FLASH_TTL_SECONDS = 60.0
env = Environment(loader=FileSystemLoader(str(HERE / "templates")), autoescape=select_autoescape(["html"]))


class _FlashStore:
    """Process-local, one-use command results bound to the credential that created them."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, str, dict[str, Any]]] = {}

    def put(self, session_token: str, payload: dict[str, Any]) -> str:
        now = time.monotonic()
        with self._lock:
            self._items = {key: item for key, item in self._items.items() if item[0] > now}
            while True:
                key = secrets.token_urlsafe(24)
                if key not in self._items:
                    break
            self._items[key] = (now + FLASH_TTL_SECONDS, session_token, payload)
        return key

    def take(self, key: str, session_token: str) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, owner, payload = item
            if expires_at <= now:
                self._items.pop(key, None)
                return None
            if owner != session_token:
                return None
            self._items.pop(key, None)
            return payload


def _nouns(scope: str) -> list[str]:
    return sorted({c.noun for c in registry.routed_commands() if c.scope == scope})


def _verbs(noun: str, scope: str | None = None) -> list[registry.Command]:
    """A noun's routed commands; on a hub page only hub-scoped ones, on a company page only company-scoped ones."""
    return [c for c in registry.routed_commands() if c.noun == noun and (scope is None or c.scope == scope)]


_GROUP_ORDER = (
    "Company",
    "Customers and sales",
    "Vendors and purchases",
    "Employees",
    "Items",
    "Accounting",
    "Settings",
    "Audit",
    "Hub",
)


def _grouped_nouns(noun_rows: list[tuple[str, list[registry.Command]]], *, company: bool) -> list[tuple[str, list[tuple[str, list[registry.Command]]]]]:
    grouped: dict[str, list[tuple[str, list[registry.Command]]]] = {}
    for noun, verbs in noun_rows:
        meta = registry.noun_meta(noun)
        if meta.get("ui_group"):
            group = str(meta["ui_group"])
        elif noun == "company":
            group = "Company"
        elif noun in ("audit", "hub audit", "undo"):
            group = "Audit"
        elif noun in ("profile",):
            group = "Settings"
        elif noun in ("chart",):
            group = "Accounting"
        else:
            group = "Hub"
        grouped.setdefault(group, []).append((noun, verbs))
    order = {name: index for index, name in enumerate(_GROUP_ORDER)}
    return [
        (group, sorted(rows, key=lambda row: (registry.noun_meta(row[0]).get("ui_order", 999), row[0])))
        for group, rows in sorted(grouped.items(), key=lambda item: (order.get(item[0], 999), item[0]))
    ]


def _presence_types() -> set[str]:
    cmd = registry.get("presence set")
    if cmd is None:
        return set()
    from typing import Literal, get_args, get_origin
    ann = cmd.input_model.model_fields["record_type"].annotation
    return set(get_args(ann)) if get_origin(ann) is Literal else set()


def _role_allows(cmd: registry.Command, record: dict[str, Any], *, hub_admin: bool = False) -> bool:
    if hub_admin or record.get("access") == "hub_admin":
        return True
    required = cmd.required_role
    if required is None:
        return True
    if required == "hub_admin":
        return False
    role = record.get("role")
    return role in ROLE_RANK and ROLE_RANK[role] >= ROLE_FOR_REQUIRED[required]


def _may_publish_presence(record: dict[str, Any], *, hub_admin: bool = False) -> bool:
    if hub_admin or record.get("access") == "hub_admin":
        return True
    role = record.get("role")
    return role in ROLE_RANK and ROLE_RANK[role] >= ROLE_RANK["standard"]


def _output_identifier(noun: str, meta: dict[str, Any], output: dict[str, Any]) -> str | None:
    identifier = meta.get("output_identifier") or meta.get("identifier")
    if identifier:
        for key in (identifier, f"{identifier}_id"):
            value = output.get(key)
            if value and not isinstance(value, (dict, list)):
                return str(value)
    nested = output.get(noun)
    if isinstance(nested, dict):
        for key in (identifier, "id", "code"):
            if key and nested.get(key):
                return str(nested[key])
    return None


def _editable_values(noun: str, shown: dict[str, Any]) -> dict[str, Any]:
    """Project the authoritative editable object from a show result."""
    definition = registry.noun_meta(noun).get("definition")
    path = definition.editable_output_path if definition is not None else ()
    current: Any = shown
    for part in path:
        if not isinstance(current, dict):
            return {}
        current = current.get(part)
    if not path and definition is None and isinstance(shown.get("info"), dict):
        current = shown["info"]
    return dict(current) if isinstance(current, dict) else {}


def _custom_value_map(value: Any) -> dict[str, Any]:
    """Translate a show projection into the static command mapping shape."""
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, list):
        return {}
    return {
        str(item["definition_id"]): item.get("value")
        for item in value
        if isinstance(item, dict) and item.get("definition_id")
    }


def _reference_label(
    target: str,
    row: dict[str, Any],
    company_view: dict[str, Any],
) -> str:
    definition = registry.noun_meta(target).get("definition")
    display_field = definition.display_field if definition is not None else "name"
    label = next(
        (
            str(row[field])
            for field in (display_field, "full_name", "name", "code", "text", "id")
            if row.get(field) not in (None, "")
        ),
        str(row.get("id", "")),
    )
    if target == "account":
        info = company_view.get("info") if isinstance(company_view.get("info"), dict) else company_view
        if info.get("show_lowest_subaccount_only") and row.get("name"):
            label = str(row["name"])
        if info.get("use_account_numbers") and row.get("number"):
            label = f"{row['number']} · {label}"
    return label


def _reference_target(
    targets: tuple[str, ...],
    noun: str,
    originals: dict[str, Any],
    attempted: dict[str, str],
) -> tuple[str | None, str | None]:
    if len(targets) == 1:
        return targets[0], None
    discriminator = "name_type" if noun == "sales-rep" else None
    chosen = F.selected_value(discriminator, originals, attempted) if discriminator else None
    normalized = str(chosen).replace("_", "-") if chosen else None
    return (normalized if normalized in targets else None), discriminator


def _inactive_toggle(path: str, request: Request, *, include: bool) -> str:
    """Toggle inactive rows without discarding any other list-page state."""
    pairs = [(key, value) for key, value in request.query_params.multi_items()
             if key != "include_inactive"]
    if not include:
        pairs.append(("include_inactive", "1"))
    query = urlencode(pairs)
    return path + (f"?{query}" if query else "")


def _success_target(cmd: registry.Command, company_id: str | None, noun: str, record_id: str | None,
                    output: dict[str, Any]) -> str:
    route_noun = noun.replace(" ", "-")
    if record_id is not None:
        base = f"/c/{company_id}/{noun}" if company_id else f"/hub/{route_noun}"
        return f"{base}/{record_id}"
    if cmd.scope == "company":
        if noun == "company":
            return f"/c/{company_id}/company/self"
        found = _output_identifier(noun, registry.noun_meta(noun), output)
        if found and registry.get(f"{noun} show") is not None:
            return f"/c/{company_id}/{noun}/{found}"
        if registry.get(f"{noun} list") is not None:
            return f"/c/{company_id}/{noun}"
        return f"/c/{company_id}/"
    if cmd.name == "company detach":
        return "/companies"
    if cmd.name in ("company new", "company attach", "demo reset") and output.get("company_id"):
        return f"/c/{output['company_id']}/"
    found = _output_identifier(noun, registry.noun_meta(noun), output)
    if found and registry.get(f"{noun} show") is not None:
        return f"/hub/{route_noun}/{found}"
    return f"/hub/{route_noun}"


def mount_workbench(app: FastAPI, host, credential, make_context, run_command, secure_cookies: bool) -> None:
    from bookflow.adapters.http.app import STATUS, error_response, safe_workbench_destination

    flashes = _FlashStore()

    def render(name: str, request: Request, status_code: int = 200, **ctx: Any) -> HTMLResponse:
        if not ctx.get("company_id") and request.cookies.get(LAST_COMPANY):
            ctx.setdefault("header_company_id", request.cookies.get(LAST_COMPANY))  # hub pages keep the company links
        flash_id = request.query_params.get("flash")
        if flash_id:
            try:
                session_token = credential(request).token_id
            except BookflowError:
                pass
            else:
                ctx.setdefault("flash_result", flashes.take(flash_id, session_token))
        tpl = env.get_template(name)
        response = HTMLResponse(tpl.render(request=request, hub_nouns=_nouns("hub"), company_nouns=_nouns("company"), json=json, **ctx),
                                status_code=status_code)
        if ctx.get("flash_result") is not None:
            response.headers["Cache-Control"] = "no-store"
        return response

    def page_error(request: Request, err: BookflowError, **ctx: Any) -> HTMLResponse:
        if err.code in ("E_UNAUTHENTICATED", "E_LOGIN_FAILED"):
            from urllib.parse import quote
            target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse("/login" + (f"?next={quote(target, safe='')}" if request.method == "GET" and target not in ("/", "/login") else ""), status_code=303)
        status = STATUS.get(err.code, 400)
        return render("error.html", request, status_code=status, error=err.to_dict(), status=status, **ctx)

    def run(request: Request, name: str, raw: dict[str, Any], company: str | None, headers: dict[str, str] | None = None, dry_run: bool = False) -> dict[str, Any]:
        cmd = registry.get(name)
        cred = credential(request)
        ctx = make_context(request, cred)
        if headers:
            ctx = ctx.model_copy(update={"reason": headers.get("X-Bookflow-Reason", ctx.reason), "source_ref": headers.get("X-Bookflow-Source-Ref", ctx.source_ref),
                                         "directive_id": headers.get("X-Bookflow-Directive", ctx.directive_id), "idempotency_key": headers.get("Idempotency-Key", ctx.idempotency_key)})
        ctx = ctx.model_copy(update={"client_name": "bookflow-workbench"})
        return run_command(cmd, raw, ctx, cred, company, "option" if company else "none", dry_run)

    @app.get("/static/{name}")
    def static(name: str):
        p = HERE / "static" / name
        if not p.exists() or "/" in name:
            return Response(status_code=404)
        media = "text/css" if name.endswith(".css") else "application/javascript"
        return Response(p.read_bytes(), media_type=media, headers={"Cache-Control": "max-age=3600"})

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        nxt = safe_workbench_destination(request.query_params.get("next"))
        return render("login.html", request, error=None, next=nxt)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        """Land in a company: the only one you can see, else the one this browser used last, else the picker."""
        try:
            out = run(request, "company list", {}, None)
        except BookflowError as e:
            return page_error(request, e)
        items = out["items"]
        ids = {c["company_id"] for c in items}
        last = request.cookies.get(LAST_COMPANY)
        if len(items) == 1:
            return RedirectResponse(f"/c/{items[0]['company_id']}/", status_code=303)
        if last in ids:
            return RedirectResponse(f"/c/{last}/", status_code=303)
        return render("picker.html", request, companies=items, columns=list_columns(items), schema_head=migrate.HEADS["company"])

    @app.get("/companies", response_class=HTMLResponse)
    def picker(request: Request):
        try:
            out = run(request, "company list", {}, None)
        except BookflowError as e:
            return page_error(request, e)
        return render("picker.html", request, companies=out["items"], columns=list_columns(out["items"]), schema_head=migrate.HEADS["company"])

    @app.get("/hub/", response_class=HTMLResponse)
    def hub_index(request: Request):
        try:
            cred = credential(request)
        except BookflowError as e:
            return page_error(request, e)
        nouns = [
            (n, [cmd for cmd in _verbs(n, "hub") if _role_allows(cmd, {}, hub_admin=cred.hub_admin)])
            for n in _nouns("hub")
        ]
        return render("index.html", request, company=None, groups=_grouped_nouns(nouns, company=False))

    @app.get("/c/{company_id}/", response_class=HTMLResponse)
    def company_index(company_id: str, request: Request):
        try:
            show = run(request, "company show", {}, company_id)
        except BookflowError as e:
            return page_error(request, e)
        is_hub_admin = credential(request).hub_admin
        nouns = [
            (n, [cmd for cmd in _verbs(n, "company") if _role_allows(cmd, show, hub_admin=is_hub_admin)])
            for n in _nouns("company")
        ]
        resp = render(
            "index.html",
            request,
            company=show,
            company_id=show["company_id"],
            groups=_grouped_nouns(nouns, company=True),
        )
        resp.set_cookie(LAST_COMPANY, show["company_id"], samesite="lax", secure=secure_cookies, max_age=90 * 86400, path="/")  # a per-browser convenience, no identity in it
        return resp

    @app.get("/c/{company_id}/_references/{owner_noun}/{field}", response_class=HTMLResponse)
    def reference_suggestions(
        company_id: str,
        owner_noun: str,
        field: str,
        request: Request,
    ):
        """Return at most 25 active same-company choices for a declared reference."""
        try:
            cred = credential(request)
            company_view = run(request, "company show", {}, company_id)
        except BookflowError as err:
            return page_error(request, err)
        definition = registry.noun_meta(owner_noun).get("definition")
        reference = F.reference_for_path(definition, field)
        if reference is None:
            return page_error(request, BookflowError("E_USAGE", message="unknown form reference"))
        targets = reference.target_nouns
        requested_target = request.query_params.get("target")
        if requested_target is None and len(targets) > 1:
            requested_target = request.query_params.get("f:name_type")
        target = requested_target.replace("_", "-") if requested_target else (targets[0] if len(targets) == 1 else None)
        if target not in targets:
            return page_error(request, BookflowError("E_USAGE", message="invalid reference target"))
        list_command = registry.get(f"{target} list")
        if list_command is None or list_command.local_only:
            return page_error(request, BookflowError("E_USAGE", message="reference target is not listable"))
        if not _role_allows(list_command, company_view, hub_admin=cred.hub_admin):
            return page_error(request, BookflowError("E_PERMISSION", details={
                "capability": list_command.capability,
                "required_role": list_command.required_role,
            }))
        query = request.query_params.get("q")
        if query is None:
            query = request.query_params.get(f"f:{field}", "")
        query = query.strip()
        if not query:
            return HTMLResponse("", headers={"Cache-Control": "no-store"})
        if len(query) > 200:
            return page_error(request, BookflowError(
                "E_VALIDATION",
                details={"fields": [{"field": "query", "problem": "must be at most 200 characters"}]},
            ))
        try:
            output = run(request, list_command.name, {"query": query}, company_id)
        except BookflowError as err:
            return page_error(request, err)
        target_definition = registry.noun_meta(target).get("definition")
        identifier = target_definition.identifier if target_definition is not None else "id"
        options = []
        for row in output.get("items", [])[:25]:
            stable_id = row.get(identifier) or row.get("id")
            if stable_id is None or not row.get("active", True):
                continue
            label = _reference_label(target, row, company_view)
            options.append(
                f'<option value="{escape(str(stable_id), quote=True)}" label="{escape(label, quote=True)}"></option>'
            )
        return HTMLResponse("".join(options), headers={"Cache-Control": "no-store"})

    def noun_page(request: Request, company_id: str | None, noun: str):
        if noun in ("audit", "hub audit"):
            return audit_common(request, company_id if noun == "audit" else None)
        role_view = None
        try:
            cred = credential(request)
        except BookflowError as e:
            return page_error(request, e)
        if company_id:
            try:
                role_view = run(request, "company show", {}, company_id)
            except BookflowError as e:
                return page_error(request, e)
        all_page_verbs = _verbs(noun, "company" if company_id else "hub")
        page_verbs = all_page_verbs
        if role_view is not None:
            page_verbs = [cmd for cmd in page_verbs
                          if not cmd.is_write or _role_allows(cmd, role_view, hub_admin=cred.hub_admin)]
        else:
            page_verbs = [cmd for cmd in page_verbs if _role_allows(cmd, {}, hub_admin=cred.hub_admin)]
        cmd = registry.get(f"{noun} list")
        if cmd is None:
            single = registry.get(noun)  # a single-word command such as `upgrade`: the noun's page is its form
            if single is not None and not single.local_only:
                return form_page(request, company_id, noun, "", None)
        if cmd is None or cmd.local_only:
            if not all_page_verbs:
                return page_error(request, BookflowError("E_USAGE", message=f"no such noun `{noun}`"))
            # a noun without a list (presence) still has a page: its actions
            return render("list.html", request, has_show=registry.get(f"{noun} show") is not None, company_id=company_id, noun=noun, items=[], columns=[], meta=registry.noun_meta(noun),
                          has_inactive=False, include=False, verbs=page_verbs, extra={"note": "this noun has no list; use its actions"})
        include = request.query_params.get("include_inactive") == "1"
        raw: dict[str, Any] = {}
        for field in ("query", "sort", "direction"):
            value = request.query_params.get(field)
            if value and field in cmd.input_model.model_fields:
                raw[field] = value
        filters = request.query_params.getlist("filter")
        if filters and "filter" in cmd.input_model.model_fields:
            raw["filter"] = filters
        if include and "include_inactive" in cmd.input_model.model_fields:
            raw["include_inactive"] = True
        try:
            out = run(request, cmd.name, raw, company_id if cmd.scope == "company" else None)
        except BookflowError as e:
            return page_error(request, e)
        meta = registry.noun_meta(noun)
        items = out.get("items", [])
        definition = meta.get("definition")
        columns = list(definition.default_columns) if definition is not None else list_columns(items)
        column_text = request.query_params.get("columns", "")
        if column_text and definition is not None:
            requested_columns = [field.strip() for field in column_text.split(",") if field.strip()]
            unknown_columns = [field for field in requested_columns if field not in definition.all_columns]
            if not requested_columns or unknown_columns or len(requested_columns) != len(set(requested_columns)):
                return page_error(request, BookflowError(
                    "E_LIST_FILTER",
                    details={
                        "problem": "columns must be a unique comma-separated selection",
                        "unknown": unknown_columns,
                        "allowed": list(definition.all_columns),
                    },
                ))
            columns = requested_columns
        return render(
            "list.html",
            request,
            has_show=registry.get(f"{noun} show") is not None,
            company_id=company_id,
            noun=noun,
            items=items,
            columns=columns,
            meta=meta,
            has_inactive="include_inactive" in cmd.input_model.model_fields,
            include=include,
            inactive_toggle=_inactive_toggle(request.url.path, request, include=include),
            verbs=page_verbs,
            query=raw.get("query", ""),
            filters=filters,
            selected_sort=raw.get("sort", ""),
            selected_direction=raw.get("direction", "asc"),
            selected_columns=",".join(columns),
            extra={k: v for k, v in out.items() if k != "items"},
        )

    @app.get("/c/{company_id}/{noun}", response_class=HTMLResponse)
    def company_noun(company_id: str, noun: str, request: Request):
        return noun_page(request, company_id, noun)

    @app.get("/hub/{noun}", response_class=HTMLResponse)
    def hub_noun(noun: str, request: Request):
        return noun_page(request, None, noun.replace("-", " "))

    def record_page(request: Request, company_id: str | None, noun: str, record_id: str):
        command_noun = "hub audit" if company_id is None and noun == "audit" else noun
        meta = registry.noun_meta(command_noun)
        show = registry.get(f"{command_noun} show")
        if show is None:
            return page_error(request, BookflowError("E_USAGE", message=f"`{noun}` has no show command"))
        raw = {meta["identifier"]: record_id} if meta["identifier"] else {}
        try:
            out = run(request, show.name, raw, company_id if show.scope == "company" else None)
            cred = credential(request)
            role_view = out
            if company_id:
                company_view = out if show.name == "company show" else run(request, "company show", {}, company_id)
                role_view = {"access": company_view.get("access"), "role": company_view.get("role")}
            audit = None
            if company_id and registry.get("audit list"):
                rid = out.get("company_id", record_id) if noun == "company" else out.get("id", record_id)
                audit = run(request, "audit list", {"record_type": meta["record_type"], "record_id": rid, "limit": 20}, company_id)["items"]
        except BookflowError as e:
            return page_error(request, e)
        verbs = [c for c in _verbs(command_noun, "company" if company_id else "hub")
                 if c.verb not in ("list", "show", "new") and _role_allows(c, role_view, hub_admin=cred.hub_admin)]
        audit_undo = None
        if company_id is not None and command_noun == "audit":
            if out.get("undo_of_event_id"):
                audit_undo = {"original_event_id": out["undo_of_event_id"]}
            else:
                undo_command = registry.get("undo")
                if undo_command is not None and _role_allows(undo_command, role_view, hub_admin=cred.hub_admin):
                    try:
                        run(request, "undo", {"event_id": out["id"]}, company_id, dry_run=True)
                    except BookflowError as err:
                        if err.code == "E_ALREADY_UNDONE":
                            audit_undo = {"undo_event_id": err.details.get("undo_event_id")}
                        elif err.code in ("E_NOT_UNDOABLE", "E_UNDO_CONFLICT"):
                            audit_undo = {
                                "unavailable": err.details.get("problem") or err.message,
                            }
                    else:
                        audit_undo = {"eligible": True, "event_id": out["id"]}
        visible_record = {key: value for key, value in out.items() if key != "editing_by"}
        return render("record.html", request, company_id=company_id, noun=noun, record_id=record_id, record=visible_record, audit=audit, meta=meta, verbs=verbs,
                      audit_undo=audit_undo,
                      presence=(meta["record_type"] in _presence_types()) and company_id is not None
                               and _may_publish_presence(role_view, hub_admin=cred.hub_admin),
                      editing=(out.get("editing_by") or []) if _may_publish_presence(role_view, hub_admin=cred.hub_admin) else [])

    @app.get("/c/{company_id}/{noun}/{record_id}", response_class=HTMLResponse)
    def company_record(company_id: str, noun: str, record_id: str, request: Request):
        if registry.get(f"{noun} {record_id}") is not None:  # a verb, not a record
            return form_page(request, company_id, noun, record_id, None)
        return record_page(request, company_id, noun, record_id)

    @app.get("/hub/{noun}/{record_id}", response_class=HTMLResponse)
    def hub_record(noun: str, record_id: str, request: Request):
        noun = noun.replace("-", " ")
        if registry.get(f"{noun} {record_id}") is not None:
            return form_page(request, None, noun, record_id, None)
        return record_page(request, None, noun, record_id)

    def form_page(request: Request, company_id: str | None, noun: str, verb: str, record_id: str | None, result: dict | None = None, error: dict | None = None, preview: bool = False, attempted: dict[str, str] | None = None):
        cmd = registry.get(f"{noun} {verb}".strip())
        if cmd is None or cmd.local_only:
            return page_error(request, BookflowError("E_USAGE", message=f"unknown command {noun} {verb}"))
        authorized_company = None
        try:
            cred = credential(request)
        except BookflowError as e:
            return page_error(request, e)
        if company_id is not None:
            try:
                authorized_company = run(request, "company show", {}, company_id)
            except BookflowError as e:
                return page_error(request, e)
            if cmd.is_write and not _role_allows(cmd, authorized_company, hub_admin=cred.hub_admin):
                return page_error(request, BookflowError("E_PERMISSION", details={
                    "capability": cmd.capability, "required_role": cmd.required_role,
                }))
        elif not _role_allows(cmd, {}, hub_admin=cred.hub_admin):
            return page_error(request, BookflowError("E_PERMISSION", details={
                "capability": cmd.capability, "required_role": cmd.required_role,
            }))
        originals = None
        shown = None
        generic_selector_form = record_id == "self" and cmd.version_source and cmd.version_source[0] != "company show"
        if cmd.version_source and record_id is not None and not generic_selector_form:
            show_name, ident, field = cmd.version_source
            raw = {ident: record_id} if ident else {}
            try:
                shown = authorized_company if show_name == "company show" and not raw else run(request, show_name, raw, company_id)
            except BookflowError as e:
                return page_error(request, e)
            originals = {**_editable_values(noun, shown), "expected_version": shown.get(field)}
        elif cmd.version_source and record_id is None:
            return page_error(request, BookflowError("E_USAGE", message="open this update from a record page"))
        originals = originals or {}
        attempted = attempted or {}
        meta = registry.noun_meta(noun)
        definition = meta.get("definition")
        if definition is not None and definition.custom_fields:
            originals["custom_fields"] = _custom_value_map(originals.get("custom_fields"))
        described = F.describe_fields(noun, verb, cmd.input_model, originals, attempted)
        if company_id is not None and definition is not None:
            for leaf in described:
                reference = F.reference_for_path(definition, leaf["path"])
                if reference is None:
                    continue
                targets = reference.target_nouns
                target, discriminator = _reference_target(targets, noun, originals, attempted)
                value = F.selected_value(leaf["path"], originals, attempted)
                current = None
                if target is not None and value:
                    target_meta = registry.noun_meta(target)
                    show_command = registry.get(f"{target} show")
                    identifier = target_meta.get("identifier")
                    if show_command is not None and identifier:
                        try:
                            row = run(request, show_command.name, {identifier: value}, company_id)
                        except BookflowError as err:
                            if err.code not in ("E_RECORD_NOT_FOUND", "E_INACTIVE_REFERENCE"):
                                return page_error(request, err)
                        else:
                            current = {
                                "id": row.get("id", value),
                                "label": _reference_label(target, row, authorized_company or {}),
                                "active": row.get("active", True),
                            }
                add_targets = []
                for candidate in targets:
                    create_command = registry.get(f"{candidate} create")
                    if (
                        create_command is not None
                        and _role_allows(create_command, authorized_company or {}, hub_admin=cred.hub_admin)
                    ):
                        target_definition = registry.noun_meta(candidate).get("definition")
                        add_targets.append({
                            "target": candidate,
                            "label": target_definition.singular_label if target_definition is not None else candidate,
                            "url": f"/c/{company_id}/{candidate}/create",
                        })
                leaf["reference"] = {
                    "targets": targets,
                    "target": target,
                    "discriminator": discriminator,
                    "suggestion_url": f"/c/{company_id}/_references/{noun}/{leaf['path']}",
                    "current": current,
                    "add_targets": add_targets,
                }
        runtime_fields = []
        if company_id is not None and definition is not None and definition.runtime_field_provider == "custom-fields":
            try:
                definitions = run(
                    request,
                    "custom-field list",
                    {"filter": [f"target_type={definition.record_type}"]},
                    company_id,
                ).get("items", [])
            except BookflowError as err:
                return page_error(request, err)
            runtime_fields = F.custom_field_descriptors(definitions)
            described = [leaf for leaf in described if leaf["path"] != "custom_fields"]
        return render("form.html", request, company_id=company_id, noun=noun, verb=verb, cmd=cmd, leaves=described, originals=originals,
                      attempted=attempted, record_id=record_id, runtime_fields=runtime_fields,
                      ctx_fields=F.context_fields(cmd), result=result, error=error,
                      preview=preview, get=F.get_path, form_value=F.form_value)

    @app.get("/hub/{noun}/{record_id}/{verb}", response_class=HTMLResponse)
    def hub_record_form(noun: str, record_id: str, verb: str, request: Request):
        return form_page(request, None, noun.replace("-", " "), verb, record_id)

    @app.get("/c/{company_id}/{noun}/{record_id}/{verb}", response_class=HTMLResponse)
    def company_record_form(company_id: str, noun: str, record_id: str, verb: str, request: Request):
        return form_page(request, company_id, noun, verb, record_id)

    def submit(request: Request, company_id: str | None, noun: str, verb: str, record_id: str | None, form: dict[str, str]):
        cmd = registry.get(f"{noun} {verb}".strip())
        if cmd is None or cmd.local_only:
            return page_error(request, BookflowError("E_USAGE", message=f"unknown command {noun} {verb}".strip()))
        originals = json.loads(form.get("originals", "{}") or "{}")
        try:
            session_token = credential(request).token_id
            raw, headers, preview = F.translate(cmd, form, originals if originals else None)
            if cmd.version_source and record_id is not None and "expected_version" not in raw and form.get("f:expected_version"):
                raw["expected_version"] = int(form["f:expected_version"])
            meta = registry.noun_meta(noun)
            if meta["identifier"] and record_id is not None and meta["identifier"] in cmd.input_model.model_fields:
                raw.setdefault(meta["identifier"], record_id)
            out = run(request, cmd.name, raw, company_id if cmd.scope == "company" else None, headers, dry_run=preview)
        except BookflowError as e:
            # a credential failure is not a form problem: it must carry its own status, or a page POST
            # without the workbench header would answer 200 and look accepted (row 3 plan, Authentication)
            if e.code in ("E_UNAUTHENTICATED", "E_WORKBENCH_HEADER"):
                return page_error(request, e)
            return form_page(request, company_id, noun, verb, record_id, error=e.to_dict(), attempted=form)
        if preview:
            return form_page(request, company_id, noun, verb, record_id, result=out, preview=True, attempted=form)
        target = _success_target(cmd, company_id, noun, record_id, out)
        flash_id = flashes.put(session_token, {"command": cmd.name, "result": out})
        location = f"{target}?flash={flash_id}"
        if request.headers.get("hx-request", "").lower() == "true":
            # A 303 is followed inside the XHR and leaves the address bar on
            # the submitted form. HX-Redirect performs the same GET as a
            # top-level navigation, making the successful destination visible.
            return Response(status_code=200, headers={"HX-Redirect": location})
        return RedirectResponse(location, status_code=303)

    async def form_of(request: Request) -> dict[str, str]:
        return {k: str(v) for k, v in (await request.form()).items()}

    @app.post("/c/{company_id}/{noun}/{verb}")
    async def company_submit(company_id: str, noun: str, verb: str, request: Request):
        return await run_in_threadpool(submit, request, company_id, noun, verb, None, await form_of(request))

    @app.post("/c/{company_id}/{noun}")
    async def company_single_submit(company_id: str, noun: str, request: Request):
        return await run_in_threadpool(submit, request, company_id, noun, "", None, await form_of(request))

    @app.post("/c/{company_id}/{noun}/{record_id}/{verb}")
    async def company_record_submit(company_id: str, noun: str, record_id: str, verb: str, request: Request):
        return await run_in_threadpool(submit, request, company_id, noun, verb, record_id, await form_of(request))

    @app.post("/hub/{noun}/{record_id}/{verb}")
    async def hub_record_submit(noun: str, record_id: str, verb: str, request: Request):
        return await run_in_threadpool(submit, request, None, noun.replace("-", " "), verb, record_id, await form_of(request))

    @app.post("/hub/{noun}/{verb}")
    async def hub_submit(noun: str, verb: str, request: Request):
        return await run_in_threadpool(submit, request, None, noun.replace("-", " "), verb, None, await form_of(request))

    @app.post("/hub/{noun}")
    async def hub_single_submit(noun: str, request: Request):
        return await run_in_threadpool(submit, request, None, noun.replace("-", " "), "", None, await form_of(request))

    @app.post("/c/{company_id}/presence/{action}/{record_type}/{record_id}")
    async def heartbeat(company_id: str, action: str, record_type: str, record_id: str, request: Request):
        return await run_in_threadpool(do_heartbeat, request, company_id, action, record_type, record_id)

    def do_heartbeat(request: Request, company_id: str, action: str, record_type: str, record_id: str):
        try:
            run(request, f"presence {action}", {"record_type": record_type, "record_id": record_id}, company_id)
        except BookflowError as e:
            return error_response(e)
        return Response(status_code=204)

    @app.get("/c/{company_id}/audit", response_class=HTMLResponse)
    def audit_page(company_id: str, request: Request):
        return audit_common(request, company_id)

    @app.get("/hub/audit", response_class=HTMLResponse)
    def hub_audit_page(request: Request):
        return audit_common(request, None)

    def audit_common(request: Request, company_id: str | None):
        name = "audit list" if company_id else "hub audit list"
        filters = {k: v for k, v in request.query_params.items() if v and k != "flash"}
        try:
            out = run(request, name, filters, company_id)
        except BookflowError as e:
            return page_error(request, e)
        return render("audit.html", request, company_id=company_id, items=out["items"], next_before=out.get("next_before"), filters=filters, cmd=registry.get(name), leaves=F.leaves(registry.get(name).input_model))
