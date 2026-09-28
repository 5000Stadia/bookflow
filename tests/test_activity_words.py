"""Audit events read as what was done and who did it (R92); the stored summary is unchanged."""
import pytest

from bookflow.adapters.workbench.activity import sentence, through, who


@pytest.mark.parametrize("command, summary, expected", [
    ("bill post", "post bill 2", "Posted bill 2"),
    ("invoice void", "invoice void: DEMO-7", "Voided invoice DEMO-7"),
    ("invoice void", "void invoice DEMO-7", "Voided invoice DEMO-7"),
    ("estimate invoice", "estimate invoice: INV-3", "Created invoice INV-3 from an estimate"),
    ("estimate work-order", "work-order work order WO-1", "Created work order WO-1 from an estimate"),
    ("bill pay", "pay bill payment P-2", "Paid bills with payment P-2"),
    ("deposit post", "Deposit post", "Posted deposit"),
    ("payment recovery begin", "payment recovery begin", "Began payment recovery"),
    ("payment-method create", "created payment-method Check", "Created payment method Check"),
    ("company update", "updated company info: default_sales_tax_item_id", "Updated company info: default sales tax item"),
    ("note add", "added note to work_document 01M3KH74Z2CW45ZH13C29VKTAM", "Added note to work document"),
    ("item-receipt post", "post item receipt IR-01M3KH9E735HGZPK3Q50HC4Y65", "Posted item receipt IR-01M3KH9E735HGZPK3Q50HC4Y65"),
    ("bill post", "stock moved by bill B-1", "Stock moved by bill B-1"),
])
def test_summaries_read_as_what_was_done(command, summary, expected):
    assert sentence({"command": command, "summary": summary}) == expected


def test_who_names_the_agent_and_whom_it_acted_for():
    assert who({"actor_name": "Office assistant", "on_behalf_of_name": "k"}) == "Office assistant for k"
    assert who({"actor_name": "k", "on_behalf_of_name": None}) == "k"
    assert through("mcp") == "MCP" and through("gui") == "Browser"
