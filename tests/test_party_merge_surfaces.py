"""R117: the same customer and vendor merge through Python, the CLI, HTTP and MCP.

Each surface gets an identical copy of the demo, previews the merge, makes it, makes it again
(nothing written), reads A/R aging with one combined customer, and undoes it.
"""
import anyio
import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('customer merge', 'customer unmerge', 'vendor merge', 'vendor unmerge'))
SURVIVOR, MERGED = "Commercial Example Customer", "Line Kinds Example Customer"


@pytest.mark.timeout(900)
def test_the_same_merge_through_python_cli_http_and_mcp(root, tmp_path):
    pair = {"merged": MERGED, "into": SURVIVOR}
    vendors = {"merged": "Summit Pipe Contracting", "into": "Lakeview Pipe Supply"}

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                preview = await matrix.call(surface, "customer merge", pair, dry_run=True)
                done = await matrix.call(surface, "customer merge", pair)
                again = await matrix.call(surface, "customer merge", pair)
                aging = await matrix.call(surface, "report ar-aging", {"as_of": DEMO_AS_OF, "limit": 200})
                undone = await matrix.call(surface, "customer unmerge", {"merged": MERGED})
                vendor = await matrix.call(surface, "vendor merge", vendors)
                vendor_undone = await matrix.call(surface, "vendor unmerge", {"merged": vendors["merged"]})
                results[surface] = (preview, done, again, aging, undone, vendor, vendor_undone)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (preview, done, again, aging, undone, vendor, vendor_undone) in results.items():
        assert preview["merge_id"] is None and preview["changed"], surface
        assert done["changed"] and not again["changed"] and again["merge_id"] == done["merge_id"], surface
        labels = {row["display_customer_label"]: row["total"]["minor_units"] for row in aging["rows"]}
        assert MERGED not in labels and labels[SURVIVOR] == preview["survivor_balance_after"]["minor_units"], surface
        assert undone["changed"] and undone["reactivated"], surface
        assert vendor["changed"] and vendor_undone["changed"], surface
        shapes.add((preview["document_count"], preview["survivor_balance_after"]["minor_units"], vendor["document_count"]))
    assert len(shapes) == 1
