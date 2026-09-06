"""All current custom kinds survive receipt correction and historical replay."""
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import method


def test_all_five_custom_kinds_on_payment_create_edit_history(client, sale):
    definitions = {}
    values = {'text': 'remittance detail', 'number': '12.50', 'date': '2026-06-02', 'bool': False, 'choice': 'Wire'}
    for kind, value in values.items():
        extra = dict(choices=[dict(value='Wire'), dict(value='Cash')]) if kind == 'choice' else {}
        definitions[kind] = client.run('custom-field create', dict(name='Payment ' + kind, kind=kind,
            scopes=['payment'], default=value, **extra), company=COMPANY)['id']
    received = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='1.00',
        payment_method=method(client), operation_key='custom-receipt'), company=COMPANY)
    original = client.run('payment show', dict(payment=received['id']), company=COMPANY)
    snapshot = original['revision']['custom_fields_snapshot']
    assert {row['kind'] for row in snapshot.values()} == set(values)
    client.run('payment update', dict(payment=received['id'], expected_version=1, operation_key='custom-correction',
        custom_fields={definitions['text']: 'Corrected description', definitions['number']: '0', definitions['bool']: True}),
        reason='Correct remittance details', company=COMPANY)
    historical = client.run('payment show', dict(payment=received['id'], revision=1), company=COMPANY)
    assert historical['revision']['custom_fields_snapshot'] == snapshot
    current = client.run('payment show', dict(payment=received['id']), company=COMPANY)['revision']['custom_fields_snapshot']
    assert current[definitions['text']]['value'] == 'Corrected description'
    assert current[definitions['number']]['value'] == '0'
    assert current[definitions['bool']]['value'] is True
