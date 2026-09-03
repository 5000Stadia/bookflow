import pytest

from bookflow.core.errors import BookflowError
from bookflow.core.money import CURRENCIES, Money


def test_parse_forms():
    assert Money.parse("12.50 USD") == Money(1250, "USD")
    assert Money.parse("2345 JPY") == Money(2345, "JPY")
    assert Money.parse("12.5", default_currency="USD") == Money(1250, "USD")
    assert Money.parse("-0.01 USD") == Money(-1, "USD")
    assert Money.parse({"amount": "12.50", "currency": "USD", "minor_units": 1250}) == Money(1250, "USD")
    assert Money.parse({"amount": "1.234", "currency": "BHD"}) == Money(1234, "BHD")


def test_rejects_floats_and_bools():
    with pytest.raises(TypeError):
        Money(12.5, "USD")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Money(True, "USD")  # type: ignore[arg-type]
    for bad in (12.5, True):
        with pytest.raises(BookflowError) as e:
            Money.parse(bad)
        assert e.value.code == "E_VALIDATION"


def test_precision_and_unknown_code():
    with pytest.raises(BookflowError) as e:
        Money.parse("12.505 USD")
    assert e.value.code == "E_AMOUNT_PRECISION"
    with pytest.raises(BookflowError) as e:
        Money.parse("1.5 JPY")
    assert e.value.code == "E_AMOUNT_PRECISION"
    with pytest.raises(BookflowError) as e:
        Money.parse("1 usd")
    assert e.value.code == "E_VALIDATION"
    with pytest.raises(BookflowError) as e:
        Money.parse("1.00")
    assert e.value.code == "E_VALIDATION"


def test_json_form_and_str():
    m = Money(1250, "USD")
    assert m.to_dict() == {"amount": "12.50", "currency": "USD", "minor_units": 1250}
    assert str(Money(-5, "USD")) == "-0.05 USD"
    assert Money(2345, "JPY").amount == "2345"
    assert Money(1234, "BHD").amount == "1.234"


def test_currency_table_loaded():
    assert CURRENCIES["USD"][0] == 2
    assert CURRENCIES["JPY"][0] == 0
    assert all(code == code.upper() and len(code) == 3 for code in CURRENCIES)
