"""R117: the same account and item merge through Python, the CLI, HTTP and MCP.

The baseline is the twin test's small company with every use on the duplicates; each surface gets
an identical copy, previews the account merge, makes it, makes it again (nothing written), reads
the trial balance with one combined account, undoes it, and does the same for the item.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix
from tests.test_list_merge_twins import ACCOUNT_M, ACCOUNT_S, ITEM_M, ITEM_S, _build, client  # noqa: F401

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('account merge', 'account unmerge', 'item merge', 'item unmerge'))


@pytest.mark.timeout(900)
def test_the_same_list_merge_through_python_cli_http_and_mcp(client, tmp_path):  # noqa: F811
    import os
    from pathlib import Path
    _build(client, "merged")
    baseline = Path(os.environ["BOOKFLOW_DATA_ROOT"])
    accounts = {"merged": ACCOUNT_M, "into": ACCOUNT_S}
    items = {"merged": ITEM_M, "into": ITEM_S}

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(baseline, tmp_path / "matrix")
            results = {}
            for surface in SURFACES:
                preview = await matrix.call(surface, "account merge", accounts, dry_run=True)
                done = await matrix.call(surface, "account merge", accounts)
                again = await matrix.call(surface, "account merge", accounts)
                trial = await matrix.call(surface, "report trial-balance", {"date_to": "2026-12-31", "limit": 200})
                undone = await matrix.call(surface, "account unmerge", {"merged": ACCOUNT_M})
                item = await matrix.call(surface, "item merge", items)
                item_undone = await matrix.call(surface, "item unmerge", {"merged": ITEM_M})
                results[surface] = (preview, done, again, trial, undone, item, item_undone)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (preview, done, again, trial, undone, item, item_undone) in results.items():
        assert preview["merge_id"] is None and preview["changed"], surface
        assert done["changed"] and not again["changed"] and again["merge_id"] == done["merge_id"], surface
        labels = {row["current_account_name"]: row["signed_net"]["minor_units"] for row in trial["rows"]}
        assert ACCOUNT_M not in labels and labels[ACCOUNT_S] == preview["survivor_balance_after"]["minor_units"], surface
        assert undone["changed"] and undone["reactivated"], surface
        assert item["changed"] and item_undone["changed"] and item_undone["restored"] == len(item["references"]), surface
        shapes.add((preview["document_count"], preview["survivor_balance_after"]["minor_units"],
                    len(preview["references"]), item["document_count"], len(item["references"])))
    assert len(shapes) == 1
