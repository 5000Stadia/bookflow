"""Plain text preserves exact, visible money; JSON remains the command payload."""
import json

from bookflow.adapters.cli.render import render_output
from bookflow.core.money import Money


def test_table_keeps_money_in_later_rows_and_currency_scales():
    out = {"items": [
        {"name": "Empty", "balance": None},
        {"name": "Large", "balance": {"minor_units": 9007199254740993, "currency": "USD"}},
        {"name": "Refund", "balance": {"minor_units": -1251, "currency": "KWD"}},
        {"name": "Zero", "balance": {"minor_units": 0, "currency": "JPY"}},
    ], "count": 4}
    text = render_output(out, False)
    assert "balance" in text
    for value in ("90071992547409.93 USD", "-1.251 KWD", "0 JPY"):
        assert value in text
    assert json.loads(render_output(out, True)) == out


def test_fields_format_money_without_losing_other_nested_data():
    out = {"settlement": {"due": {"minor_units": 123, "currency": "USD", "amount": "1.23"}},
           "capture": {"minor_units": 123, "currency": "USD", "source": "original"},
           "unknown": {"minor_units": 123, "currency": "INVALID"}}
    text = render_output(out, False)
    assert "due: 1.23 USD" in text
    assert "source: original" in text and "currency: INVALID" in text
    assert json.loads(render_output(out, True)) == out


def test_actual_account_list_and_show_include_balance(cli):
    listed = cli.json("account", "list", "--company", "Demo Plumbing Co")
    account = next(row for row in listed["items"] if row.get("balance"))
    balance = account["balance"]
    expected = str(Money(balance["minor_units"], balance["currency"]))
    assert expected in cli.run("account", "list", "--company", "Demo Plumbing Co").stdout
    assert expected in cli.run("account", "show", account["id"],
                               "--company", "Demo Plumbing Co").stdout
