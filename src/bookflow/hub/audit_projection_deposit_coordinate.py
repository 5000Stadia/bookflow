"""Closed coordinate receipt rows; private, without event registration."""
from __future__ import annotations
import json
from typing import Literal
from pydantic import Field, model_validator
from bookflow.hub import audit_projection_legacy as legacy


class CoordinateApplicationAllocationsRow(legacy.ApplicationAllocationView):
    tag: Literal['application_allocation'] = Field('application_allocation', exclude=True)


class CoordinateApplicationsRow(legacy.ApplicationView):
    tag: Literal['application'] = Field('application', exclude=True)


class CoordinateDocumentLineIdentitiesRow(legacy.DocumentLineIdentityView):
    tag: Literal['document_line_identity'] = Field('document_line_identity', exclude=True)


class CoordinateDocumentLinesRow(legacy.DocumentLineView):
    tag: Literal['document_line'] = Field('document_line', exclude=True)


class CoordinatePaymentComponentKeysRow(legacy.PaymentComponentKeyView):
    tag: Literal['payment_component_key'] = Field('payment_component_key', exclude=True)


class CoordinatePaymentComponentsRow(legacy.PaymentComponentView):
    tag: Literal['payment_component'] = Field('payment_component', exclude=True)


class CoordinatePaymentProfilesRow(legacy.PaymentProfileView):
    tag: Literal['payment_profile'] = Field('payment_profile', exclude=True)


class CoordinatePostingBatchesRow(legacy.PostingBatchView):
    tag: Literal['posting_batch'] = Field('posting_batch', exclude=True)


class CoordinatePostingLineSourcesRow(legacy.PostingLineSourceView):
    tag: Literal['posting_line_source'] = Field('posting_line_source', exclude=True)


class CoordinatePostingLinesRow(legacy.PostingLineView):
    tag: Literal['posting_line'] = Field('posting_line', exclude=True)


class CoordinateSalesLineProfilesRow(legacy.SalesLineProfileView):
    tag: Literal['sales_line_profile'] = Field('sales_line_profile', exclude=True)


class CoordinateSalesProfilesRow(legacy.SalesProfileView):
    tag: Literal['sales_profile'] = Field('sales_profile', exclude=True)


class CoordinateSalesTaxAttributionLinesRow(legacy.SalesTaxAttributionLineView):
    tag: Literal['sales_tax_attribution_line'] = Field('sales_tax_attribution_line', exclude=True)


class CoordinateSalesTaxAttributionsRow(legacy.SalesTaxAttributionView):
    tag: Literal['sales_tax_attribution'] = Field('sales_tax_attribution', exclude=True)


class CoordinateSalesTaxComponentsRow(legacy.SalesTaxComponentView):
    tag: Literal['sales_tax_component'] = Field('sales_tax_component', exclude=True)


class CoordinateSalesTaxLineKeysRow(legacy.SalesTaxLineKeyView):
    tag: Literal['sales_tax_line_key'] = Field('sales_tax_line_key', exclude=True)


class CoordinateSettlementLineKeysRow(legacy.SettlementLineKeyView):
    tag: Literal['settlement_line_key'] = Field('settlement_line_key', exclude=True)


class CoordinateTransactionRevisionsRow(legacy.TransactionRevisionView):
    tag: Literal['transaction_revision'] = Field('transaction_revision', exclude=True)


class CoordinateTransactionsRow(legacy.TransactionView):
    tag: Literal['transaction'] = Field('transaction', exclude=True)


class CoordinateWorkBillingAllocationsRow(legacy.WorkBillingAllocationView):
    tag: Literal['work_billing_allocation'] = Field('work_billing_allocation', exclude=True)


class CoordinateSourceRows(legacy.View):
    application_allocations: tuple[CoordinateApplicationAllocationsRow, ...] = ()
    applications: tuple[CoordinateApplicationsRow, ...] = ()
    document_line_identities: tuple[CoordinateDocumentLineIdentitiesRow, ...] = ()
    document_lines: tuple[CoordinateDocumentLinesRow, ...] = ()
    payment_component_keys: tuple[CoordinatePaymentComponentKeysRow, ...] = ()
    payment_components: tuple[CoordinatePaymentComponentsRow, ...] = ()
    payment_profiles: tuple[CoordinatePaymentProfilesRow, ...] = ()
    posting_batches: tuple[CoordinatePostingBatchesRow, ...] = ()
    posting_line_sources: tuple[CoordinatePostingLineSourcesRow, ...] = ()
    posting_lines: tuple[CoordinatePostingLinesRow, ...] = ()
    sales_line_profiles: tuple[CoordinateSalesLineProfilesRow, ...] = ()
    sales_profiles: tuple[CoordinateSalesProfilesRow, ...] = ()
    sales_tax_attribution_lines: tuple[CoordinateSalesTaxAttributionLinesRow, ...] = ()
    sales_tax_attributions: tuple[CoordinateSalesTaxAttributionsRow, ...] = ()
    sales_tax_components: tuple[CoordinateSalesTaxComponentsRow, ...] = ()
    sales_tax_line_keys: tuple[CoordinateSalesTaxLineKeysRow, ...] = ()
    settlement_line_keys: tuple[CoordinateSettlementLineKeysRow, ...] = ()
    transaction_revisions: tuple[CoordinateTransactionRevisionsRow, ...] = ()
    work_billing_allocations: tuple[CoordinateWorkBillingAllocationsRow, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def captured_owner(cls, value):
        from bookflow.company.deposit_coordinate_models import SourceRows
        if type(value) is dict:
            SourceRows.model_validate_json(json.dumps(value, allow_nan=False))
        return value


class CoordinateHeader(legacy.View):
    before: CoordinateTransactionsRow
    after: CoordinateTransactionsRow

    @model_validator(mode="before")
    @classmethod
    def captured_owner(cls, value):
        from bookflow.company.deposit_coordinate_models import CoordinateHeader as Owner
        if type(value) is dict:
            Owner.model_validate_json(json.dumps(value, allow_nan=False))
        return value
