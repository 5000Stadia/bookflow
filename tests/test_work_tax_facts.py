"""Version dispatch and derived-versus-agreed work economics before persistence."""
from copy import deepcopy
import pytest
from pydantic import ValidationError
from bookflow.company.work_facts import WorkLineFacts, WorkTaxComponent
from bookflow.company.work_tax_facts import WorkLineFacts2, read_line, economic_basis, basis_hash
from tests.test_tax_policy_sales import sale, tax_sale, request, COMPANY


def test_document_cells_preserve_legacy_validation_and_economic_basis(client, tax_sale):
    estimate = client.run('estimate create', dict(request(tax_sale,"line_component_half_even"), title='Versioned tax agreement'), company=COMPANY)
    # A version-one DTO witness uses the exact legacy arithmetic policy.
    old = dict(estimate['revision']['lines'][0]['facts'],schema_version=1)
    assert old['schema_version'] == 1 and old['tax_minor_units'] == 0
    bad_legacy = deepcopy(old)
    bad_legacy['taxes'][0]['tax_minor_units'] = 1
    bad_legacy.update(tax_minor_units=1, gross_minor_units=11)
    with pytest.raises(ValidationError):
        read_line(bad_legacy)
    with pytest.raises(ValidationError):
        WorkTaxComponent.model_validate(bad_legacy['taxes'][0])
    allocated = dict(bad_legacy, schema_version=2)
    first = read_line(allocated)
    assert isinstance(first, WorkLineFacts2)
    assert first.tax_minor_units == 1
    other = deepcopy(allocated)
    other['taxes'][0]['tax_minor_units'] = 0
    other['taxes'][1]['tax_minor_units'] = 1
    assert basis_hash(first, 'invoice_combined_half_up') == basis_hash(other, 'invoice_combined_half_up')
    assert basis_hash(first, 'line_combined_half_up') != basis_hash(first, 'invoice_combined_half_up')
    operational = dict(other, completed_quantity_microunits=other['quantity_microunits'], billable=False)
    assert basis_hash(first, 'invoice_combined_half_up') == basis_hash(operational, 'invoice_combined_half_up')
    changed = deepcopy(other); changed['description'] = 'Another agreement'
    assert basis_hash(first, 'invoice_combined_half_up') != basis_hash(changed, 'invoice_combined_half_up')
    payload = economic_basis(first, 'invoice_combined_half_up')
    assert set(payload) == {'basis_version', 'sales_tax_calculation', 'economics', 'tax_rules'}
    assert payload['economics']['profile'] == old['profile']
    assert payload['tax_rules'] == [cell['rule'] for cell in old['taxes']]
    assert not {'taxes','tax_minor_units','gross_minor_units','tax_ordinal','schema_version','completed_quantity_microunits','billable'} & payload['economics'].keys()
    with pytest.raises(ValidationError):
        basis_hash(dict(other, tax_ordinal=3), 'invoice_combined_half_up')
    with pytest.raises(ValidationError):
        basis_hash(other, None)
    assert WorkLineFacts.model_validate(old).model_dump(mode='json') == old


def test_explicit_legacy_work_details_stay_outside_historical_facts(client, tax_sale):
    result = client.run('estimate create', dict(request(tax_sale,"line_component_half_even"), title='Legacy interpretation'), company=COMPANY)
    from bookflow.company.work_outputs import WorkWriteOutput
    saved = deepcopy(result)
    original_facts = deepcopy(saved['revision']['facts'])
    original_lines = deepcopy(saved['revision']['lines'])
    saved['revision'].pop('tax_calculation_details')
    assert WorkWriteOutput.model_validate(saved).model_dump(mode='json') == saved
    client.run('company update', dict(sales_tax_calculation='line_combined_half_up'), company=COMPANY)
    shown = client.run('estimate show', dict(estimate=result['id']), company=COMPANY)
    assert shown['revision']['facts'] == original_facts
    assert shown['revision']['lines'] == original_lines
    detail = shown['revision']['tax_calculation_details']
    assert not detail['legacy_interpretation'] and detail['origin']['kind'] == 'explicit'
    assert detail['policy'] == 'line_component_half_even' and detail['attribution'] is not None
    history = client.run('estimate history', dict(estimate=result['id']), company=COMPANY)
    assert history['items'][0]['tax_calculation_details'] == detail


@pytest.mark.parametrize('part',['root','profile','line'])
@pytest.mark.parametrize('version',[True,1.0,'1',False])
def test_exact_work_discriminator(client,tax_sale,part,version):
    from bookflow.company.work_tax_facts import read_facts
    result=client.run('estimate create',dict(request(tax_sale),title='Strict work versions'),company=COMPANY)
    facts=deepcopy(result['revision']['facts'])
    value=result['revision']['lines'][0]['facts'] if part=='line' else facts
    if part=='profile':value['profile']['schema_version']=version
    else:value['schema_version']=version
    with pytest.raises(ValidationError):(read_line if part=='line' else read_facts)(value)


def test_legacy_work_root_rejects_new_profile_and_inverse(client,tax_sale):
    from bookflow.company.work_tax_facts import read_facts
    result=client.run('estimate create',dict(request(tax_sale),title='Version matrix'),company=COMPANY)
    value=deepcopy(result['revision']['facts']);value['schema_version']=1
    with pytest.raises(ValidationError):read_facts(value)
    value['schema_version']=2;value['profile']['schema_version']=1
    value['profile'].pop('sales_tax_calculation');value['profile'].pop('tax_policy_origin')
    with pytest.raises(ValidationError):read_facts(value)


@pytest.mark.parametrize('changed',['root','line'])
def test_revision_root_line_version_matrix_even_when_cents_match(client,tax_sale,changed):
    from bookflow.company.work_outputs import WorkRevisionOutput
    result=client.run('estimate create',dict(request(tax_sale,nets=('0.01',)),title='Exact line version matrix'),company=COMPANY)
    value=deepcopy(result['revision'])
    assert value['tax_minor_units']==0
    if changed=='line':value['lines'][0]['facts']['schema_version']=1
    else:
        value['facts']['schema_version']=value['facts']['profile']['schema_version']=1
        value['facts']['profile'].pop('sales_tax_calculation');value['facts']['profile'].pop('tax_policy_origin')
    with pytest.raises(ValidationError):WorkRevisionOutput.model_validate(value)
