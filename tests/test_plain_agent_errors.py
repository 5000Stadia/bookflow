"""R142: an over-long reason and an over-large deposit answer in plain words an agent can act on."""
import pytest

from bookflow import BookflowError
from tests.test_service_sales_lifecycle import sale, COMPANY  # noqa: F401
from tests.test_payment_receipts import method

LONG = 'x' * 141


def _receipt(client, sale, key):
    return client.run('payment receive', dict(customer=sale['customer'], amount='25.00', date='2026-06-02',
                                              payment_method=method(client), operation_key=key), company=COMPANY)


def _refused(call):
    with pytest.raises(BookflowError) as caught:
        call()
    return caught.value


def test_over_long_reason_on_payment_writes_is_a_plain_field_error(client, sale):
    payment = _receipt(client, sale, 'long-reason-receive')
    for name, body in (
            ('payment void', dict(payment=payment['id'], expected_version=1, operation_key='long-reason-void')),
            ('payment update', dict(payment=payment['id'], expected_version=1, operation_key='long-reason-update',
                                    memo='Changed memo'))):
        error = _refused(lambda: client.run(name, body, reason=LONG, company=COMPANY))
        assert error.code == 'E_VALIDATION', name
        assert error.details['fields'] == [{'field': 'reason', 'problem': 'must be at most 140 characters'}]
        assert '141 characters' in error.message and 'at most 140' in error.message
    # A missing reason still says a reason is needed.
    error = _refused(lambda: client.run('payment void', dict(payment=payment['id'], expected_version=1,
                                                             operation_key='no-reason-void'), company=COMPANY))
    assert error.code == 'E_REASON_REQUIRED'
    # Exactly 140 characters is accepted.
    voided = client.run('payment void', dict(payment=payment['id'], expected_version=1, operation_key='ok-void'),
                        reason='y' * 140, company=COMPANY)
    assert voided['current']['status'] == 'voided'


def test_deposit_over_200_rows_names_the_limit_and_what_to_do(client, sale):
    bank = client.account.create(name='R142 bank', type='bank', company=COMPANY)['id']
    row = dict(received_from=dict(kind='customer', id=sale['customer']), from_account=sale['income'], amount='1.00')
    document = dict(mode='inline', deposit_to=bank, date='2026-06-03', additional=[row] * 201)
    error = _refused(lambda: client.run('deposit post', dict(operation_key='r142-big', document=document),
                                        company=COMPANY))
    assert error.code == 'E_VALIDATION'
    problem = ' '.join(field['problem'] for field in error.details['fields'])
    assert 'at most 200 rows' in problem and 'has 201' in problem and 'second deposit' in problem
    assert 'exceed200' not in problem
