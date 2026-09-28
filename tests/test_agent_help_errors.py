"""R86: help and errors a fresh agent can act on first time (notes/blind-trials-20260927.md)."""

import pytest

from tests.test_row3_host import hosted  # noqa: F401 - fixture

from bookflow.core.errors import BookflowError

CO = "Demo Plumbing Co"


def _refusal(client, name, body, **context):
    with pytest.raises(BookflowError) as caught:
        client.run(name, body, company=CO, **context)
    return caught.value


def test_unknown_field_names_the_accepted_fields_and_the_query_options(client):
    error = _refusal(client, "customer query", {"filters": {"name_contains": "Riverside"}})
    assert error.code == "E_VALIDATION"
    [field] = error.details["fields"]
    assert field["field"] == "filters"
    assert "query" in field["accepted_fields"] and "filter" in field["accepted_fields"]
    assert "accepted fields: query" in field["problem"]
    assert "customer query options" in error.details["hint"]

    ledger = _refusal(client, "report general-ledger",
                      {"date_from": "2026-09-01", "date_to": "2026-09-27", "accounts": ["Sales Tax Payable"]})
    [field] = ledger.details["fields"]
    assert field["field"] == "accounts" and "account" in field["accepted_fields"]
    assert "hint" not in ledger.details


def test_money_errors_name_the_accepted_shape_not_validator_internals(client):
    for value in ({"amount": "22.80", "currency": "USD"}, 22.8):
        error = _refusal(client, "inventory adjust",
                         {"item": "Brass Shutoff Valve", "date": "2026-09-13", "quantity_change": "2",
                          "value_change": value, "adjustment_account": "Opening Balance Equity"},
                         dry_run=True, reason="Record truck stock")
        assert error.details["fields"] == [{"field": "value_change", "problem": error.details["fields"][0]["problem"]}]
        problem = error.details["fields"][0]["problem"]
        assert 'a decimal string like "22.80"' in problem
        assert "function-after" not in str(error.to_dict()) and "MoneyInput" not in str(error.to_dict())


def test_missing_dates_say_what_to_pass(client):
    error = _refusal(client, "report general-ledger", {"account": "Checking"})
    problems = {f["field"]: f["problem"] for f in error.details["fields"]}
    assert problems == {"date_from": 'required: a date like "2026-09-27" (YYYY-MM-DD)',
                        "date_to": 'required: a date like "2026-09-27" (YYYY-MM-DD)'}


def test_union_branch_tags_never_reach_the_field_path(client):
    error = _refusal(client, "payment receive",
                     {"customer": "Commercial Example Customer", "date": "2026-09-27", "amount": "200.00",
                      "operation_key": "r86-branch", "applications": {"mode": "inline", "items": [
                          {"invoice_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "amount_minor_units": 100}]}},
                     dry_run=True, reason="Record check")
    paths = {f["field"] for f in error.details["fields"]}
    assert "applications.items.0.invoice" in paths and "applications.items.0.invoice_id" in paths
    assert not any(".inline." in path for path in paths)
    unknown = next(f for f in error.details["fields"] if f["field"] == "applications.items.0.invoice_id")
    assert unknown["accepted_fields"] == ["invoice", "expected_version", "amount"]


def test_help_views_carry_one_complete_worked_example():
    from bookflow.adapters.mcp.catalog import command_help
    from bookflow.adapters.mcp.envelopes import validate
    from bookflow.core import registry
    registry.load_all()
    for name in ("customer query", "invoice query", "payment receive", "payment suggest", "bill pay",
                 "inventory adjust", "register query", "report general-ledger", "reconcile opening start",
                 "reconcile start", "deposit post", "sales-tax liability"):
        cmd = registry.get(name)
        for view in ("usage", "input_schema", "full"):
            doc = command_help(name, view)
            example = doc["example"]
            assert example["command"] == name and doc["cli_example"].startswith("bookflow " + name)
            validate("bookflow_run", example)
            cmd.input_model.model_validate(example["input"])
        assert "```json" in command_help(name)["documentation"]
    receive = command_help("payment receive")["example"]["input"]
    assert receive["applications"]["items"][0]["amount"] == "147.00"  # the example takes a 3.00 early-payment discount (R132)
    assert command_help("customer query")["example"]["input"]["query"] == "Riverside"
    assert "opening_id" not in command_help("reconcile start")["example"]["input"]
    assert "example" not in command_help("invoice post", "output_schema")


@pytest.mark.parametrize("name", ["customer query", "invoice query", "payment receive", "payment apply",
                                  "payment suggest", "reconcile start"])
def test_changed_cli_examples_parse_to_their_documented_input(name, monkeypatch, capsys):
    import json
    import shlex
    import sys
    from bookflow.adapters.cli.app import main
    from bookflow.core import dispatch
    from bookflow.documentation.examples import EXAMPLES

    def execute(cmd, raw, ctx, **kwargs):
        assert cmd.name == name
        assert cmd.input_model.model_validate(raw).model_dump() == \
            cmd.input_model.model_validate(EXAMPLES[name].input).model_dump()
        return {"example_parsed": True}

    monkeypatch.setattr(dispatch, "run", execute)
    monkeypatch.setattr(sys, "argv", shlex.split(EXAMPLES[name].invocation))
    main()
    assert json.loads(capsys.readouterr().out) == {"example_parsed": True}


def test_list_commands_first_lines_make_the_plumbers_questions_findable():
    from bookflow.adapters.mcp.catalog import list_commands
    rows = {row["name"]: row["description"] for row in list_commands(limit=200)["commands"]}
    rows.update({row["name"]: row["description"] for prefix in ("payment", "sales-tax", "invoice", "register",
                                                                  "reconcile", "customer")
                 for row in list_commands(prefix=prefix, limit=200)["commands"]})
    assert 'strategy "exact_then_oldest"' in rows["payment receive"] and "oldest invoice first" in rows["payment receive"]
    assert "oldest invoices first" in rows["payment suggest"] and "applications.items" in rows["payment suggest"]
    assert "exact_then_oldest" in rows["payment apply"]
    assert rows["sales-tax liability"].startswith("How much sales tax is owed, by agency")
    assert "for a period" in rows["sales-tax liability"]
    assert 'For open/unpaid invoices set settlement to "open"' in rows["invoice query"]
    assert rows["payment invoices"].startswith("Open (unpaid) invoices a customer can pay")
    assert "omitted, the current fiscal year to today" in rows["register query"]
    assert "`reconcile opening start` first" in rows["reconcile start"]
    assert rows["reconcile opening start"].startswith("First step for an account never reconciled")
    assert "`customer query options`" in rows["customer query"]


def test_a_wrong_payment_method_names_the_list_command(client):
    error = _refusal(client, "payment receive",
                     {"customer": "Commercial Example Customer", "date": "2026-09-27", "amount": "10.00",
                      "operation_key": "r86-method", "payment_method": "Store Voucher"},
                     dry_run=True, reason="Record card payment")
    assert error.code == "E_RECORD_NOT_FOUND"
    assert error.details["list_command"] == "payment-method list"
    assert "`payment-method list`" in error.message


def test_reconcile_start_without_an_opening_names_the_first_step(client):
    checking = client.run("account show", {"account": "Checking"}, company=CO)
    account_id = checking.get("id") or checking["account"]["id"]
    # The account is named like any other account: by name or by ID.
    opening = client.run("reconcile opening start", {"operation_key": "r86-open", "account": "Checking",
        "opening_date": "2026-01-31", "entered_balance": "1250.00",
        "evidence": {"format": 1, "statement_reference": "January statement", "entered_text": None}},
        company=CO, dry_run=True, reason="Adopt checking")
    assert opening["draft"]["account_id"] == account_id
    misspelt = _refusal(client, "reconcile start", {"operation_key": "r86-recon", "account": "Chekcing",
        "statement_date": "2026-09-25", "ending_balance": "6111.27"}, dry_run=True, reason="Reconcile September")
    assert misspelt.code == "E_RECORD_NOT_FOUND" and misspelt.details["suggestions"] == ["Checking"]
    error = _refusal(client, "reconcile start",
                     {"operation_key": "r86-recon", "account": "Checking", "statement_date": "2026-09-25",
                      "ending_balance": "6111.27"}, dry_run=True, reason="Reconcile September")
    assert error.code == "E_VALIDATION"
    assert error.message.startswith("this account has no reconciliation opening yet; start one with `reconcile opening start`")
    assert error.details["next_command"] == "reconcile opening start"
    assert error.details["fields"][0]["field"] == "opening_id"
    both = _refusal(client, "reconcile start",
                    {"operation_key": "r86-recon", "account": account_id, "statement_date": "2026-09-25",
                     "ending_balance": "6111.27", "opening_id": account_id, "opening_draft_id": account_id},
                    dry_run=True, reason="Reconcile September")
    assert both.details["fields"] == [{"field": "input", "problem": "give opening_id or opening_draft_id, not both"}]


def test_permission_refusals_say_why_in_one_plain_line():
    from bookflow.core.errors import BookflowError as Error, explain_permission, permission_message
    role = Error("E_PERMISSION", details={"capability": "ledger.post", "required_role": "standard", "role": "member"})
    assert role.message == "This needs ledger.post at role standard or above; your role here is member."
    assert Error("E_PERMISSION", details={"capability": "token", "required_role": "hub_admin"}).message == \
        "Only an installation administrator may do this (token)."
    # A publication fence keeps its deliberately uninformative answer.
    fenced = {"stage": "publication", "outcome": "unknown"}
    assert Error("E_PERMISSION", details=fenced).message == "The acting user may not run this command here."
    assert "\n" not in permission_message({"reason": "unresolved_payment_evidence"})

    class Command:
        name, capability, required_role = "deposit post", "ledger.post", "standard"
    bare = explain_permission(Error("E_PERMISSION"), Command)
    assert bare.details == {"reason": "record_rule", "command": "deposit post", "capability": "ledger.post",
                            "required_role": "standard"}
    assert bare.message.startswith("`deposit post` was refused by a rule on a record or account it reads or changes.")
    assert "membership effective" in bare.message
    # A refusal that already names its reason, or its hidden cause, is never rewritten.
    named = explain_permission(Error("E_PERMISSION", details={"reason": "capability_not_activated"}), Command)
    assert named.details == {"reason": "capability_not_activated"}


def test_a_scrubbed_inner_refusal_reaches_the_caller_with_the_commands_requirement(client, monkeypatch):
    def refuse(*args, **kwargs):
        raise BookflowError("E_PERMISSION", details={})
    from bookflow.core import registry
    registry.load_all()
    monkeypatch.setattr(registry.get("payment invoices"), "plan",
                        lambda inp, ctx, s: refuse())
    error = _refusal(client, "payment invoices",
                     {"mode": "new_receipt", "customer": "Commercial Example Customer", "date": "2026-09-27"})
    assert error.code == "E_PERMISSION"
    assert error.details == {"reason": "record_rule", "command": "payment invoices", "capability": "ledger.read",
                             "required_role": "member"}
    assert error.message.startswith("`payment invoices` was refused")


def test_agent_writes_say_a_reason_is_needed_even_for_a_preview():
    from bookflow.adapters.mcp.catalog import command_help
    from bookflow.core.dispatch import AGENT_REASON_MESSAGE
    usage = command_help("invoice post")["context_usage"]
    assert "Agent writes require reason or an active directive, and so do their dry_run previews" in usage
    assert "dry-run previews included" in AGENT_REASON_MESSAGE


def test_hosted_refusals_carry_the_same_explanations(hosted, monkeypatch):
    """The MCP adapter runs commands on the host; its refusals are the host's HTTP documents."""
    from bookflow.core import registry
    registry.load_all()

    def refuse(*args, **kwargs):
        raise BookflowError("E_PERMISSION", details={})
    monkeypatch.setattr(registry.get("payment invoices"), "plan", refuse)
    denied = hosted.call("payment.invoices", {"mode": "new_receipt", "customer": "Commercial Example Customer",
                                              "date": "2026-09-27"}, company=hosted.company_id)
    assert denied.status_code == 403, denied.text
    body = denied.json()
    assert body["details"] == {"reason": "record_rule", "command": "payment invoices", "capability": "ledger.read",
                               "required_role": "member"}
    assert body["message"].startswith("`payment invoices` was refused")
    invalid = hosted.call("customer.query", {"filters": {"name_contains": "Riverside"}}, company=hosted.company_id)
    assert invalid.status_code == 422 and invalid.json()["details"]["fields"][0]["accepted_fields"][0] == "query"


def test_register_query_defaults_to_the_fiscal_year_and_pages_newest_first(client, monkeypatch):
    from datetime import datetime, timezone
    from bookflow.core import clock
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 9, 27, 18, tzinfo=timezone.utc))
    first = client.run("register query", {"account": "Checking", "limit": 7}, company=CO)
    assert first["metadata"]["period"] == {"date_from": "2026-01-01", "date_to": "2026-09-27"}

    def every(direction):
        rows, cursor = [], None
        while True:
            page = client.run("register query", {"account": "Checking", "limit": 7, "direction": direction,
                                                 **({"cursor": cursor} if cursor else {})}, company=CO)
            rows += page["rows"]
            cursor = page["next_cursor"]
            if not cursor:
                return rows, page
    oldest, _ = every("asc")
    newest, last = every("desc")
    assert len(oldest) > 7 and newest == list(reversed(oldest))
    assert last["totals"] == first["totals"]
    newest_first = client.run("register query", {"account": "Checking", "limit": 7, "direction": "desc"}, company=CO)
    error = _refusal(client, "register query", {"account": "Checking", "limit": 7, "cursor": newest_first["next_cursor"]})
    assert error.code == "E_VALIDATION" and error.details["fields"][0]["field"] == "cursor"


def test_invoice_query_filters_by_what_is_still_owed(client):
    from collections import Counter

    def statuses(**filters):
        page = client.run("invoice query", {"limit": 200, **filters}, company=CO)
        return Counter(row["settlement_current"]["status"] for row in page["items"]), page["items"]
    everything, _ = statuses()
    for settlement, expected in (("open", {"unpaid", "partial"}), ("unpaid", {"unpaid"}),
                                 ("partial", {"partial"}), ("paid", {"paid"})):
        found, _ = statuses(settlement=settlement)
        assert set(found) <= expected and sum(found.values()) == sum(everything[k] for k in expected), settlement
    # Paying an open invoice moves it to paid; taking the application back reopens it.
    _, commercial = statuses(settlement="open", customer="Commercial Example Customer")
    [invoice] = commercial
    due = invoice["settlement_current"]["due_minor_units"]
    paid = client.run("payment receive", {"customer": "Commercial Example Customer", "date": "2026-09-27",
        "amount": {"minor_units": due, "currency": "USD"}, "operation_key": "r86-settle", "payment_method": "Check",
        "applications": {"mode": "inline", "items": [{"invoice": invoice["id"],
            "expected_version": invoice["settlement_current"]["version"],
            "amount": {"minor_units": due, "currency": "USD"}}]}}, company=CO, reason="Record check")
    assert [row["id"] for row in statuses(settlement="paid")[1]].count(invoice["id"]) == 1
    assert invoice["id"] not in [row["id"] for row in statuses(settlement="open")[1]]
    application = paid["effect"]["applications"][0]
    current = client.run("invoice settlement", {"invoice": invoice["id"]}, company=CO)
    client.run("payment unapply", {"payment": paid["id"], "expected_version": paid["version"],
        "operation_key": "r86-unsettle", "applications": [{"application_id": application["application_id"],
            "invoice_expected_version": current["version"]}]}, company=CO, reason="Wrong invoice")
    assert invoice["id"] in [row["id"] for row in statuses(settlement="unpaid")[1]]


def test_a_wrong_command_name_suggests_the_closest_real_ones(client):
    from bookflow.adapters.mcp.catalog import command_help, list_commands
    with pytest.raises(BookflowError) as caught:
        command_help("invoice list")
    assert caught.value.code == "E_USAGE" and caught.value.details["suggestions"][0] == "invoice query"
    assert "`invoice query`" in caught.value.message
    with pytest.raises(BookflowError) as caught:
        client.run("sales tax liability", {"as_of": "2026-09-27"}, company=CO)
    assert caught.value.details["suggestions"][0] == "sales-tax liability"
    assert list_commands(prefix="reconcile begin")["suggestions"][0] == "reconcile start"
    assert "suggestions" not in list_commands(prefix="reconcile")


def test_item_receipt_reads_each_say_what_they_take():
    from bookflow.adapters.mcp.catalog import list_commands
    rows = {row["name"]: row["description"] for row in list_commands(prefix="item-receipt", limit=200)["commands"]}
    assert len({rows["item-receipt show"], rows["item-receipt query"], rows["item-receipt history"]}) == 3
    assert "`receipt`" in rows["item-receipt history"]


def test_deposit_replay_refusals_name_the_requirement_and_nothing_else(monkeypatch):
    """deposit_lifecycle.recover, deposit_operations.recover and deposit_operation_pages keep
    the capability, threshold and rule of a refusal (as main's `_refusal` does), never the
    identity, role or record."""
    from types import SimpleNamespace
    from bookflow.company import (deposit_dependency_history as history, deposit_lifecycle as lifecycle,
                                  deposit_operation_pages as pages, deposit_operations as operations)
    denial = {"capability": "customer-work", "required_role": "standard", "role": "member",
              "record": "01ARZ3NDEKTSV4RRFFQ69G5FAV"}
    public = {"capability": "customer-work", "required_role": "standard"}

    def deny(*args, **kwargs):
        raise BookflowError("E_PERMISSION", details=dict(denial))
    saved = {"id": "operation", "transaction_id": "deposit"}
    monkeypatch.setattr(operations, "find", lambda s, key: saved)
    monkeypatch.setattr(operations.rows, "rows", lambda *args, **kwargs: [{"transaction_id": "source"}])
    monkeypatch.setattr(operations.dependencies, "authorize", deny)
    inp = SimpleNamespace(operation_key="key")
    with pytest.raises(BookflowError) as caught:
        operations.recover(None, None, inp, "post")
    assert caught.value.details == public
    assert caught.value.message == "This needs customer-work at role standard or above."

    calls = []

    def graph(s, binding, ids, write=False):
        calls.append(ids)
        if ids:
            deny()
    monkeypatch.setattr(history, "_authorize_binding_graph", graph)
    binding = SimpleNamespace(on_behalf_of=None)
    with pytest.raises(BookflowError) as caught:
        lifecycle.recover(None, SimpleNamespace(on_behalf_of=None), inp, "post", binding)
    assert caught.value.details == public and calls[-1] == ["source"]

    monkeypatch.setattr(history, "execution_binding", lambda s, binding: None)
    with pytest.raises(BookflowError) as caught:
        pages.authorized_original(None, saved, binding)
    assert caught.value.details == public
