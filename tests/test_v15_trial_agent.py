"""V1.5 blind trials (notes/blind-trials-20260928.md): the agent-interface fixes.

R70 and R80 agents met a bare "Invalid command syntax", results their MCP client refused as too
large, a command search that could not find card charges or sales tax, and forty invoice ids to
copy by hand from `payment suggest` into `payment receive`.
"""
import json

import pytest

import bookflow
from bookflow.core.errors import BookflowError
from tests.test_row3_host import hosted, live  # noqa: F401  (fixtures)


# ---------------------------------------------------------------- unknown top-level fields

def _refusal(tool, arguments):
    from bookflow.adapters.mcp.envelopes import validate
    with pytest.raises(BookflowError) as caught:
        validate(tool, arguments)
    assert caught.value.code == "E_USAGE"
    return caught.value


def test_an_unknown_top_level_field_says_what_was_wrong_and_the_accepted_shape():
    # R80 day 5 #9: limit beside input.
    wrong = _refusal("bookflow_run", {"command": "register query", "input": {"account": "Checking"}, "limit": 12})
    assert wrong.message.startswith("unknown top-level field 'limit': put the command's business arguments")
    assert "inside input" in wrong.message and '"command": "payment receive", "input": {...}' in wrong.message
    assert wrong.details == {"arguments": ["limit"], "accepted": sorted(wrong.details["accepted"])}
    assert {"command", "input", "company", "dry_run", "transport"} <= set(wrong.details["accepted"])
    # R70 #7-9: action beside command.
    mixed = _refusal("bookflow_run", {"command": "payment receive", "input": {}, "action": "execute"})
    assert "action belongs to recovery of an existing intent" in mixed.message
    assert '"action": "execute"|"status"|"release"|"inspect", "operation_ref"' in mixed.message
    # Recovery without command names both shapes; other tools list their own fields.
    stray = _refusal("bookflow_run", {"action": "status", "operation_ref": "op-1", "input": {}})
    assert "without command" in stray.message and "operation_ref" in stray.message
    listed = _refusal("bookflow_list_commands", {"prefix": "tax", "query": "sales"})
    assert listed.message == "unknown field 'query' for bookflow_list_commands. Accepted fields: cursor, limit, prefix."


# ---------------------------------------------------------------- command search

def test_command_search_finds_card_charges_and_sales_tax_by_everyday_words():
    from bookflow.adapters.mcp.catalog import list_commands

    def names(words, **kw):
        page = list_commands(prefix=words, limit=200, **kw)
        return page, [row["name"] for row in page["commands"]]

    for words in ("credit card", "charge", "cc", "CC", "credit-card"):
        page, found = names(words)
        assert "card-charge post" in found and page["match"] == "keywords", words
    page, found = names("tax")
    assert {"sales-tax liability", "sales-tax pay", "sales-tax-code query"} <= set(found)
    assert page["match"] == "keywords" and "suggestions" not in page
    # A real prefix is still a plain prefix page, and a keyword page still pages.
    assert "match" not in list_commands(prefix="card-charge")
    first = list_commands(prefix="tax", limit=3)
    second = list_commands(prefix="tax", limit=3, cursor=first["next_cursor"])
    assert not {r["name"] for r in first["commands"]} & {r["name"] for r in second["commands"]}


# ---------------------------------------------------------------- MCP size budget

def test_a_result_within_the_budget_is_unchanged():
    from bookflow.adapters.mcp.budget import fit
    document = {"id": "P1", "rows": [{"n": i} for i in range(50)]}
    assert fit(document) is document


def test_an_oversized_write_result_keeps_warnings_and_headline_and_names_what_it_left_out():
    from bookflow.adapters.mcp.budget import BUDGET, fit, size
    money = {"amount": "156.96", "currency": "USD", "minor_units": 15696}
    document = {
        "dry_run": False, "id": "01PAYMENT", "version": 1, "changed": True,
        "effect": {"kind": "receive", "applications": [{"invoice_id": f"01INVOICE{i:020d}", "amount": money} for i in range(200)],
                   "allocations": [{"allocation_id": f"01ALLOC{i:022d}", "amount": money} for i in range(160)]},
        "current": {"status": "posted", "applied_minor_units": 627840},
        "summary": {"text": "Received 6278.40 USD from Big Property Co.", "documents": [{"number": f"BP-{i}"} for i in range(5)]},
        "warnings": [{"code": "W_REFERENCE_REPEATED", "message": "check 88231 was already recorded for this customer"}],
    }
    show = '{"command": "payment show", "input": {"payment": "01PAYMENT"}}'
    compact = fit(document, show=show)
    assert size(document) > BUDGET >= size(compact)
    assert list(compact)[0] == "warnings" and compact["warnings"] == document["warnings"]
    assert compact["id"] == "01PAYMENT" and compact["current"] == document["current"]
    assert compact["effect"] == {"kind": "receive"} and compact["summary"] == document["summary"]
    note = compact["result_compacted"]
    assert note["full_characters"] == size(document) and note["budget_characters"] == BUDGET
    assert {"field": "effect.allocations", "items": 160} in note["omitted"]
    assert {"field": "effect.applications", "items": 200} in note["omitted"]
    assert f"Read the full record with bookflow_run {show}." in note["full_result"]
    assert "transport.result_file" in note["full_result"]


def test_an_oversized_query_page_keeps_the_leading_rows_that_fit():
    from bookflow.adapters.mcp.budget import BUDGET, fit, size
    rows = [{"kind": "posting", "posting_line_id": f"01LINE{i:024d}", "memo": "x" * 1400} for i in range(37)]
    document = {"count": 37, "next_cursor": None, "account": {"name": "Checking"}, "rows": rows}
    compact = fit(document)
    kept = len(compact["rows"])
    assert 0 < kept < 37 and compact["rows"] == rows[:kept] and size(compact) <= BUDGET
    assert compact["count"] == 37 and compact["account"] == {"name": "Checking"}
    note = compact["result_compacted"]
    assert note["omitted"][0] == {"field": "rows", "items": 37, "kept": kept}
    assert f"input.limit {kept} and follow next_cursor" in note["full_result"]


def test_a_cut_page_drops_the_cursor_that_would_skip_rows_and_a_cut_list_says_how_to_get_the_rest():
    """R70/R80: invoice query cut to 14 of 50 kept a cursor past row 50; account list cut to 16 of 33 has no limit."""
    from bookflow.adapters.mcp.budget import fit
    rows = [{"id": f"01ROW{i:024d}", "memo": "x" * 1400} for i in range(33)]
    paged = fit({"items": rows, "next_cursor": "past-row-33"},
                paging={"command": "invoice query", "paged": True, "narrow": [], "alternative": None})
    kept = len(paged["items"])
    assert paged["next_cursor"] is None and "would skip the rows not shown" in paged["result_compacted"]["full_result"]
    assert f"input.limit {kept} and follow next_cursor" in paged["result_compacted"]["full_result"]
    listed = fit({"items": rows}, paging={"command": "account list", "paged": False, "narrow": ["query", "filter"],
                                          "alternative": "account query"})
    note = listed["result_compacted"]
    assert note["omitted"][0] == {"field": "items", "items": 33, "kept": kept}
    assert f"kept its first {kept} of 33 items; account list returns them all at once and takes no limit" in note["full_result"]
    assert f"run account query with input.limit {kept} and follow next_cursor" in note["full_result"]
    assert "narrow this command with input.query or input.filter" in note["full_result"]


# ---------------------------------------------------------------- payment receive as suggested

def _open_invoices(c, company, count):
    customer = c.run("customer create", {"name": "Big Property Co"}, company=company, reason="fixture")["id"]
    posted = []
    for day in [5, 1, 3, 2, 4, 6, 7, 8][:count]:
        posted.append(c.run("invoice post", {"date": f"2026-08-{day:02d}", "customer": customer, "number": f"BP-{day:03d}",
                                             "lines": [{"item": "Mainline Clearing", "quantity": "1"}]},
                            company=company, reason="fixture"))
    return customer, posted


def test_payment_receive_applies_the_suggestion_without_copying_ids(root):
    c = bookflow.connect(data_root=str(root))
    company = c.company.list()["items"][0]["company_id"]
    customer, _ = _open_invoices(c, company, 5)
    due = {row["invoice_id"]: row for row in c.run("payment suggest", {"mode": "new_receipt", "customer": customer,
            "date": "2026-09-27", "amount": "1000000.00", "strategy": "exact_then_oldest"}, company=company)["items"]}
    each = next(iter(due.values()))["due_minor_units"]
    amount = f"{(3 * each + each // 2) / 100:.2f}"   # three oldest in full and half the fourth
    suggestion = c.run("payment suggest", {"mode": "new_receipt", "customer": customer, "date": "2026-09-27",
                                           "amount": amount, "strategy": "exact_then_oldest"}, company=company)["items"]
    expected = [(row["invoice_id"], row["amount_minor_units"]) for row in suggestion]
    assert len(expected) == 4
    receipt = {"customer": customer, "date": "2026-09-27", "amount": amount, "operation_key": "r70-suggested",
               "reference": "88231", "payment_method": "Check", "applications": {"mode": "suggested"}}

    def applied(result):
        return [(row["invoice_id"], row["amount"]["minor_units"]) for row in result["effect"]["applications"]]

    preview = c.run("payment receive", receipt, company=company, reason="R70 as suggested", dry_run=True)
    assert preview["dry_run"] is True and sorted(applied(preview)) == sorted(expected)
    assert not c.run("payment query", {"customer": customer}, company=company)["items"]

    posted = c.run("payment receive", receipt, company=company, reason="R70 as suggested")
    assert sorted(applied(posted)) == sorted(expected)
    assert posted["current"]["applied_minor_units"] == sum(units for _, units in expected)
    # A replay of the same operation is the same receipt, not a second choice.
    again = c.run("payment receive", receipt, company=company, reason="R70 as suggested")
    assert again["id"] == posted["id"] and again["idempotent_replay"] is True


# ---------------------------------------------------------------- over actual MCP

@pytest.mark.timeout(600)
def test_actual_mcp_bulk_receipt_arrives_within_budget_with_its_warning(hosted, live, tmp_path):
    """R70 #23 over the real launcher: 40 invoices paid as suggested; the agent sees its own result."""
    pytest.importorskip("mcp")
    import anyio
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from bookflow.adapters.mcp.budget import BUDGET
    from tests import provenance

    company = hosted.company_id
    customer = hosted.ok("customer.create", {"name": "Big Property Co"}, company=company)["id"]
    for index in range(40):
        hosted.ok("invoice.post", {"date": f"2026-08-{index % 28 + 1:02d}", "customer": customer, "number": f"BP-{index:03d}",
                                   "lines": [{"item": "Mainline Clearing", "quantity": "1"}]}, company=company)
    hosted.ok("payment.receive", {"customer": customer, "date": "2026-09-20", "amount": "1.00", "operation_key": "earlier",
                                  "reference": "88231", "payment_method": "Check"}, company=company)
    due = sum(row["due_minor_units"] for row in hosted.ok("payment.invoices", {"mode": "new_receipt", "customer": customer,
              "date": "2026-09-27", "limit": 200}, company=company)["items"])

    async def witness():
        params = StdioServerParameters(command=provenance.launcher(), args=["mcp", "--url", live, "--client-name", "v15-budget"],
            env=provenance.child_env(BOOKFLOW_TOKEN=hosted.secret, BOOKFLOW_COMPANY=company,
                                     BOOKFLOW_DATA_ROOT=str(tmp_path / "absent")), cwd=str(tmp_path))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                reply = await session.call_tool("bookflow_run", {"command": "payment receive", "reason": "R70 bulk", "input": {
                    "customer": customer, "date": "2026-09-27", "amount": f"{due / 100:.2f}", "operation_key": "bulk-88231",
                    "reference": "88231", "payment_method": "Check", "applications": {"mode": "suggested"}}})
                assert not reply.is_error, reply.content[0].text[:500]
                text = reply.content[0].text
                result = json.loads(text)
                assert len(text) <= BUDGET and result == reply.structured_content
                assert list(result)[0] == "warnings" and result["warnings"], "the repeated-reference warning reaches the agent"
                assert result["effect_counts"]["applications"] == 40 and result["current"]["available_minor_units"] == 0
                note = result["result_compacted"]
                assert note["full_characters"] > BUDGET and {"field": "effect.applications", "items": 40} in note["omitted"]
                show = json.loads(note["full_result"].split("bookflow_run ", 1)[1].split("}}. ", 1)[0] + "}}")
                assert show == {"command": "payment show", "input": {"payment": result["id"]}}
                shown = await session.call_tool("bookflow_run", show)
                assert not shown.is_error and json.loads(shown.content[0].text)["id"] == result["id"]
                wrong = await session.call_tool("bookflow_run", {"command": "payment query", "input": {}, "limit": 5})
                assert wrong.is_error and "put the command's business arguments" in wrong.content[0].text
                # R72 #3: full help arrives within the budget over the real launcher, and a section reads alone.
                helped = await session.call_tool("bookflow_help", {"command": "invoice post", "view": "full"})
                assert not helped.is_error and len(helped.content[0].text) <= BUDGET
                outline = json.loads(helped.content[0].text)
                assert outline["result_compacted"]["reason"] == "size_budget"
                part = await session.call_tool("bookflow_help", {"command": "invoice post", "section": "Errors"})
                assert not part.is_error and json.loads(part.content[0].text)["section"] == "Errors"
                # R72 #17: a result_file outside every output directory is refused before anything is
                # submitted, and says so, rather than posting and then reporting an unknown outcome.
                refused = await session.call_tool("bookflow_run", {"command": "invoice post", "reason": "R72 file", "input": {
                    "date": "2026-09-27", "customer": customer, "number": "R72-FILE",
                    "lines": [{"item": "Mainline Clearing", "quantity": "1"}]}, "transport": {"result_file": str(tmp_path / "invoice.json")}})
                error = json.loads(refused.content[0].text)
                assert refused.is_error and error["code"] == "E_PERMISSION"
                assert error["details"]["outcome"] == "not_submitted" and error["details"]["field"] == "transport.result_file"
                assert error["details"]["allowed_directories"] == [] and "--output-dir" in error["message"]
                assert hosted.call("invoice.show", {"invoice": "R72-FILE"}, company=company).status_code == 404
                # A cut list that takes no limit points at its paged query (R80 day 1: account list 16 of 33).
                listed = json.loads((await session.call_tool("bookflow_run", {"command": "account list", "input": {}})).content[0].text)
                assert "run account query with input.limit" in listed["result_compacted"]["full_result"]

    anyio.run(witness)


def test_full_help_arrives_within_the_budget_usage_and_example_first_with_every_section_reachable():
    """R72 #3: `bookflow_help invoice post view:full` was 202,778 characters and the client cut it."""
    from bookflow.adapters.mcp.budget import BUDGET, fit_help, size
    from bookflow.adapters.mcp.catalog import command_help
    whole = command_help('invoice post', 'full')
    assert size(whole) > BUDGET
    compact = fit_help(whole)
    assert size(compact) <= BUDGET
    assert list(compact)[:4] == ['name', 'description', 'view', 'example']
    assert compact['example'] == whole['example'] and compact['input_schema'] == whole['input_schema']
    note = compact['result_compacted']
    assert set(note['outlined']) >= {'documentation', 'output_schema'} and 'section=' in note['full_result']
    # Each outlined part names its sections, and each one reads alone within the budget.
    assert 'Output' in compact['documentation']['sections']
    definition = compact['output_schema']['sections'][0]
    part = fit_help(command_help('invoice post', section=definition))
    assert part['content'] == whole['output_schema']['$defs'][definition] and size(part) <= BUDGET
    output = fit_help(command_help('invoice post', section='Output'))
    assert size(output) <= BUDGET and output['result_compacted']['reason'] == 'size_budget'
    with pytest.raises(BookflowError) as unknown:
        command_help('invoice post', section='No such part')
    assert unknown.value.code == 'E_VALIDATION' and definition in unknown.value.details['allowed']
    # A view that fits is unchanged.
    assert fit_help(command_help('reconcile finish', 'usage')) == command_help('reconcile finish', 'usage')
