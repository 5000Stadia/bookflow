"""R154: converting an item to an inventory part through Python, the CLI, HTTP and MCP, and an agent refused.

Each surface gets an identical copy of the demo, previews converting its non-inventory Copper Coupling,
converts it, is refused converting it again (one-way), and reads it back as an inventory part with
nothing on hand.
"""
import anyio
import pytest

from bookflow.core.errors import BookflowError
from tests.conftest import as_user, make_actor
from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('item convert',))
COMPANY = "Demo Plumbing Co"
ITEM = "Copper Coupling"  # the demo's non-inventory part, sold on invoices; no open order or quote names it


@pytest.mark.timeout(900)
def test_the_same_conversion_through_python_cli_http_and_mcp(root, tmp_path):
    body = {"item": ITEM, "to": "inventory"}

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                preview = await matrix.call(surface, "item convert", body, dry_run=True)
                done = await matrix.call(surface, "item convert", body)
                again = await matrix.call(surface, "item convert", body, rejected=True)
                shown = await matrix.call(surface, "item show", {"item": ITEM})
                results[surface] = (preview, done, again, shown)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (preview, done, again, shown) in results.items():
        assert preview["conversion_id"] is None and preview["to_type"] == "inventory_part", surface
        assert preview["cogs_account"] == "Cost of Goods Sold" and preview["asset_account"] == "Inventory Asset", surface
        assert done["conversion_id"] and done["from_type"] == "non_inventory_part" and done["one_way"], surface
        assert "E_TYPE_CHANGE" in str(again), surface
        assert shown["type"] == "inventory_part" and shown["quantity_on_hand"] == "0", surface
        assert shown["id"] == done["item_id"], surface
        shapes.add((done["item_id"], done["cogs_account_id"], done["version"], tuple(done["changed_fields"])))
    assert len(shapes) == 1


def test_an_agent_is_refused(client, root):
    """An agent authorized at the same role as a person who may convert is still refused."""
    from bookflow.core import registry
    from bookflow.core.config import Config
    from bookflow.core.context import Context, Interface
    from bookflow.core.dispatch import run as dispatch_run
    from tests.conftest import make_agent
    company = client.company.show(company=COMPANY)["company_id"]
    person = make_actor(root, "convert-person", company_role=(company, "standard"))
    agent = make_agent(lambda name, body: client.run(name, body), "convert-agent", principals=person, company=company)
    config = Config.load(root / "config.toml")
    config.set_user("convert-agent", agent)
    config.save()
    ctx = Context.new(Interface.python, "convert agent witness", on_behalf_of=person, reason="an agent stocking it")
    with pytest.raises(BookflowError) as error:
        dispatch_run(registry.get("item convert"), {"item": ITEM, "to": "inventory"}, ctx, data_root=str(root),
                     company_selector=COMPANY, company_source="option", _login="convert-agent")
    assert error.value.code == "E_PERMISSION" and error.value.details.get("capability") == "item.convert"
    assert "Converting an item to an inventory part needs the capability item.convert" in error.value.message
    # The person the agent acts for may convert.
    done = as_user(root, "convert-person").run("item convert", {"item": ITEM, "to": "inventory"},
                                               company=COMPANY, reason="we stock couplings now")
    assert done["to_type"] == "inventory_part"
