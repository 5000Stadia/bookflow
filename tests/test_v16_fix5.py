"""Fix batch 5 after the R170 blind July: help text, cutover plan paging and account numbers.

- `discount_account` help says what the commands do since fix 4a: refuse and list the chart's
  discount accounts, never create one.
- `cutover plan` pages its steps with limit and cursor (as `reconcile import` pages its lines), so
  a plan over the MCP size budget tells the agent how to read the rest; counts, blocking lines and
  exceptions always cover the whole plan.
- A journal line names its account by number as the anchor's Make General Journal Entries accepts a
  typed number; elsewhere a number is refused with the account it belongs to named.
"""
import pytest

from bookflow import BookflowError
from bookflow.core import registry

COMPANY = 'Demo Plumbing Co'


def _texts(model):
    import json
    return json.dumps(model.model_json_schema())


def test_discount_account_help_says_it_refuses_and_never_creates():
    for name in ('payment receive', 'bill pay', 'company update'):
        command = registry.get(name)
        text = command.description + _texts(command.input_model)
        assert 'created as an income' not in text and 'created if the chart' not in text, name
        assert 'when first needed' not in text and 'created when' not in text, name
    for name in ('payment receive', 'bill pay'):
        text = _texts(registry.get(name).input_model)
        assert 'nothing is created' in text, name


def _numbered(client):
    return client.account.create(name='Telephone Expense', type='expense', number='6210', company=COMPANY)


def test_a_journal_line_takes_an_account_number(client):
    phone = _numbered(client)
    bank = client.account.create(name='Fix5 Cash', type='bank', company=COMPANY)
    posted = client.journal.post(date='2026-01-12', company=COMPANY, lines=[
        dict(account='6210', side='debit', amount='45.00'),
        dict(account=bank['id'], side='credit', amount='45.00')])
    import json
    assert phone['id'] in json.dumps(posted)


def test_an_unknown_account_says_what_it_accepts(client):
    with pytest.raises(BookflowError) as raised:
        client.journal.post(date='2026-01-12', company=COMPANY, lines=[
            dict(account='7654321', side='debit', amount='1.00'), dict(account='7654320', side='credit', amount='1.00')])
    assert raised.value.code == 'E_RECORD_NOT_FOUND'
    assert '`account list`' in raised.value.message


def test_elsewhere_a_number_is_refused_with_its_account_named(client):
    from pathlib import Path
    from bookflow.company import accounts
    from bookflow.storage.engine import open_database
    phone = _numbered(client)
    path = Path(client.company.show(company=COMPANY)['path']) / 'company.db'
    with open_database(path, writable=False) as db:
        with pytest.raises(BookflowError) as raised:
            accounts.resolve_account(db, '6210')
        assert accounts.resolve_account(db, '6210', by_number=True)['id'] == phone['id']
    error = raised.value
    assert error.details['suggestions'] == ['Telephone Expense']
    assert error.details['account_number']['id'] == phone['id']
    assert "number of 'Telephone Expense'" in error.message


def test_cutover_plan_pages_its_steps_and_keeps_the_whole_plan_in_view(tmp_path, monkeypatch):
    from tests import fakeco_replay as replay_module
    from tests.test_cutover_rest import _harbor
    harbor = _harbor(tmp_path, monkeypatch)
    files = harbor.attach(replay_module.CORE_FILES + replay_module.REST)
    move_in = dict(as_of=replay_module.AS_OF, files=files, mappings=replay_module.MAPPINGS)
    whole = harbor.run("cutover plan", move_in)
    assert not whole["has_more"] and whole["next_cursor"] is None
    total = len(whole["steps"])
    assert total > 40

    seen, cursor = [], None
    while True:
        page = harbor.run("cutover plan", dict(move_in, limit=40, **({"cursor": cursor} if cursor else {})))
        for key in ("counts", "blocking", "exceptions", "checks", "clearing", "journal", "mappings", "summary", "ready"):
            assert page[key] == whole[key], key
        seen.extend(page["steps"])
        assert len(page["steps"]) <= 40
        if not page["has_more"]:
            assert page["next_cursor"] is None
            break
        cursor = page["next_cursor"]
    assert seen == whole["steps"]

    # A cursor belongs to its input: another as_of refuses it.
    first = harbor.run("cutover plan", dict(move_in, limit=10))
    with pytest.raises(BookflowError) as raised:
        harbor.run("cutover plan", dict(move_in, as_of="2026-05-31", limit=10, cursor=first["next_cursor"]))
    assert raised.value.code == "E_VALIDATION"


def test_an_oversized_plan_tells_the_agent_to_page():
    """The MCP size budget offers the paged advice once `cutover plan` takes limit and cursor."""
    from bookflow.adapters.mcp.budget import fit
    fields = registry.get("cutover plan").input_model.model_json_schema()["properties"]
    assert "limit" in fields and "cursor" in fields
    steps = [dict(order=n, kind="customer", outside_id=f"customer:{n}", name="x" * 200, action="create",
                  command="customer create", record_id=None, amount=None, date=None, detail=None) for n in range(167)]
    compact = fit(dict(as_of="2026-06-30", ready=True, blocking=[], counts=[], steps=steps, has_more=False,
                       next_cursor=None), paging={"command": "cutover plan", "paged": True})
    note = compact["result_compacted"]
    assert "Rerun with input.limit" in note["full_result"] and "follow next_cursor" in note["full_result"]
