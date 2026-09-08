"""Fail when an owning invoice schema grows beyond its historical adapter."""
import importlib
import pytest
from bookflow.hub import audit_projection_legacy as views

# Explicit ownership inventory; additions require a classified adapter change.
OWNERS = (('InvoiceAuditSalesLineInput', 'bookflow.company.sales_models.SalesLineInput'), ('InvoiceAuditSettlementPaymentVersion', 'bookflow.company.sales_models.SettlementPaymentVersion'), ('InvoiceAuditInvoiceUpdateInput', 'bookflow.company.sales_models.InvoiceUpdateInput'), ('InvoiceAuditTaxDetails', 'bookflow.company.tax_attribution.TaxDetails'), ('InvoiceAuditJournalBatchOutput', 'bookflow.company.journal_outputs.JournalBatchOutput'), ('InvoiceAuditBillingSourceLinkOutput', 'bookflow.company.sales_outputs.BillingSourceLinkOutput'), ('InvoiceAuditBillingSourceOutput', 'bookflow.company.sales_outputs.BillingSourceOutput'), ('InvoiceAuditExactFraction', 'bookflow.company.billing_facts.ExactFraction'), ('InvoiceAuditTaxComponentOutput', 'bookflow.company.sales_outputs.TaxComponentOutput'), ('InvoiceAuditSalesLineOutput', 'bookflow.company.sales_outputs.SalesLineOutput'), ('InvoiceAuditSalesRevisionOutput', 'bookflow.company.sales_outputs.SalesRevisionOutput'), ('InvoiceAuditInvoiceCorrectionEffect', 'bookflow.company.payment_outputs.InvoiceCorrectionEffect'), ('InvoiceAuditInvoiceCorrectionOutput', 'bookflow.company.payment_outputs.InvoiceCorrectionOutput'), ('InvoiceAuditWorkBillingSourceEffect', 'bookflow.company.sales_outputs.WorkBillingSourceEffect'), ('InvoiceAuditWorkBillingCurrent', 'bookflow.company.sales_outputs.WorkBillingCurrent'), ('InvoiceAuditForecastReason', 'bookflow.company.tax_forecasts.ForecastReason'), ('InvoiceAuditWorkTaxForecast', 'bookflow.company.tax_forecasts.WorkTaxForecast'), ('InvoiceAuditBillingProgressAmount', 'bookflow.company.sales_outputs.BillingProgressAmount'), ('InvoiceAuditBillingProgressLine', 'bookflow.company.sales_outputs.BillingProgressLine'), ('InvoiceAuditSalesWriteOutput', 'bookflow.company.sales_outputs.SalesWriteOutput'))

@pytest.mark.parametrize('view_name,owner_name', OWNERS)
def test_invoice_operation_owned_fields(view_name,owner_name):
    module,name=owner_name.rsplit('.',1)
    owner=getattr(importlib.import_module(module),name)
    view=getattr(views,view_name)
    expected=set(owner.model_fields)
    if name=='InvoiceUpdateInput':expected.remove('operation_key')
    assert set(view.model_fields)-{'tag','projection_partial'}==expected
