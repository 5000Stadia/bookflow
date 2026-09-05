"""Workbench pages, generated from the registry and NOUN_META (row 3 plan, The workbench)."""

from __future__ import annotations

import json
import hashlib
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
from bookflow.adapters.workbench import workflows as W
from bookflow.adapters.workbench import statements as S
from bookflow.adapters.workbench import sales as Sales
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
    return [c for c in registry.routed_commands() if c.noun == noun and (c.verb != "query" or noun == "rate") and (scope is None or c.scope == scope)]


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


def _output_version(noun: str, output: dict[str, Any]) -> int | None:
    value = output.get("version")
    if isinstance(value, int):
        return value
    nested = output.get(noun)
    if isinstance(nested, dict) and isinstance(nested.get("version"), int):
        return int(nested["version"])
    return None


def _editable_values(noun: str, shown: dict[str, Any]) -> dict[str, Any]:
    """Project the authoritative editable object from a show result."""
    if noun in ('invoice', 'sales-receipt'):
        return Sales.editable_values(shown)
    if noun == "journal":
        revision = shown.get("revision", {})
        return {
            "number": revision.get("number"), "date": revision.get("date"),
            "memo": revision.get("memo"),
            "custom_fields": _custom_value_map(revision.get("custom_fields")),
            "lines": [{
                "line_id": line["line_id"], "account": line["account_id"],
                "side": line["side"], "amount": (
                    f"{line['original_amount']['amount']} {line['original_amount']['currency']}"
                    if line.get("original_amount") else line["amount"]["amount"]),
                **{key: line[key] for key in ("name_type", "name_id", "class_id", "description") if line.get(key) is not None},
            } for line in revision.get("lines", [])],
        }
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


def _form_reference(definition: Any, noun: str, path: str) -> Any | None:
    """Project the domain's sole authoritative reference declarations."""
    definition = definition or registry.noun_meta(noun).get('form_definition')
    return F.reference_for_path(definition, path)


def _decorate_collection_references(
    schema: dict[str, Any],
    *,
    definition: Any,
    noun: str,
    company_id: str,
    path: str,
    create_targets: Any = None,
) -> None:
    """Attach scoped search metadata to scalar leaves in recursive collections."""
    item = schema["item"]
    if item["kind"] == "collection":
        _decorate_collection_references(
            item["collection"],
            definition=definition,
            noun=noun,
            company_id=company_id,
            path=path,
            create_targets=create_targets,
        )
        return
    if item["kind"] != "object":
        return
    for child in item["fields"]:
        child_path = f"{path}.{child['name']}"
        if child["kind"] == "collection":
            _decorate_collection_references(
                child["collection"],
                definition=definition,
                noun=noun,
                company_id=company_id,
                path=child_path,
                create_targets=create_targets,
            )
            continue
        reference = _form_reference(definition, noun, child_path)
        if reference is None or len(reference.target_nouns) != 1:
            continue
        target = reference.target_nouns[0]
        child["reference"] = {
            "target": target,
            "suggestion_url": f"/c/{company_id}/_references/{noun}/{child_path}?target={target}",
            "include_closest": bool(getattr(reference, "child_units", False)),
            "add_targets": create_targets((target,)) if create_targets and not getattr(reference, "child_units", False) else [],
        }


def _record_selector(cmd: registry.Command, noun: str) -> str | None:
    if cmd.name == "rate set":
        return None
    if cmd.version_source:
        return cmd.version_source[1]
    conventional = noun.replace("-", "_").replace(" ", "_")
    if conventional in cmd.input_model.model_fields:
        return conventional
    return next(
        (field for field in cmd.positional if field in cmd.input_model.model_fields),
        None,
    )


def _matching_vendor_link(
    customer: dict[str, Any], vendor_id: str | None
) -> dict[str, Any] | None:
    if not vendor_id:
        return None
    return next(
        (
            link
            for link in customer.get("vendor_links", [])
            if isinstance(link, dict) and link.get("vendor_id") == vendor_id
        ),
        None,
    )


def _inactive_toggle(path: str, request: Request, *, include: bool) -> str:
    """Toggle inactive rows without discarding any other list-page state."""
    pairs = [(key, value) for key, value in request.query_params.multi_items()
             if key not in ("include_inactive", "cursor")]
    if not include:
        pairs.append(("include_inactive", "1"))
    query = urlencode(pairs)
    return path + (f"?{query}" if query else "")


def _success_target(cmd: registry.Command, company_id: str | None, noun: str, record_id: str | None,
                    output: dict[str, Any]) -> str:
    route_noun = noun.replace(" ", "-")
    if company_id and cmd.name == "rate set" and output.get("id"):
        return f"/c/{company_id}/rate/{output['id']}"
    if company_id and cmd.name in ("register post", "register update") and output.get("id"):
        return f"/c/{company_id}/journal/{output['id']}"
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
    static_urls = {
        name: f"/static/{name}?v={hashlib.sha256((HERE / 'static' / name).read_bytes()).hexdigest()[:16]}"
        for name in ("style.css", "htmx.min.js", "workflow.js", "annotations.js", "register.js", "register.css", "sales.js", "sales.css")
    }

    def render(name: str, request: Request, status_code: int = 200, **ctx: Any) -> HTMLResponse:
        if not ctx.get("company_id") and request.cookies.get(LAST_COMPANY):
            ctx.setdefault("header_company_id", request.cookies.get(LAST_COMPANY))  # hub pages keep the company links
        company_view = getattr(request.state, "workbench_company", None)
        if company_view and company_view["company_id"] == (ctx.get("company_id") or ctx.get("header_company_id")):
            ctx["company_label"] = company_view["display_name"]
        flash_id = request.query_params.get("flash")
        if flash_id:
            try:
                session_token = credential(request).token_id
            except BookflowError:
                pass
            else:
                ctx.setdefault("flash_result", flashes.take(flash_id, session_token))
        tpl = env.get_template(name)
        response = HTMLResponse(tpl.render(request=request, static_urls=static_urls, hub_nouns=_nouns("hub"), company_nouns=_nouns("company"), json=json, **ctx),
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
        if not cmd.is_write:
            # Form rendering performs internal reads after a submitted write.
            # Its retry key belongs to that write, never to company/show lookups.
            ctx = ctx.model_copy(update={'idempotency_key': None})
        result = run_command(cmd, raw, ctx, cred, company, "option" if company else "none", dry_run)
        if name == "company show":
            request.state.workbench_company = result
        return result

    def annotations(company_id, noun, record_id, shown, role_view, cred):
        """Project target metadata and existing UI authority; commands own all data access."""
        if not company_id or not record_id:
            return None
        from bookflow.company.records import target_types
        record_type = registry.noun_meta(noun).get("record_type")
        if record_type not in target_types():
            return None
        shown = shown or {}
        key = shown.get("company_id") if noun == "company" else shown.get("id")
        key = key or (company_id if noun == "company" else record_id)
        if key == "self":
            return None
        allowed = {
            name: bool((cmd := registry.get(name)) and _role_allows(cmd, role_view or {}, hub_admin=cred.hub_admin))
            for name in ("note add", "note edit", "note list", "attachment add", "attachment get",
                         "attachment list", "attachment unlink", "activity")
        }
        return {"company": company_id, "target": {"record_type": record_type, "record_id": key},
                "allowed": allowed, "writes": [name for name in allowed if registry.get(name).is_write]}

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
        reference = _form_reference(definition, owner_noun, field)
        if reference is None:
            return page_error(request, BookflowError("E_USAGE", message="unknown form reference"))
        targets = reference.target_nouns
        requested_target = request.query_params.get("target")
        if requested_target is None and len(targets) > 1:
            requested_target = request.query_params.get("f:name_type")
        target = requested_target.replace("_", "-") if requested_target else (targets[0] if len(targets) == 1 else None)
        if target not in targets:
            return page_error(request, BookflowError("E_USAGE", message="invalid reference target"))
        target_definition = registry.noun_meta(target).get("definition")
        list_command = registry.get(target_definition.query_command) if target_definition else None
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
        if not query and "." in field:
            leaf_name = field.rsplit(".", 1)[-1]
            query = next(
                (
                    value
                    for key, value in request.query_params.multi_items()
                    if key.startswith("c:") and key.endswith(f":{leaf_name}")
                ),
                "",
            )
        query = query.strip()
        if not query:
            return HTMLResponse("", headers={"Cache-Control": "no-store"})
        if len(query) > 200:
            return page_error(request, BookflowError(
                "E_VALIDATION",
                details={"fields": [{"field": "query", "problem": "must be at most 200 characters"}]},
            ))
        if getattr(reference, 'owned_collection', None) == 'shipping_addresses':
            customer = request.query_params.get('f:customer')
            if not customer:
                return HTMLResponse('', headers={'Cache-Control': 'no-store'})
            try:
                party = run(request, 'customer show', {'customer': customer}, company_id)
            except BookflowError as err:
                return page_error(request, err)
            options = []
            for address in party.get('shipping_addresses', []):
                label = ' · '.join(str(address[key]) for key in ('label', 'line1', 'city') if address.get(key))
                if address.get('active', True) and query.casefold() in label.casefold():
                    options.append(f'<option value="{escape(address["id"], quote=True)}" label="{escape(label, quote=True)}"></option>')
            return HTMLResponse(''.join(options[:25]), headers={'Cache-Control': 'no-store'})
        if getattr(reference, "child_units", False):
            component_selector = next(
                (
                    value
                    for key, value in request.query_params.multi_items()
                    if key.startswith("c:") and (key.endswith(":component_item_id") or
                        (owner_noun in ('invoice', 'sales-receipt') and key.endswith(":item")))
                ),
                None,
            )
            if not component_selector:
                return HTMLResponse("", headers={"Cache-Control": "no-store"})
            try:
                component = run(
                    request,
                    "item show",
                    {"item": component_selector},
                    company_id,
                )
                unit_set_id = component.get("unit_of_measure_set_id")
                if not component.get("active", True) or not unit_set_id:
                    return HTMLResponse("", headers={"Cache-Control": "no-store"})
                unit_set = run(
                    request,
                    "unit-of-measure show",
                    {"unit_of_measure": unit_set_id},
                    company_id,
                )
            except BookflowError as err:
                if err.code in ("E_RECORD_NOT_FOUND", "E_INACTIVE_REFERENCE"):
                    return HTMLResponse("", headers={"Cache-Control": "no-store"})
                return page_error(request, err)
            normalized_query = query.casefold()
            options = []
            for unit in unit_set.get("units", []):
                searchable = " ".join(
                    str(unit.get(key, ""))
                    for key in ("id", "name", "abbreviation")
                ).casefold()
                if not unit.get("active", True) or normalized_query not in searchable:
                    continue
                stable_id = str(unit["id"])
                label = f"{unit_set['name']} · {unit['name']} ({unit['abbreviation']})"
                options.append(
                    f'<option value="{escape(stable_id, quote=True)}" '
                    f'label="{escape(label, quote=True)}"></option>'
                )
                if len(options) == 25:
                    break
            return HTMLResponse("".join(options), headers={"Cache-Control": "no-store"})
        try:
            output = run(request, list_command.name, {"query": query, "projection": "reference", "limit": 25}, company_id)
        except BookflowError as err:
            return page_error(request, err)
        relationship_versions: dict[str, int] = {}
        if owner_noun == "customer" and field == "vendor":
            customer_selector = request.query_params.get("f:customer")
            if customer_selector:
                try:
                    customer = run(
                        request,
                        "customer show",
                        {"customer": customer_selector},
                        company_id,
                    )
                except BookflowError as err:
                    return page_error(request, err)
                relationship_versions = {
                    str(link["vendor_id"]): int(link["version"])
                    for link in customer.get("vendor_links", [])
                    if isinstance(link, dict)
                }
        target_definition = registry.noun_meta(target).get("definition")
        identifier = target_definition.identifier if target_definition is not None else "id"
        options = []
        for row in output.get("items", [])[:25]:
            stable_id = row.get(identifier) or row.get("id")
            if stable_id is None or not row.get("active", True):
                continue
            label = str(row["label"])
            relationship_version = relationship_versions.get(str(stable_id), "")
            options.append(
                f'<option value="{escape(str(stable_id), quote=True)}" '
                f'label="{escape(label, quote=True)}" '
                f'data-version="{escape(str(row.get("version", "")), quote=True)}" '
                f'data-link-version="{escape(str(relationship_version), quote=True)}"></option>'
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
        meta = registry.noun_meta(noun)
        definition = meta.get("definition")
        cmd = registry.get(definition.query_command if definition is not None and company_id else f"{noun} list")
        if cmd is None:
            cmd = registry.get(f"{noun} query")
        if cmd is not None and any(field.is_required() for field in cmd.input_model.model_fields.values()):
            # Record-scoped lists need a target, not an invalid empty invocation.
            return form_page(request, company_id, noun, cmd.verb, None)
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
        if company_id and "limit" in cmd.input_model.model_fields:
            try:
                limit = int(request.query_params.get("limit", "50"))
            except ValueError:
                return page_error(request, BookflowError("E_VALIDATION", details={"fields": [{"field": "limit", "problem": "must be an integer"}]}))
            raw["limit"] = limit
            if "projection" in cmd.input_model.model_fields:
                raw["projection"] = "summary"
            if request.query_params.get("cursor"):
                raw["cursor"] = request.query_params["cursor"]
        for field in ("query", "sort", "direction", "date_from", "date_to", "status", "from_currency", "customer", "number"):
            value = request.query_params.get(field)
            if value and field in cmd.input_model.model_fields:
                raw[field] = value
        filters = [value for value in request.query_params.getlist("filter") if value.strip()]
        if filters and "filter" in cmd.input_model.model_fields:
            raw["filter"] = filters
        if include and "include_inactive" in cmd.input_model.model_fields:
            raw["include_inactive"] = True
        try:
            out = run(request, cmd.name, raw, company_id if cmd.scope == "company" else None)
        except BookflowError as e:
            if e.code == "E_QUERY_STALE":
                restart = request.url.path + "?" + urlencode([(k, v) for k, v in request.query_params.multi_items() if k != "cursor"])
                return render("error.html", request, error={**e.to_dict(), "message": "The list changed while you were browsing. Restart to see current results."}, restart_url=restart)
            return page_error(request, e)
        meta = registry.noun_meta(noun)
        items = out.get("items", [])
        definition = meta.get("definition")
        columns = (["number", "date", "memo", "total", "status"] if noun == "journal" else
                   ["number", "date", "customer_name", "due_date", "total", "status"] if noun in ('invoice', 'sales-receipt') else
                   ["date", "from_currency", "to_currency", "rate", "source", "version"] if noun == "rate" else
                   list(definition.summary_columns) if definition is not None else list_columns(items))
        column_text = request.query_params.get("columns", "")
        if column_text and definition is not None:
            requested_columns = [field.strip() for field in column_text.split(",") if field.strip()]
            allowed_columns = (*definition.summary_columns, definition.display_field, "id", "version", "active")
            unknown_columns = [field for field in requested_columns if field not in allowed_columns]
            if not requested_columns or unknown_columns or len(requested_columns) != len(set(requested_columns)):
                return page_error(request, BookflowError(
                    "E_LIST_FILTER",
                    details={
                        "problem": "columns must be a unique comma-separated selection",
                        "unknown": unknown_columns,
                        "allowed": list(allowed_columns),
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
            rate_filters=raw if noun == "rate" else None,
            sales_filters=raw if noun in ('invoice', 'sales-receipt') else None,
            filters=filters,
            selected_sort=raw.get("sort", ""),
            selected_direction=raw.get("direction", "asc"),
            selected_columns=",".join(columns),
            next_url=(request.url.path + "?" + urlencode([(k, v) for k, v in request.query_params.multi_items() if k != "cursor"] + [("cursor", out["next_cursor"])])) if out.get("next_cursor") else None,
            extra={k: v for k, v in out.items() if k not in ("items", "next_cursor", "projection")},
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
        show_selector = _record_selector(show, command_noun)
        raw = {show_selector: record_id} if show_selector else {}
        try:
            if command_noun in ("journal", "invoice", "sales-receipt") and request.query_params.get("revision_number"):
                try:
                    raw["revision_number"] = int(request.query_params["revision_number"])
                except ValueError:
                    raise BookflowError("E_VALIDATION", details={"fields": [{"field": "revision_number", "problem": "must be an integer"}]}) from None
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
                 if c.verb not in ("list", "show", "new", "create") and _role_allows(c, role_view, hub_admin=cred.hub_admin)]
        if command_noun == "customer":
            verbs = [
                command
                for command in verbs
                if not (
                    (command.verb == "link-vendor" and out.get("linked_vendor_id"))
                    or (command.verb == "unlink-vendor" and not out.get("linked_vendor_id"))
                    or (command.verb == "activate" and out.get("active"))
                    or (command.verb == "deactivate" and not out.get("active"))
                )
            ]
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
        definition = meta.get("definition")
        display_field = definition.display_field if definition is not None else meta.get("display_field")
        record_title = next(
            (
                str(out[field])
                for field in (display_field, "full_name", "display_name", "name", "code", "summary")
                if field and out.get(field) not in (None, "")
            ),
            record_id,
        )
        if command_noun in ("journal", "invoice", "sales-receipt"):
            record_title = out["revision"]["number"]
        contact_copy = None
        if company_id is not None and command_noun in ("customer", "vendor"):
            target_noun = "vendor" if command_noun == "customer" else "customer"
            linked_field = "linked_vendor_id" if command_noun == "customer" else "linked_customer_id"
            update = registry.get(f"{target_noun} update")
            if (
                out.get(linked_field)
                and update is not None
                and _role_allows(update, role_view, hub_admin=cred.hub_admin)
            ):
                contact_copy = {
                    "url": f"/c/{company_id}/{command_noun}/{record_id}/copy-contact",
                    "label": f"copy contact details to linked {target_noun}",
                }
        workspace = None
        if company_id is not None and command_noun == "customer":
            try:
                jobs = run(request, definition.query_command, {"projection": "summary", "filter": [f"parent_id={out['id']}"], "limit": 25}, company_id)
                source_ids = {value for key, value in out.items() if key.endswith("_source_id") and value and value != out["id"]}
                source_names = {source: run(request, "customer show", {"customer": source}, company_id)["full_name"] for source in source_ids}
            except BookflowError as err:
                return page_error(request, err)
            create = registry.get("customer create")
            workspace = {
                "sections": W.customer_sections(out), "jobs": jobs["items"],
                "jobs_url": f"/c/{company_id}/customer?" + urlencode({"filter": f"parent_id={out['id']}"}),
                "more_jobs": bool(jobs.get("next_cursor")),
                "add_job": f"/c/{company_id}/customer/create?" + urlencode({"parent": out["id"]}) if create and out.get("active") and _role_allows(create, role_view, hub_admin=cred.hub_admin) else None,
                "inheritance": [{"field": W.label(key.removesuffix("_source_id")), "id": value, "name": source_names[value]} for key, value in out.items() if key.endswith("_source_id") and value in source_names],
            }
        return render("record.html", request, company_id=company_id, noun=noun, record_id=record_id, record=visible_record, record_title=record_title, audit=audit, meta=meta, verbs=verbs,
                      sale=Sales.detail_context(out, company_id) if command_noun in ('invoice', 'sales-receipt') else None,
                      audit_undo=audit_undo, contact_copy=contact_copy, workspace=workspace,
                      annotations=annotations(company_id, noun, record_id, out, role_view, cred),
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

    def form_page(
        request: Request,
        company_id: str | None,
        noun: str,
        verb: str,
        record_id: str | None,
        result: dict | None = None,
        error: dict | None = None,
        preview: bool = False,
        attempted: dict[str, str] | None = None,
        workflow_note: str | None = None,
        report_input: dict | None = None,
    ):
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
        attempted = attempted or {}
        source_report_watermark = attempted.get("_source_report_watermark", request.query_params.get("source_report_watermark"))
        if source_report_watermark is not None and (not source_report_watermark.isascii() or not source_report_watermark.isdigit() or len(source_report_watermark) > 20):
            source_report_watermark = None
        if noun == "report" and not cmd.is_write and not attempted:
            for field in cmd.input_model.model_fields:
                value = request.query_params.get("f:" + field, request.query_params.get(field))
                if value is not None:
                    attempted["f:" + field] = value
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
            originals = {
                **_editable_values(noun, shown),
                "expected_version": shown.get(field),
                **({ident: record_id} if ident else {}),
            }
        elif cmd.version_source and record_id is None:
            return page_error(request, BookflowError("E_USAGE", message="open this update from a record page"))
        originals = originals or {}
        if noun in ('invoice', 'sales-receipt') and verb == 'history' and record_id is not None:
            originals[noun.replace('-', '_')] = record_id
            if not attempted and result is None:
                try:
                    raw_history = {noun.replace('-', '_'): record_id,
                        'limit': int(request.query_params.get('limit', '50'))}
                    if request.query_params.get('cursor'):
                        raw_history['cursor'] = request.query_params['cursor']
                    result = run(request, cmd.name, raw_history, company_id)
                except ValueError:
                    return page_error(request, BookflowError('E_VALIDATION', details={'fields': [{'field': 'limit', 'problem': 'must be an integer'}]}))
                except BookflowError as err:
                    return page_error(request, err, restart_url=request.url.path)
        if cmd.name == "rate set" and record_id is not None:
            try:
                shown = run(request, "rate show", {"rate_id": record_id}, company_id)
            except BookflowError as err:
                return page_error(request, err)
            originals = {key: shown[key] for key in ("date", "from_currency", "rate")}
            originals["expected_version"] = shown["version"]
        if noun == "register" and shown and shown.get("revision", {}).get("lines"):
            from bookflow.adapters.workbench.register import edit_projection
            try:
                account = run(request, "account show", {"account": shown["revision"]["lines"][0]["account_id"]}, company_id)
                projection = edit_projection(shown, account, lambda row: _reference_label("account", row, authorized_company or {}))
            except BookflowError as err:
                return page_error(request, err)
            if projection:
                originals = projection["payload"]
            originals["custom_fields"] = _custom_value_map(shown["revision"].get("custom_fields"))
        if company_id is not None and cmd.name == "customer create" and request.query_params.get("parent") and not attempted:
            try:
                parent = run(request, "customer show", {"customer": request.query_params["parent"]}, company_id)
            except BookflowError as err:
                return page_error(request, err)
            attempted["f:parent_id"] = parent["id"]
            workflow_note = f"Add a job under {parent['full_name']}. Unset commercial defaults inherit from its ancestors. Contact and shipping address source controls choose inherited or owned details; review these before saving."
        if company_id is not None and record_id is not None and cmd.name == "other-name convert":
            try:
                source = run(request, "other-name show", {"other_name": record_id}, company_id)
            except BookflowError as err:
                return page_error(request, err)
            originals.update({"other_name": record_id, "expected_version": source["version"]})
        if company_id is not None and record_id is not None and cmd.name in (
            "customer link-vendor",
            "customer unlink-vendor",
        ):
            try:
                customer = run(request, "customer show", {"customer": record_id}, company_id)
            except BookflowError as err:
                return page_error(request, err)
            originals.update({
                "customer": record_id,
                "expected_customer_version": customer["version"],
            })
            vendor_id = (
                attempted.get("f:vendor")
                or request.query_params.get("vendor")
                or customer.get("linked_vendor_id")
            )
            if vendor_id:
                try:
                    vendor = run(request, "vendor show", {"vendor": vendor_id}, company_id)
                except BookflowError as err:
                    if not (
                        error is not None
                        and attempted.get("f:vendor")
                        and err.code in ("E_RECORD_NOT_FOUND", "E_INACTIVE_REFERENCE")
                    ):
                        return page_error(request, err)
                else:
                    originals.update({
                        "vendor": vendor["id"],
                        "expected_vendor_version": vendor["version"],
                    })
                    relationship = _matching_vendor_link(customer, vendor["id"])
                    if relationship is not None:
                        originals["expected_link_version"] = relationship["version"]
        F.project_input_values(cmd.input_model, originals)
        meta = registry.noun_meta(noun)
        definition = meta.get("definition")
        if definition is not None and definition.custom_fields:
            originals["custom_fields"] = _custom_value_map(originals.get("custom_fields"))
        described = F.describe_fields(noun, verb, cmd.input_model, originals, attempted)
        sales_form = noun in ('invoice', 'sales-receipt') and verb in ('post', 'update')
        if sales_form:
            described = [leaf for leaf in described if leaf['path'] != 'expected_facts_fingerprint']
        if cmd.name in S.COMMANDS:
            # The visible filter form always starts fresh; continuation has its
            # own immutable filter fields and signed cursor in a separate form.
            described = [leaf for leaf in described if leaf["path"] != "cursor"]
        for index, leaf in enumerate(described, 1):
            leaf["index"] = index
            if noun == "customer":
                leaf["label"] = W.label(leaf["path"])
        selector = _record_selector(cmd, noun) if record_id is not None else None
        for leaf in described:
            if leaf["path"] == selector:
                leaf["pinned"] = True
        reference_definition = definition or meta.get('form_definition')
        if company_id is not None and reference_definition is not None:
            def creation_targets(targets: tuple[str, ...]) -> list[dict[str, Any]]:
                choices = []
                for candidate in targets:
                    create_command = registry.get(f"{candidate} create")
                    if create_command is not None and _role_allows(create_command, authorized_company or {}, hub_admin=cred.hub_admin):
                        target_definition = registry.noun_meta(candidate).get("definition")
                        return_token = secrets.token_urlsafe(24)
                        choices.append({"target": candidate, "label": target_definition.singular_label if target_definition else candidate,
                                        "return_token": return_token,
                                        "url": f"/c/{company_id}/{candidate}/create?" + urlencode({"return_token": return_token, "return_target": candidate})})
                return choices
            for leaf in described:
                if leaf["kind"] == "collection":
                    _decorate_collection_references(
                        leaf["collection"],
                        definition=reference_definition,
                        noun=noun,
                        company_id=company_id,
                        path=leaf["path"],
                        create_targets=creation_targets,
                    )
            for leaf in described:
                reference = _form_reference(reference_definition, noun, leaf["path"])
                if reference is None:
                    continue
                targets = reference.target_nouns
                target, discriminator = _reference_target(targets, noun, originals, attempted)
                value = F.selected_value(leaf["path"], originals, attempted)
                current = None
                owned_addresses = getattr(reference, 'owned_collection', None) == 'shipping_addresses'
                if owned_addresses and value:
                    customer = F.selected_value('customer', originals, attempted)
                    if customer:
                        try:
                            party = run(request, 'customer show', {'customer': customer}, company_id)
                        except BookflowError as err:
                            if err.code != 'E_RECORD_NOT_FOUND':
                                return page_error(request, err)
                        else:
                            address = next((row for row in party.get('shipping_addresses', []) if row['id'] == value), None)
                            if address:
                                current = dict(id=value, label=' · '.join(str(address[key]) for key in ('label', 'line1', 'city') if address.get(key)), active=address.get('active', True))
                elif target is not None and value:
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
                                "version": row.get("version"),
                                "link_version": originals.get("expected_link_version"),
                            }
                add_targets = [] if owned_addresses else creation_targets(targets)
                version_field = None
                link_version_field = None
                include_fields = []
                if cmd.name == "customer link-vendor":
                    if leaf["path"] == "customer":
                        version_field = "expected_customer_version"
                    elif leaf["path"] == "vendor":
                        version_field = "expected_vendor_version"
                        link_version_field = "expected_link_version"
                        include_fields.append("customer")
                leaf["reference"] = {
                    "targets": targets,
                    "target": target,
                    "discriminator": discriminator,
                    "suggestion_url": f"/c/{company_id}/_references/{noun}/{leaf['path']}",
                    "current": current,
                    "add_targets": add_targets,
                    "version_field": version_field,
                    "link_version_field": link_version_field,
                    "include_fields": include_fields,
                }
        runtime_fields = []
        reference_values: dict[str, dict[str, Any]] = {}
        reference_cache: dict[tuple[str, str], dict[str, Any]] = {}
        presentation_attempt = dict(attempted)
        def current_reference(target: str, value: str) -> dict[str, Any]:
            key = (target, value)
            if key not in reference_cache:
                target_meta = registry.noun_meta(target)
                try:
                    row = run(request, f"{target} show", {target_meta["identifier"]: value}, company_id)
                except BookflowError as err:
                    if err.code not in ("E_RECORD_NOT_FOUND", "E_INACTIVE_REFERENCE"):
                        raise
                    row = {}
                reference_cache[key] = row
            return reference_cache[key]

        def collect_references(schema: dict[str, Any], values: list[Any], wire_path: str, source_path: str | None = None) -> None:
            item = schema["item"]
            source_path = source_path or wire_path
            source_prefix = "c:" + source_path + ":"
            source_indexes = list(dict.fromkeys(key[len(source_prefix):].split(":", 1)[0] for key in presentation_attempt if key.startswith(source_prefix)))
            for index, value in enumerate(values):
                if item["kind"] != "object" or not isinstance(value, dict):
                    continue
                for child in item["fields"]:
                    path = f"{wire_path}:{index}:{child['name']}"
                    source_index = source_indexes[index] if index < len(source_indexes) else str(index)
                    original_path = f"{source_path}:{source_index}:{child['name']}"
                    for prefix in ("label:c:", "ref-state:c:"):
                        if prefix + original_path in presentation_attempt:
                            attempted[prefix + path] = presentation_attempt[prefix + original_path]
                    current_value = value.get(child["name"])
                    if child["kind"] == "collection":
                        collect_references(child["collection"], current_value or [], path, original_path)
                    elif child.get("reference") and current_value:
                        target = child["reference"]["target"]
                        if child["reference"].get("include_closest"):
                            component = current_reference("item", value.get("component_item_id", ""))
                            unit_set = current_reference("unit-of-measure", component["unit_of_measure_set_id"]) if component.get("unit_of_measure_set_id") else {}
                            row = next((unit for unit in unit_set.get("units", []) if unit["id"] == current_value), {})
                            label = f"{unit_set.get('name', '')} · {row.get('name', '')} ({row.get('abbreviation', '')})" if row else "Unavailable unit"
                        else:
                            row = current_reference(target, str(current_value))
                            label = _reference_label(target, row, authorized_company or {}) if row else "Unavailable record"
                        reference_values["c:" + path] = {"id": current_value, "label": label, "active": row.get("active", True), "version": row.get("version")}
        try:
            for leaf in described:
                if leaf["kind"] == "collection":
                    collect_references(leaf["collection"], leaf["collection"]["values"], leaf["path"])
        except BookflowError as err:
            return page_error(request, err)
        runtime_scope = (noun.replace('-', '_') if noun in ('invoice', 'sales-receipt') and verb in ('post', 'update') else
                         "journal_entry" if noun in ("journal", "register") and verb in ("post", "update") else
                         definition.record_type if definition is not None and definition.runtime_field_provider == "custom-fields" else None)
        if company_id is not None and runtime_scope:
            try:
                definitions = run(
                    request,
                    "custom-field list",
                    {"filter": [f"target_type={runtime_scope}"]},
                    company_id,
                ).get("items", [])
            except BookflowError as err:
                return page_error(request, err)
            runtime_fields = F.custom_field_descriptors(definitions, originals.get("custom_fields"), attempted, update=verb == "update")
            described = [leaf for leaf in described if leaf["path"] not in ("custom_fields", "custom_field_kinds")]
        return_token = attempted.get("_return_token") or request.query_params.get("return_token")
        return_target = attempted.get("_return_target") or request.query_params.get("return_target")
        return_context = None
        if (
            verb == "create"
            and return_target == noun
            and return_token is not None
            and 16 <= len(return_token) <= 200
        ):
            return_context = {"token": return_token, "target": return_target}
        return render("form.html", request, company_id=company_id, noun=noun, verb=verb, cmd=cmd, leaves=described, originals=originals,
                      attempted=attempted, record_id=record_id, runtime_fields=runtime_fields,
                      captured_custom_fields=(shown or {}).get("revision", {}).get("custom_fields", []),
                      captured_foreign_lines=[line for line in (shown or {}).get("revision", {}).get("lines", []) if line.get("original_amount")],
                      annotations=annotations(company_id, noun, record_id, shown, authorized_company, cred),
                      ctx_fields=F.context_fields(cmd), result=result, error=error,
                      sales_form=sales_form, sales_scope=cred.token_id,
                      sales_fingerprint=(result.get('facts_fingerprint', '') if preview and result else
                          '' if error and error.get('code') == 'E_PREVIEW_STALE' else attempted.get('f:expected_facts_fingerprint', '')),
                      sale=Sales.detail_context(result, company_id, preview=preview) if result and noun in ('invoice', 'sales-receipt') and 'revision' in result else None,
                      sales_history=result if noun in ('invoice', 'sales-receipt') and verb == 'history' else None,
                      statement=S.view(result, report_input, company_id) if result and report_input is not None and cmd.name in S.COMMANDS else None,
                      source_report_watermark=source_report_watermark,
                      preview=preview, get=F.get_path, form_value=F.form_value,
                      return_context=return_context, workflow_note=workflow_note,
                      reference_values=reference_values,
                      form_groups=W.customer_form_groups(described) if noun == "customer" and verb in ("create", "update") else None)

    def contact_copy_page(
        request: Request,
        company_id: str,
        source_noun: str,
        source_id: str,
    ):
        """Open an ordinary endpoint update prefilled from its linked endpoint."""
        if source_noun not in ("customer", "vendor"):
            return page_error(request, BookflowError("E_USAGE", message="contact copy is available for linked customers and vendors"))
        target_noun = "vendor" if source_noun == "customer" else "customer"
        linked_field = "linked_vendor_id" if source_noun == "customer" else "linked_customer_id"
        try:
            source = run(
                request,
                f"{source_noun} show",
                {source_noun.replace("-", "_"): source_id},
                company_id,
            )
            target_id = source.get(linked_field)
            if not target_id:
                raise BookflowError(
                    "E_RECORD_IN_USE",
                    message=f"{source_noun} is not linked to a {target_noun}",
                )
            target = run(
                request,
                f"{target_noun} show",
                {target_noun.replace("-", "_"): target_id},
                company_id,
            )
        except BookflowError as err:
            return page_error(request, err)
        attempted: dict[str, str] = {}
        copied = (
            "contact", "alt_contact", "phone", "alt_phone", "fax", "email",
            "cc_email", "website",
        )
        for field in copied:
            if source.get(field) is None:
                if target.get(field) is not None:
                    attempted[f"clear:{field}"] = "1"
            else:
                attempted[f"f:{field}"] = str(source[field])
        if target_noun == "customer" and target.get("contact_mode") == "inherit":
            # The copied collection is complete.  Selecting ownership is an
            # explicit ordinary customer-update field; the domain remains the
            # authority for whether this transition is valid.
            attempted["f:contact_mode"] = "own"
        update_command = registry.get(f"{target_noun} update")
        if update_command is not None and "contacts" in update_command.input_model.model_fields:
            attempted.update(F.collection_attempt(
                update_command.input_model.model_fields["contacts"].annotation,
                "contacts",
                source.get("contacts", []),
                omit_stable_ids=True,
            ))
        return form_page(
            request,
            company_id,
            target_noun,
            "update",
            str(target["id"]),
            attempted=attempted,
            workflow_note=(
                f"Contact-copy preview from {source_noun} {source.get('display_name') or source.get('name') or source_id}. "
                f"This submits the ordinary versioned {target_noun} update command."
            ),
        )

    from bookflow.adapters.workbench.register import install as install_register
    install_register(app, render=render, run=run, credential=credential, page_error=page_error)

    @app.get("/hub/{noun}/{record_id}/{verb}", response_class=HTMLResponse)
    def hub_record_form(noun: str, record_id: str, verb: str, request: Request):
        return form_page(request, None, noun.replace("-", " "), verb, record_id)

    @app.get("/c/{company_id}/{noun}/{record_id}/{verb}", response_class=HTMLResponse)
    def company_record_form(company_id: str, noun: str, record_id: str, verb: str, request: Request):
        if verb == "copy-contact":
            return contact_copy_page(request, company_id, noun, record_id)
        return form_page(request, company_id, noun, verb, record_id)

    def submit(request: Request, company_id: str | None, noun: str, verb: str, record_id: str | None, form: dict[str, str]):
        cmd = registry.get(f"{noun} {verb}".strip())
        if cmd is None or cmd.local_only:
            return page_error(request, BookflowError("E_USAGE", message=f"unknown command {noun} {verb}".strip()))
        originals = json.loads(form.get("originals", "{}") or "{}")
        try:
            session_token = credential(request).token_id
            unresolved = [key.removeprefix("ref-state:") for key, value in form.items() if key.startswith("ref-state:") and value == "pending"]
            if unresolved:
                raise BookflowError("E_VALIDATION", details={"fields": [{"field": key, "problem": "Choose a matching record by name, or use Clear."} for key in unresolved]})
            raw, headers, preview = F.translate(cmd, form, originals if originals else None)
            if noun in ('invoice', 'sales-receipt') and verb in ('post', 'update'):
                if verb == 'update':
                    raw = Sales.preserve_line_origins(raw, originals)
                if preview:
                    raw.pop('expected_facts_fingerprint', None)
                    headers.pop('Idempotency-Key', None)
                elif not raw.get('expected_facts_fingerprint'):
                    raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'preview', 'problem': 'Preview these values before saving.'}]})
            if (cmd.version_source or cmd.name == "rate set") and record_id is not None and "expected_version" not in raw and form.get("f:expected_version"):
                raw["expected_version"] = int(form["f:expected_version"])
            selector = _record_selector(cmd, noun) if record_id is not None else None
            if selector:
                raw.setdefault(selector, record_id)
            out = run(request, cmd.name, raw, company_id if cmd.scope == "company" else None, headers, dry_run=preview)
        except BookflowError as e:
            # a credential failure is not a form problem: it must carry its own status, or a page POST
            # without the workbench header would answer 200 and look accepted (row 3 plan, Authentication)
            if e.code in ("E_UNAUTHENTICATED", "E_WORKBENCH_HEADER"):
                return page_error(request, e)
            return form_page(request, company_id, noun, verb, record_id, error=e.to_dict(), attempted=form)
        if preview:
            return form_page(request, company_id, noun, verb, record_id, result=out, preview=True, attempted=form)
        if noun in ('invoice', 'sales-receipt') and verb in ('history', 'query'):
            return form_page(request, company_id, noun, verb, record_id, result=out, attempted=form)
        if noun == "report" and not cmd.is_write:
            return form_page(request, company_id, noun, verb, record_id, result=out, attempted=form,
                             report_input=cmd.input_model.model_validate(raw).model_dump())
        return_token = form.get("_return_token")
        return_target = form.get("_return_target")
        created_id = _output_identifier(noun, registry.noun_meta(noun), out)
        created_version = _output_version(noun, out)
        if (
            verb == "create"
            and return_target == noun
            and return_token is not None
            and 16 <= len(return_token) <= 200
            and created_id is not None
        ):
            payload = json.dumps({
                "type": "bookflow-reference-created",
                "token": return_token,
                "target": noun,
                "id": created_id,
                "version": created_version,
                "label": _reference_label(noun, out.get(noun, out), {}),
            }).replace("</", "<\\/")
            record_url = f"/c/{company_id}/{noun}/{created_id}" if company_id else f"/hub/{noun.replace(' ', '-')}/{created_id}"
            return HTMLResponse(
                "<!doctype html><meta charset=utf-8><title>Created</title>"
                f"<p>Created. <a href=\"{escape(record_url, quote=True)}\">Open record</a></p>"
                f"<script>const message={payload};if(window.opener){{window.opener.postMessage(message,location.origin);window.close();}}</script>",
                headers={"Cache-Control": "no-store"},
            )
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
