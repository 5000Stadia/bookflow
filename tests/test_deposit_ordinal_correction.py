"""Independent additional-component zero and source-positive contract witnesses."""
import pytest
from pydantic import ValidationError
from bookflow.company import deposits, deposit_validation
from bookflow.company.deposit_models import Cell, ComponentOccurrence, SemanticKey
from bookflow.core.errors import BookflowError
from tests.test_deposit_g1 import additional, intent, dims, nets


def test_additional_zero_exact_money_and_provenance():
    effect = deposits.prepare(intent(additional('one', 1, 1), additional('two', 2, 2),
                                     additional('fee', 3, -1, 'expense'), cash=1))
    assert [(c.row_id, c.component_ordinal, c.bucket, c.units) for c in effect.cells] == [
        ('two', 0, 'cash_back', 1), ('one', 0, 'additional:fee', 1), ('two', 0, 'main_bank', 1)]
    assert (effect.posting_total, effect.subtotal, effect.bank_total) == (3, 2, 1)
    assert nets(effect) == {'cash': 1, 'feeacct': 1, 'bank': 1, 'oneacct': -1, 'twoacct': -2}
    assert {(l.key, l.account_id, l.signed_debit, l.dimensions) for l in effect.legs} == {
        ('cash_back/two/0', 'cash', 1, dims('two')),
        ('additional:fee/one/0', 'feeacct', 1, dims('fee')),
        ('main_bank/two/0', 'bank', 1, dims('two')),
        ('additional:one', 'oneacct', -1, dims('one')),
        ('additional:two', 'twoacct', -2, dims('two'))}
    deposit_validation.validate(effect)
    shifted = effect.model_copy(update={'cells': tuple(c.model_copy(update={'component_ordinal': 1}) for c in effect.cells)})
    with pytest.raises(BookflowError):
        deposit_validation.validate(shifted)


def test_typed_zero_is_cell_only_not_source_occurrence():
    assert Cell(row_id='additional', component_ordinal=0, bucket='main_bank', units=1).component_ordinal == 0
    key = SemanticKey(kind='payment', identity='payment', tax_item='')
    assert ComponentOccurrence(key=key, ordinal=1, present=True).ordinal == 1
    for value in (0, -1, True, 1.0):
        with pytest.raises(ValidationError):
            ComponentOccurrence(key=key, ordinal=value, present=True)
    for value in (-1, True, 0.0):
        with pytest.raises(ValidationError):
            Cell(row_id='additional', component_ordinal=value, bucket='main_bank', units=1)
