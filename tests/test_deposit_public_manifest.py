"""Executable closed-field inventory for the public deposit detail contracts (G7).

No database: this is a pure declaration test over the private reader graph and
the public wire models, with negative controls proving unknown models and
unknown fields fail regardless of spelling.
"""
import pytest
from pydantic import BaseModel

from bookflow.company import deposit_public_manifest as manifest
from bookflow.company import deposit_public_models as w
from bookflow.company import deposit_read_models as m
from bookflow.core.errors import BookflowError


def test_manifest_matches_the_reachable_private_graph_exactly():
    assert manifest.conform() is True
    models = manifest.reachable(manifest._roots())
    assert {manifest._name(model) for model in models} == set(manifest.FIELDS)
    # The complete closure, not just the two output roots.
    assert len(models) == len(manifest.FIELDS) == 48
    assert sum(len(fields) for fields in manifest.FIELDS.values()) == 358


def test_every_field_carries_exactly_one_known_disposition():
    for name, fields in manifest.FIELDS.items():
        for field, (disposition, targets, note) in fields.items():
            assert disposition in manifest.DISPOSITIONS, (name, field)
            if disposition == 'private':
                assert targets == () and note, (name, field)
            elif disposition != 'projected':
                assert targets, (name, field)


def test_the_inspection_guard_and_private_identities_are_never_disclosed():
    """The hard exclusions the owning plan names, asserted on the declaration."""
    excluded = [
        ('deposit_read_models.DependencySummary', 'guard'),
        ('deposit_read_models.EvidenceLink', 'label'),
        ('deposit_read_models.SourceItem', 'membership_ids'),
        ('deposit_read_models.SourceCurrent', 'claim_id'),
        ('deposit_read_models.CellItem', 'id'),
        ('deposit_read_models.CellItem', 'component_id'),
        ('deposit_read_models.CellItem', 'bucket_row_id'),
        ('deposit_models.CashSource', 'business_batch_id'),
        ('deposit_models.CashSource', 'semantic_presence'),
        ('deposit_models.CashComponent', 'document_line_id'),
        ('deposit_models.CashComponent', 'posting_line_id'),
        ('deposit_models.CashComponent', 'posting_source_id'),
        ('deposit_models.CashComponent', 'physical_component_id'),
        ('deposit_models.CashComponent', 'sale_line'),
        ('deposit_models.CashComponent', 'tax'),
        ('payment_outputs.PaymentProfileOutput', 'billing_address'),
        ('payment_outputs.PaymentProfileOutput', 'preferences'),
        ('sales_facts.SalesProfile', 'billing_address'),
        ('sales_facts.SalesProfile', 'shipping_address'),
        ('journal_custom_fields.SnapshotField', 'value_id'),
        ('deposit_read_models.DepositShow', 'fingerprints'),
        ('deposit_read_models.DepositItemPage', 'fingerprint'),
        ('deposit_read_models.DepositItemPage', 'next_cursor'),
    ]
    for model, field in excluded:
        assert manifest.disposition(model, field) == 'private', (model, field)
    # No public model may carry a guard-shaped or private-identity-shaped name.
    for model in manifest.reachable(manifest._public_roots()):
        assert not {'guard', 'membership_ids', 'claim_id', 'component_id', 'bucket_row_id',
                    'read_digest', 'uf_account', 'semantic_presence'} & set(model.model_fields), model.__name__


def test_every_public_wire_model_forbids_unknown_keys_and_is_frozen():
    for model in manifest.reachable(manifest._public_roots()):
        assert model.model_config.get('extra') == 'forbid', model.__name__
        assert model.model_config.get('frozen') is True, model.__name__
        assert model.model_config.get('strict') is True, model.__name__


def _same_name(base, name, module, **fields):
    """A probe class the manifest key cannot tell apart from the real one."""
    body = {'__module__': module, '__annotations__': {k: v for k, v in fields.items()}}
    body.update({k: None for k in fields})
    return type(name, (base,), body)


def test_an_innocuously_named_nested_field_fails_the_closure():
    """Negative control: a plausible new field on a reachable nested model fails."""
    private = 'bookflow.company.deposit_read_models'
    probe_navigation = _same_name(m.Navigation, 'Navigation', private, friendly_hint=str | None)
    swap = {'__module__': private,
            '__annotations__': {'current_references': tuple[probe_navigation, ...]},
            'current_references': ()}
    probe_show = type('DepositShow', (m.DepositShow,), dict(swap))
    probe_page = type('DepositItemPage', (m.DepositItemPage,), dict(swap))
    with pytest.raises(BookflowError) as caught:
        manifest.conform(roots=(probe_show, probe_page))
    assert caught.value.code == 'E_INTERNAL'
    assert caught.value.details['reason'] == 'field set differs for deposit_read_models.Navigation'
    assert caught.value.details['detail'] == ['friendly_hint']


def test_an_unknown_nested_model_fails_the_closure():
    class Sidecar(BaseModel):
        note: str | None = None

    probe = type('DependencySummary', (m.DependencySummary,), {
        '__module__': 'bookflow.company.deposit_read_models',
        '__annotations__': {'sidecar': Sidecar | None},
        'sidecar': None,
    })
    with pytest.raises(BookflowError) as caught:
        manifest.conform(roots=(m.DepositShow, m.DepositItemPage, probe))
    assert caught.value.details['reason'] in ('ambiguous model name',
                                              'model closure differs from the manifest')


def test_a_removed_manifest_field_fails_the_closure():
    edited = {name: dict(fields) for name, fields in manifest.FIELDS.items()}
    edited['deposit_read_models.DependencySummary'].pop('guard')
    with pytest.raises(BookflowError) as caught:
        manifest.conform(fields=edited)
    assert caught.value.details['reason'].startswith('field set differs')
    assert caught.value.details['detail'] == ['guard']


def test_an_unnamed_public_field_fails_the_closure():
    """A guard-shaped public field would need an explicit declared source."""
    probe = _same_name(w.InspectionSummary, 'InspectionSummary',
                       'bookflow.company.deposit_public_models', read_digest=str | None)
    with pytest.raises(BookflowError) as caught:
        manifest.conform(public_roots=(w.DepositDetail, w.DepositItemsPage, probe))
    assert caught.value.details['reason'] == 'public field with no declared source'
    assert caught.value.details['detail'] == ['InspectionSummary.read_digest']


def test_a_stale_declared_construction_fails_the_closure():
    edited = dict(manifest.PUBLIC_CONSTRUCTED)
    edited['DepositDetail.invented'] = 'no such wire field'
    with pytest.raises(BookflowError) as caught:
        manifest.conform(constructed=edited)
    assert caught.value.details['reason'] == 'declared construction for a public field that does not exist'


def test_a_manifest_target_that_no_longer_exists_fails():
    edited = {name: dict(fields) for name, fields in manifest.FIELDS.items()}
    edited['deposit_read_models.DependencySummary']['source_ids'] = (
        'disclosed', ('InspectionSummary.invented',), '')
    with pytest.raises(BookflowError) as caught:
        manifest.conform(fields=edited)
    assert caught.value.details['reason'] == 'manifest names a public field that does not exist'


def test_public_roots_are_reachable_only_through_declared_models():
    """Every public model is reachable from the two registered outputs."""
    names = {model.__name__ for model in manifest.reachable(manifest._public_roots())}
    assert {'DepositDetail', 'DepositItemsPage', 'SourceRow', 'AdditionalRow', 'AllocationRow',
            'PartyReference', 'ClassReference', 'PaymentMethodReference', 'AccountReference',
            'SourceAccountReference', 'CurrentReference', 'IssuerIdentity', 'CustomFieldValue',
            'InspectionSummary', 'AnnotationAccess'} <= names
    assert all(issubclass(model, BaseModel) for model in manifest.reachable(manifest._public_roots()))
