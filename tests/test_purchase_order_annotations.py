"""Purchase order annotations use the stable order identity, without changing the order."""
import io

import pytest

from bookflow import BookflowError
from bookflow.core.ids import new_id
from tests.test_purchase_order import books, _order


def test_order_notes_and_attachment_round_trip(books):
    order = _order(books)
    client, company = books['client'], books['company']
    before = books['run']('purchase-order show', {'purchase_order': order['id']})
    target = dict(record_type='purchase_order', record_id=order['id'], company=company)
    note = client.note.add(**target, body='Deliver at the side gate')['note']
    assert client.note.list(**target)['items'][0]['id'] == note['id']
    payload = b'Supplier quote for June restock\n'
    added = client.attachment.add(**target, original_filename='quote.txt',
                                  media_type='text/plain', input_stream=io.BytesIO(payload))
    assert client.attachment.list(**target)['count'] == 1
    output = io.BytesIO()
    client.attachment.get(company=company, attachment=added['attachment']['id'], output_stream=output)
    assert output.getvalue() == payload
    assert books['run']('purchase-order show', {'purchase_order': order['id']}) == before


def test_missing_and_other_company_orders_reject_annotations(books):
    order = _order(books)
    client = books['client']
    other = client.company.new(legal_name='Other orders', home_currency='USD', timezone='UTC',
                               organization='Orders organization', chart='general')['company_id']
    for company, identity in ((books['company'], new_id()), (other, order['id'])):
        target = dict(company=company, record_type='purchase_order', record_id=identity)
        with pytest.raises(BookflowError) as error:
            client.note.add(**target, body='Wrong order')
        assert error.value.code == 'E_RECORD_NOT_FOUND'
        with pytest.raises(BookflowError) as error:
            client.attachment.add(**target, original_filename='quote.txt', media_type='text/plain',
                                  input_stream=io.BytesIO(b'quote'))
        assert error.value.code == 'E_RECORD_NOT_FOUND'
