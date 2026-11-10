"""Commission attribution lookup and reporting through the common command registry."""
from bookflow.core.registry import Plan, command
from bookflow.company import commissions
from bookflow.company.commission_models import (
    RecipientOptionsInput, RecipientOptionsOutput, CommissionAttributionInput, CommissionAttributionOutput,
)


@command('commission recipient-options', scope='company',
    description='Find active sales representatives for who sold an invoice line: names and IDs only, with an optional header-rep suggestion. Attribution uses sales-rep list names; no rates, earnings or payments.',
    input_model=RecipientOptionsInput, output_model=RecipientOptionsOutput,
    required_role='standard', capability='commission.attribute', error_codes=['E_RECORD_NOT_FOUND'])
def plan_recipient_options(inp, ctx, s):
    commissions.require_capability(s, 'commission.attribute')
    return Plan(commissions.recipient_options(s, inp))


@command('report commission-attribution', scope='company',
    description='Report final net invoice sales credited to each salesperson or affiliate from captured per-line attribution. Current posted invoices only; historical attribution remains readable when the switch is off. No commission money is calculated.',
    input_model=CommissionAttributionInput, output_model=CommissionAttributionOutput,
    required_role='standard', capability='commission.read', error_codes=['E_RECORD_NOT_FOUND'])
def plan_attribution(inp, ctx, s):
    commissions.require_capability(s, 'commission.read')
    return Plan(commissions.attribution(s, inp))


plan_attribution.resource_requirements = (('customer-work', 'member'),)
