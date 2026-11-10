"""R189 attribution preferences and item flags require no commission money setup."""
import pytest

from bookflow.core.errors import BookflowError

COMPANY = 'Demo Plumbing Co'


def call(client, name, data=None):
    return client.run(name, data or {}, company=COMPANY, reason='Check commission attribution settings')


def test_switch_and_item_flag_survive_disable_without_accounts(client):
    before = call(client, 'company show')
    assert before['info']['commission_attribution_enabled'] is False
    item = call(client, 'item show', {'item': 'Pipe Sealant'})
    assert item['commission_enabled'] is False
    accounts = call(client, 'account list')['items']
    with pytest.raises(BookflowError) as failure:
        call(client, 'item update', {'item': item['id'], 'expected_version': item['version'], 'commission_enabled': True})
    assert failure.value.code == 'E_FEATURE_DISABLED'
    assert call(client, 'item show', {'item': item['id']})['version'] == item['version']
    call(client, 'company update', {'expected_version': before['info_version'], 'commission_attribution_enabled': True})
    enabled = call(client, 'item update', {'item': item['id'], 'expected_version': item['version'], 'commission_enabled': True})
    assert enabled['commission_enabled'] is True
    company = call(client, 'company show')
    call(client, 'company update', {'expected_version': company['info_version'], 'commission_attribution_enabled': False})
    assert call(client, 'item show', {'item': item['id']})['commission_enabled'] is True
    # Ordinary maintenance of an existing flagged item remains possible while the layer is off.
    maintained = call(client, 'item update', {'item': item['id'], 'expected_version': enabled['version'], 'notes': 'Kept while attribution is off'})
    assert maintained['commission_enabled'] is True
    assert call(client, 'account list')['items'] == accounts


def test_invalid_item_type_and_null_switch_refuse_atomically(client):
    before = call(client, 'company show')
    with pytest.raises(BookflowError) as failure:
        call(client, 'company update', {'expected_version': before['info_version'], 'commission_attribution_enabled': None})
    assert failure.value.code == 'E_VALIDATION'
    call(client, 'company update', {'expected_version': before['info_version'], 'commission_attribution_enabled': True})
    for item_type in ('subtotal', 'discount', 'group', 'payment', 'sales_tax_item', 'sales_tax_group', 'fixed_asset'):
        with pytest.raises(BookflowError) as failure:
            call(client, 'item create', {'name': 'Rejected ' + item_type, 'type': item_type, 'commission_enabled': True})
        assert failure.value.code == 'E_VALIDATION'
    assert call(client, 'item list', {'query': 'Rejected '})['items'] == []


def test_explicit_false_is_inert_on_unsupported_item_type(client):
    company = call(client, 'company show')
    call(client, 'company update', {'expected_version': company['info_version'], 'commission_attribution_enabled': True})
    item = call(client, 'item create', {'name': 'Unattributed subtotal', 'type': 'subtotal',
        'description': 'Subtotal without attribution', 'commission_enabled': False})
    assert item['commission_enabled'] is False
    changed = call(client, 'item update', {'item': item['id'], 'expected_version': item['version'],
        'description': 'Still unattributed', 'commission_enabled': False})
    assert changed['commission_enabled'] is False
