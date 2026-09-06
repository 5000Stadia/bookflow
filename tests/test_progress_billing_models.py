"""Public progress intent, historic retry identity and exact proof representations."""
from fractions import Fraction
from math import lcm

import pytest
from pydantic import ValidationError

from bookflow.company.billing import hashes
from bookflow.company.billing_facts import AllocationProof, ExactFraction
from bookflow.company.billing_models import WorkOrderInvoiceInput

BASE = dict(work_order='0'*26, expected_version=1, conversion_key='old-contract', date='2026-09-05')


def proof(**changes):
    return AllocationProof(**(dict(source_document_id='0'*26, source_revision_id='0'*26,
        source_line_id='0'*26, root_document_id='0'*26, root_line_id='0'*26,
        source_basis_hash='a'*64, quoted_quantity_microunits=1,
        quoted_base_quantity_microunits=1, quoted_net_minor_units=100,
        denominator='100000000', spans=[dict(start='0', end='40000000')]) | changes))


def test_existing_permanent_request_hash_is_byte_compatible():
    # Receipt from the independently loaded frozen Row17 build 7d15ef8.
    old = WorkOrderInvoiceInput(**BASE)
    assert hashes(old, 'work_order', 'invoice') == (
        'ef71b039b655088571dd255551b95bddbcf68fb17bde2794afb25e91d1e53331',
        'af9a494c42eaffdf157a230e6624fa724ac8443defc06cdddc1117d0045908d9')
    assert not {'selections', 'percent'} & old.model_dump(mode='json').keys()
    partial = WorkOrderInvoiceInput(**BASE, percent='25')
    assert hashes(partial, 'work_order', 'invoice')[1] != hashes(old, 'work_order', 'invoice')[1]


@pytest.mark.parametrize('selection', [
    {'quantity': '0.25'}, {'net_amount': '40.00'}, {'percent': '25'},
    {'net_amount': {'minor_units': 40, 'currency': 'USD'}}, {'rebill_allocation_id': '1'*26},
])
def test_public_per_line_selection_roundtrips(selection):
    inp = WorkOrderInvoiceInput(**BASE, selections=[dict(line_id='0'*26, **selection)])
    assert WorkOrderInvoiceInput.model_validate_json(inp.model_dump_json(exclude_unset=True)) == inp


@pytest.mark.parametrize('extra', [
    {'percent': None}, {'selections': None}, {'selections': []},
    {'percent': '25', 'line_ids': ['0'*26]}, {'percent': 25},
    {'percent': '100.000001'}, {'percent': '0'}, {'percent': '-1'}, {'percent': '1e1'},
    {'selections': [{'line_id': '0'*26, 'quantity': '0'}]},
    {'selections': [{'line_id': '0'*26, 'quantity': '0.0000001'}]},
    {'selections': [{'line_id': '0'*26, 'quantity': None}]},
    {'selections': [{'line_id': '0'*26, 'quantity': '1', 'percent': '10'}]},
    {'selections': [{'line_id': '0'*26, 'percent': '1'}]*2},
    {'selections': [{'source_line_id': '0'*26, 'percent': '1'}]},
])
def test_ambiguous_or_inexact_selection_rejects(extra):
    with pytest.raises((ValidationError, ValueError)):
        WorkOrderInvoiceInput(**BASE, **extra)


def test_sub_microunit_quantity_and_net_are_not_rounded_substitutes():
    p = proof()
    assert p.quantity() == Fraction(1, 2_500_000)
    assert p.net() == 40
    assert ExactFraction.of(p.quantity()).model_dump() == dict(numerator='1', denominator='2500000')
    d = lcm(3_000_000, 4, 100_000_000)
    installments = [proof(quoted_quantity_microunits=3_000_000,
        quoted_base_quantity_microunits=3_000_000, quoted_net_minor_units=4,
        denominator=str(d), spans=[dict(start=str(a),end=str(b))])
        for a,b in [(0,d//3),(d//3,2*d//3),(2*d//3,d)]]
    assert [p.net() for p in installments] == [1,2,1]
    assert [p.quantity() for p in installments] == [Fraction(1)]*3


@pytest.mark.parametrize('changes', [
    {'denominator': '0'}, {'denominator': '0100000000'}, {'denominator': '100'},
    {'spans': []}, {'spans': [{'start':'0','end':'0'}]},
    {'spans': [{'start':'0','end':'100000001'}]},
    {'spans': [{'start':'0','end':'10'}, {'start':'10','end':'20'}]},
    {'spans': [{'start':'0','end':'10'}, {'start':'9','end':'20'}]},
    {'spans': [{'start':'10','end':'20'}, {'start':'0','end':'5'}]},
    {'spans': [{'start':0,'end':'20'}]},
])
def test_proof_shape_rejects_ambiguous_coordinates(changes):
    with pytest.raises(ValidationError):
        proof(**changes)
