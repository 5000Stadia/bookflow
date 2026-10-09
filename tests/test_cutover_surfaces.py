"""R166: the same move-in through Python, the CLI, HTTP and MCP.

One fresh company holds the sample export set as attachments; each surface gets an identical copy
of it, plans, applies and ties out the same move-in, and gets the same answers: ready, the same
seventy-five writes with the same outside ids, and a tie-out to the cent.
"""
from pathlib import Path

import anyio
import bookflow
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('cutover plan', 'cutover apply', 'cutover tie-out'))
FIXTURES = Path(__file__).parent / "fixtures" / "cutover"
AS_OF = "2026-09-30"


@pytest.mark.timeout(900)
def test_the_same_move_in_through_python_cli_http_and_mcp(tmp_path):
    baseline = tmp_path / "baseline"
    client = bookflow.connect(data_root=str(baseline))
    client.init()
    client.run("organization new", {"name": "Riverbend Holdings"})
    client.run("company new", {"organization": "Riverbend Holdings", "legal_name": "Riverbend Plumbing LLC",
                               "display_name": "Riverbend Plumbing", "home_currency": "USD", "timezone": "America/Chicago"})
    company = client.company.show(company="Riverbend Plumbing")["company_id"]
    client.run("company update", {"sales_tax_enabled": True}, company=company)
    files = []
    for path in sorted(FIXTURES.iterdir()):
        with open(path, "rb") as body:
            added = client.run("attachment add", {"record_type": "company_info", "record_id": company,
                                                  "original_filename": path.name, "media_type": "text/plain"},
                               company=company, input_stream=body)
        files.append({"attachment": added["attachment"]["id"]})
    move = {"as_of": AS_OF, "files": files}

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(baseline, tmp_path)
            assert matrix.company == company
            results = {}
            for surface in SURFACES:
                plan = await matrix.call(surface, "cutover plan", move)
                applied = await matrix.call(surface, "cutover apply", move)
                tie = await matrix.call(surface, "cutover tie-out", move)
                rerun = await matrix.call(surface, "cutover apply", move)
                results[surface] = (plan, applied, tie, rerun)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (plan, applied, tie, rerun) in results.items():
        assert plan["ready"] and not plan["dry_run"], surface
        assert {f["kind"] for f in plan["files"]} == {"iif", "trial_balance", "open_invoices", "unpaid_bills",
                                                      "ar_aging", "ap_aging", "inventory_valuation"}, surface
        assert (applied["created"], applied["already_in"]) == (76, 0), surface
        assert tie["tied"] and tie["clearing"]["minor_units"] == 0, (surface, tie["summary"])
        assert tie["receivables"]["books_total"]["minor_units"] == 2509905, surface
        assert tie["payables"]["books_total"]["minor_units"] == 984590, surface
        assert (rerun["created"], rerun["already_in"]) == (0, 76), surface
        shapes.add(tuple((step["kind"], step["outside_id"], step["action"]) for step in applied["steps"]))
    assert len(shapes) == 1
