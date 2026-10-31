"""POS registers (R114 increment 4, design/point-of-sale.md §2): a till's fixed facts.

Off unless company setting `pos_enabled` is on (E_FEATURE_DISABLED). The work lives in ``company/pos_layer.py``.
"""
from bookflow.company import pos_layer
from bookflow.company import pos_models as m
from bookflow.core.registry import Plan, command

_OFF = ' Off unless company setting pos_enabled is on (E_FEATURE_DISABLED).'
_WRITE_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_NAME_TAKEN', 'E_INACTIVE_REFERENCE', 'E_FEATURE_DISABLED']
_VERSION = ('pos-register show', 'pos_register', 'version')


def plan_create(inp, ctx, s) -> Plan:
    return pos_layer.plan_create(s, ctx, inp)


def plan_update(inp, ctx, s) -> Plan:
    return pos_layer.plan_update(s, ctx, inp)


def plan_show(inp, ctx, s) -> Plan:
    return Plan(pos_layer.show(s, inp))


def plan_list(inp, ctx, s) -> Plan:
    return Plan(pos_layer.list_registers(s, inp))


register_create = command(
    'pos-register create', scope='company', description=(
        'Add a POS register (a till). Turning POS on already made Main; a second register is for a second counter '
        'or device. Every field but the name has a default: the default inventory site, Undeposited Funds as the '
        'deposit account, the cash, card and check payment methods in that order, a new walk-in customer '
        '("Walk-in (<name>)"; never shown on the POS screen or receipt), the first bank account for cash refunds, '
        'an 80 mm receipt and no shift counting. A register is not an account: its cash sits in Undeposited Funds '
        'like every other receipt. Needs pos.layout. Example: `bookflow pos-register create --name "Front counter" '
        '--receipt-width 58`.' + _OFF),
    input_model=m.RegisterCreateInput, output_model=m.RegisterWriteOutput, writes={'company'}, required_role='admin',
    capability='pos.layout', accepts_idempotency_key=True, error_codes=_WRITE_ERRORS)(plan_create)
register_create.applier(pos_layer.apply)
register_update = command(
    'pos-register update', scope='company', description=(
        'Change a register under its expected version: name only what changes (name, site, deposit_account, '
        'payment_methods in order, walk_in_customer, refund_account, receipt_width 80 or 58, receipt_message, '
        'count_shifts, decoder_order). Needs pos.layout.' + _OFF),
    input_model=m.RegisterUpdateInput, output_model=m.RegisterWriteOutput, writes={'company'}, required_role='admin',
    capability='pos.layout', accepts_idempotency_key=True, positional=['pos_register'], version_source=_VERSION,
    error_codes=[*_WRITE_ERRORS, 'E_VERSION_CONFLICT'])(plan_update)
register_update.applier(pos_layer.apply)
register_show = command(
    'pos-register show', scope='company', description=(
        'Show a register: its site, deposit account, payment methods in order, walk-in customer, refund account, '
        'receipt width and message, shift counting and decoder order. Needs pos.sell.' + _OFF),
    input_model=m.RegisterShowInput, output_model=m.RegisterOutput, writes=set(), required_role='member',
    capability='pos.sell', positional=['pos_register'],
    error_codes=['E_RECORD_NOT_FOUND', 'E_FEATURE_DISABLED'])(plan_show)
register_list = command(
    'pos-register list', scope='company', description=(
        'The registers by name, each as `pos-register show` gives it. With one register a device never chooses. '
        'Needs pos.sell.' + _OFF),
    input_model=m.RegisterListInput, output_model=m.RegisterPageOutput, writes=set(), required_role='member',
    capability='pos.sell', error_codes=['E_FEATURE_DISABLED'])(plan_list)
