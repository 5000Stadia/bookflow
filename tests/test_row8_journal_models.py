"""Pure input and exact-money contracts for domestic journals."""

from datetime import date
from decimal import localcontext

import pytest
from pydantic import ValidationError

from bookflow.company.journal_models import (
    JournalHistoryInput,
    JournalLineInput,
    JournalPostInput,
    JournalQueryInput,
    JournalShowInput,
    JournalUpdateInput,
    JournalVoidInput,
    MoneyInput,
    checked_sum,
    parse_domestic_amount,
)
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX, INT64_MIN
from bookflow.core.money import Money


def line(**changes):
    return {"account": "Cash", "side": "debit", "amount": "1.00", **changes}


def post(**changes):
    return {"date": "2024-02-29", "lines": [line(), line(side="credit")], **changes}


@pytest.mark.parametrize(("value", "currency", "units", "amount"), [
    ("12.50", "USD", 1250, "12.50"),
    (" 0012.5 USD ", "USD", 1250, "12.50"),
    ("2345 JPY", "JPY", 2345, "2345"),
    ("0.001 KWD", "KWD", 1, "0.001"),
    ("92233720368547758.07", "USD", INT64_MAX, "92233720368547758.07"),
    ({"minor_units": 1250, "currency": "USD", "amount": "12.50"}, "USD", 1250, "12.50"),
    (MoneyInput(minor_units=1, currency="USD"), "USD", 1, "0.01"),
])
def test_exact_canonical_money(value, currency, units, amount):
    with localcontext() as context:
        context.prec = 2
        money = parse_domestic_amount(value, currency)
    assert money == Money(units, currency)
    assert money.to_dict() == {"minor_units": units, "currency": currency, "amount": amount}


@pytest.mark.parametrize("value", [
    1, 1.25, True, False, None, [], Money(1, "USD"), "1e2", "+1", "NaN", "1,000",
    "1 usd", "1 ZZZ", "1 EUR", "１.００", "",
    {"minor_units": True, "currency": "USD"},
    {"minor_units": 1.0, "currency": "USD"},
    {"minor_units": "1", "currency": "USD"},
    {"minor_units": 1, "currency": "EUR"},
    {"minor_units": 1, "currency": "usd"},
    {"minor_units": 1, "currency": "USD", "extra": 1},
    {"amount": "1.00", "currency": "USD"},
    {"minor_units": 1, "currency": "USD", "amount": "1.00"},
    {"minor_units": 1, "currency": "USD", "amount": None},
    {"minor_units": 1, "currency": "USD", "amount": 0.01},
    {"minor_units": 1, "currency": "USD", "amount": "0.01 EUR"},
])
def test_amount_rejects_coercion_foreign_and_contradiction(value):
    with pytest.raises(BookflowError) as caught:
        parse_domestic_amount(value, "USD", field="lines.0.amount")
    assert caught.value.code == "E_VALIDATION"
    assert caught.value.details["fields"][0]["field"] == "lines.0.amount"


@pytest.mark.parametrize("home", [None, True, "usd", "ZZZ", []])
def test_home_currency_is_validated_even_with_explicit_code(home):
    with pytest.raises(BookflowError, match="E_VALIDATION"):
        parse_domestic_amount("1 USD", home)


@pytest.mark.parametrize("value", ["0", "-0.00", "-1", "92233720368547758.08", "9" * 5000,
    {"minor_units": 0, "currency": "USD"},
    {"minor_units": INT64_MAX + 1, "currency": "USD"},
])
def test_amount_positive_i64_bounds(value):
    with pytest.raises(BookflowError, match="E_VALUE_RANGE"):
        parse_domestic_amount(value, "USD")


@pytest.mark.parametrize(("value", "currency"), [("1.000", "USD"), ("1.0", "JPY"), ("1.0000", "KWD")])
def test_precision_is_never_rounded(value, currency):
    with pytest.raises(BookflowError, match="E_AMOUNT_PRECISION"):
        parse_domestic_amount(value, currency)


def test_sum_exact_signed_and_final_range():
    assert checked_sum(iter([]), "total") == 0
    assert checked_sum([INT64_MAX - 1, 1], "total") == INT64_MAX
    assert checked_sum([INT64_MAX, 1, -1], "total") == INT64_MAX
    assert checked_sum([INT64_MIN], "total") == INT64_MIN
    for values in ([INT64_MAX, 1], [INT64_MIN, -1], [INT64_MAX + 1]):
        with pytest.raises(BookflowError, match="E_VALUE_RANGE"):
            checked_sum(values, "total")
    for values in ([True], [1.0], ["1"]):
        with pytest.raises(BookflowError, match="E_VALIDATION"):
            checked_sum(values, "total")


@pytest.mark.parametrize("changes", [
    {"minor_units": True}, {"minor_units": "1"}, {"minor_units": 1.0},
    {"minor_units": 0}, {"minor_units": INT64_MAX + 1}, {"currency": "ZZZ"},
    {"amount": "0.02"}, {"amount": None}, {"amount": 0.01}, {"unexpected": True},
])
def test_money_input_is_strict_and_consistent(changes):
    with pytest.raises(ValidationError):
        MoneyInput.model_validate({"minor_units": 1, "currency": "USD", **changes})


def test_line_party_pair_and_selectors():
    for kind in ("customer", "vendor", "employee", "other_name"):
        model = JournalLineInput.model_validate(line(name_type=kind, name_id="ordinary selector"))
        assert model.name_id == "ordinary selector"
    for changes in ({"name_type": "vendor"}, {"name_id": "Vendor"}, {"name_type": "job", "name_id": "Job"},
                    {"account": " "}, {"account": 1}, {"side": "Debit"}, {"amount": 1.0}, {"extra": 1}):
        with pytest.raises(ValidationError):
            JournalLineInput.model_validate(line(**changes))


@pytest.mark.parametrize("value", ["2023-02-29", "2024-04-31", "0000-01-01", "2024-1-01", "20240101", "2024-01-01T00:00:00", date(2024, 1, 1), 20240101])
def test_actual_iso_dates(value):
    for model, payload in ((JournalPostInput, post(date=value)),
                           (JournalUpdateInput, {"journal": "1", "date": value}),
                           (JournalQueryInput, {"date_from": value}),
                           (JournalQueryInput, {"date_to": value})):
        with pytest.raises(ValidationError):
            model.model_validate(payload)


def test_create_bounds_and_line_identity():
    assert JournalPostInput.model_validate(post(number="  J-1  ")).number == "J-1"
    assert len(JournalPostInput.model_validate(post(lines=[line()] * 200)).lines) == 200
    for changes in ({"number": " "}, {"number": "x" * 65}, {"memo": "x" * 2001},
                    {"lines": []}, {"lines": [line()]}, {"lines": [line()] * 201},
                    {"lines": [line(line_id="old"), line()]},
                    {"lines": [line(line_id=None), line()]}, {"extra": 1}):
        with pytest.raises(ValidationError):
            JournalPostInput.model_validate(post(**changes))


def test_patch_presence_clear_and_replacement():
    absent = JournalUpdateInput(journal="J-1")
    assert absent.model_fields_set == {"journal"}
    assert absent.refresh_defaults is False
    clear = JournalUpdateInput(journal="J-1", memo=None)
    assert clear.model_fields_set == {"journal", "memo"}
    replacement = JournalUpdateInput(journal="J-1", lines=[line(line_id="existing"), line()])
    assert replacement.lines[0].line_id == "existing"
    for changes in ({"date": None}, {"number": None}, {"lines": None}, {"lines": []}, {"lines": [line()]},
                    {"lines": [line()] * 201}, {"refresh_defaults": "false"}, {"refresh_defaults": 0}):
        with pytest.raises(ValidationError):
            JournalUpdateInput.model_validate({"journal": "J-1", **changes})


@pytest.mark.parametrize("value", [0, -1, True, 1.0, "1"])
def test_versions_are_strict_positive(value):
    for model in (JournalUpdateInput, JournalVoidInput):
        with pytest.raises(ValidationError):
            model(journal="1", expected_version=value)
    with pytest.raises(ValidationError):
        JournalShowInput(journal="1", revision_number=value)


def test_query_and_history_bounds():
    assert JournalQueryInput().limit == JournalHistoryInput(journal="1").limit == 50
    assert JournalQueryInput(date_from="2024-02-29", date_to="2024-02-29").date_from == "2024-02-29"
    for changes in ({"date_from": "2024-03-01", "date_to": "2024-02-29"}, {"status": "draft"},
                    {"query": "x" * 2001}, {"unexpected": 1}):
        with pytest.raises(ValidationError):
            JournalQueryInput(**changes)
    for model, base in ((JournalQueryInput, {}), (JournalHistoryInput, {"journal": "1"})):
        assert model(**base, limit=200, cursor="x" * 4096).limit == 200
        for changes in ({"limit": 0}, {"limit": 201}, {"limit": True}, {"limit": "50"},
                        {"limit": 50.0}, {"cursor": "x" * 4097}):
            with pytest.raises(ValidationError):
                model.model_validate({**base, **changes})


def test_declared_schema_bounds():
    schema = JournalPostInput.model_json_schema()
    assert schema["properties"]["lines"]["minItems"] == 2
    assert schema["properties"]["lines"]["maxItems"] == 200
    assert schema["properties"]["memo"]["anyOf"][0]["maxLength"] == 2000
    assert schema["properties"]["number"]["anyOf"][0]["maxLength"] == 64
    page = JournalQueryInput.model_json_schema()["properties"]
    assert page["limit"]["minimum"] == 1
    assert page["limit"]["maximum"] == 200
    assert page["cursor"]["anyOf"][0]["maxLength"] == 4096
