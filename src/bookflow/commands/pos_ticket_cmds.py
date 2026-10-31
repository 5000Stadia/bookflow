"""Shared held ticket commands; no accounting postings."""
from bookflow.company import pos_tickets as t, pos_ticket_models as m, pos_sales
from bookflow.core.registry import Plan, command

ERRORS = ['E_FEATURE_DISABLED', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_WORK_DEPENDENCY',
          'E_VALIDATION', 'E_INACTIVE_REFERENCE', 'E_PERMISSION', 'E_PERIOD_CLOSED', 'E_VALUE_RANGE', 'E_AMOUNT_PRECISION']


def plan_hold(inp, ctx, s):
    return t.plan_hold(s, ctx, inp)


def plan_list(inp, ctx, s):
    return Plan(t.listing(s))


def plan_resume(inp, ctx, s):
    return t.plan_transition(s, ctx, inp, 'open')


def plan_discard(inp, ctx, s):
    return t.plan_transition(s, ctx, inp, 'discarded')


hold = command('pos ticket hold', scope='company', description=(
    'Hold a cart on the host, shared across devices. Posts nothing and reserves no stock. Optional label defaults '
    'to time and first item. A previously resumed ticket requires its expected_version. Needs pos.sell; explicit '
    'prices follow pos.price-override. Off unless pos_enabled is on.'), input_model=m.HoldInput,
    output_model=m.TicketOutput, writes={'company'}, required_role='member', capability='pos.sell',
    accepts_idempotency_key=True, error_codes=ERRORS)(plan_hold)
hold.applier(t.apply)
hold.authorize_input = pos_sales.authorize
hold.authorization = 'member pos.sell; pos.price-override when a line has unit_price'

listing = command('pos ticket list', scope='company', description=(
    'List company held tickets newest first with register, clerk and last held total. Posts nothing. Needs pos.sell; '
    'off unless pos_enabled is on.'), input_model=m.ListInput, output_model=m.TicketList, writes=set(),
    required_role='member', capability='pos.sell', error_codes=['E_FEATURE_DISABLED'])(plan_list)
listing.authorize_input = t.authorize

resume = command('pos ticket resume', scope='company', description=(
    'Claim a held ticket at expected_version; returns its cart, preserving explicit prices. Preview the returned '
    'cart for today’s prices before charging. The new version is required for completion or another hold. '
    'Posts nothing. Needs pos.sell; off unless pos_enabled is on.'), input_model=m.TicketInput,
    output_model=m.TicketOutput, writes={'company'}, required_role='member', capability='pos.sell',
    positional=['ticket'], accepts_idempotency_key=True, error_codes=['E_FEATURE_DISABLED', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_WORK_DEPENDENCY'])(plan_resume)
resume.applier(t.apply)
resume.authorize_input = t.authorize

discard = command('pos ticket discard', scope='company', description=(
    'Discard a held or resumed ticket at expected_version, keeping its audit history. Posts nothing. Needs '
    'pos.sell; off unless pos_enabled is on.'), input_model=m.TicketInput, output_model=m.TicketOutput,
    writes={'company'}, required_role='member', capability='pos.sell', positional=['ticket'],
    accepts_idempotency_key=True, error_codes=['E_FEATURE_DISABLED', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_WORK_DEPENDENCY'])(plan_discard)
discard.applier(t.apply)
discard.authorize_input = t.authorize
