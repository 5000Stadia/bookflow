"""R179: what a 1099 vendor was paid before the books began here, through Python, the CLI, HTTP and MCP.

Each surface gets an identical copy of the demo, previews an opening 1099 amount for the demo's 1099
subcontractor, sets it, sets it again (nothing written), reads the year's 1099 summary with it counted,
and clears it.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('vendor 1099-opening',))
VENDOR = "Summit Pipe Contracting"  # the demo's 1099 vendor: a 2,400.00 check in December 2026


@pytest.mark.timeout(900)
def test_the_same_opening_1099_amount_through_python_cli_http_and_mcp(root, tmp_path):
    opening = {"vendor": VENDOR, "year": 2026, "as_of": "2026-03-31", "amount": "1000.00"}

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                preview = await matrix.call(surface, "vendor 1099-opening", opening, dry_run=True)
                done = await matrix.call(surface, "vendor 1099-opening", opening)
                again = await matrix.call(surface, "vendor 1099-opening", opening)
                year = await matrix.call(surface, "report vendor-1099-summary", {"date_from": "2026-01-01", "date_to": "2026-12-31"})
                cleared = await matrix.call(surface, "vendor 1099-opening", {**opening, "amount": "0.00"})
                results[surface] = (preview, done, again, year, cleared)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (preview, done, again, year, cleared) in results.items():
        assert preview["id"] is None and preview["changed"] and preview["amount"]["minor_units"] == 100000, surface
        assert done["changed"] and done["version"] == 1 and done["id"], surface
        assert not again["changed"] and again["id"] == done["id"] and again["version"] == 1, surface
        row = next(r for r in year["rows"] if r["display_vendor_label"] == VENDOR)
        assert (row["payments"]["minor_units"], row["opening_payments"]["minor_units"]) == (340000, 100000), surface
        assert cleared["changed"] and cleared["amount"]["minor_units"] == 0 and cleared["version"] == 2, surface
        shapes.add((done["vendor_id"] is not None, row["payments"]["minor_units"], year["totals"]["opening_payments"]["minor_units"]))
    assert len(shapes) == 1
