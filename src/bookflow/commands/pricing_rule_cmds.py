"""Price rules (R110): the anchor's Advanced Pricing price rules, quantity breaks and the conflict preference.

The work lives in ``company/pricing_rules.py``; a sales line asks it for a price through one hook in
``sales_defaults.resolve_line``. Rules share the ``price-level`` capability: standard to write, member
to read.
"""
from bookflow.company import pricing_rules as rules
from bookflow.company import pricing_rule_models as m
from bookflow.core.registry import Plan, command

_CAPABILITY = 'price-level'
_WRITE_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALUE_RANGE',
                 'E_AMOUNT_PRECISION', 'E_NAME_TAKEN']
_VERSION = ('price-rule show', 'rule', 'version')


def _write(name, description, inp, out, planner, applier, *, errors, positional=(), version=None):
    cmd = command(name, scope='company', description=description, input_model=inp, output_model=out,
                  writes={'company'}, required_role='standard', capability=_CAPABILITY, accepts_idempotency_key=True,
                  positional=list(positional), version_source=version, error_codes=errors)(planner)
    cmd.applier(applier)
    return cmd


def _read(name, description, inp, out, planner, *, errors, positional=()):
    return command(name, scope='company', description=description, input_model=inp, output_model=out,
                   writes=set(), required_role='member', capability=_CAPABILITY, positional=list(positional),
                   error_codes=errors)(planner)


def plan_create(inp, ctx, s) -> Plan:
    return rules.plan_create(s, ctx, inp)


def plan_update(inp, ctx, s) -> Plan:
    return rules.plan_update(s, ctx, inp)


def plan_activate(inp, ctx, s) -> Plan:
    return rules.plan_state(s, ctx, inp, True)


def plan_deactivate(inp, ctx, s) -> Plan:
    return rules.plan_state(s, ctx, inp, False)


def plan_show(inp, ctx, s) -> Plan:
    return Plan(rules.rule_show(s, inp))


def plan_list(inp, ctx, s) -> Plan:
    return Plan(rules.rule_list(s, inp))


def plan_preview(inp, ctx, s) -> Plan:
    return Plan(rules.preview(s, inp))


def plan_preferences(inp, ctx, s) -> Plan:
    return Plan(rules.preferences_show(s, inp))


def plan_set_preferences(inp, ctx, s) -> Plan:
    return rules.plan_preferences(s, ctx, inp)


_CONDITIONS = (' Conditions, all of which must hold (an empty list matches all): items (a parent item covers its'
               ' subitems), item_categories, customers (a customer covers its jobs), customer_types, classes (the'
               " line's, else the sale's), sales_reps, the transaction date from date_from through date_to, and"
               ' min_quantity in the item base unit.')
_ADJUST = (' The adjustment starts from the item sales price (basis sales_price) or its cost (basis cost), moves it'
           ' down (a discount) or up (a markup) by a percent (kind percent) or an amount per base unit (kind amount),'
           ' given as value, or as quantity breaks (breaks: [{min_quantity, value}], below the first the rule does not'
           ' apply), never below zero, and rounds it: cent, x.99 or x.95 (up to the next price ending so) or dollar.')

price_rule_create = _write(
    'price-rule create', "Add a price rule, as the anchor's Advanced Pricing does: a sales line whose item, customer,"
    ' class, rep, date and quantity meet its conditions defaults to the rule price.' + _CONDITIONS + _ADJUST +
    ' priority orders rules (1 first; default after every rule). When a price level and rules, or several rules,'
    ' price one line, the company preference (`price-rule set-preferences`) picks the lowest price (the default)'
    ' or the first rule by priority. The rule a line used is captured on it, so later edits never change saved'
    ' documents. Example: 10% off pipe fittings for Acme through December.',
    m.PriceRuleCreateInput, m.PriceRuleWriteOutput, plan_create, rules.apply_rule, errors=_WRITE_ERRORS)
price_rule_update = _write(
    'price-rule update', 'Change a price rule using its expected version; name only what changes (a condition list'
    ' given replaces that list; an empty list matches all). Saved documents keep the rule as they captured it;'
    ' new lines and re-priced lines use the change.',
    m.PriceRuleUpdateInput, m.PriceRuleWriteOutput, plan_update, rules.apply_rule,
    errors=[*_WRITE_ERRORS, 'E_VERSION_CONFLICT'], positional=['rule'], version=_VERSION)
price_rule_activate = _write(
    'price-rule activate', 'Make an inactive price rule price new lines again.',
    m.PriceRuleStateInput, m.PriceRuleWriteOutput, plan_activate, rules.apply_rule,
    errors=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'], positional=['rule'], version=_VERSION)
price_rule_deactivate = _write(
    'price-rule deactivate', 'Stop a price rule pricing new lines (a promotion that has ended early); it is kept,'
    ' and documents that used it keep their prices.',
    m.PriceRuleStateInput, m.PriceRuleWriteOutput, plan_deactivate, rules.apply_rule,
    errors=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'], positional=['rule'], version=_VERSION)
price_rule_show = _read('price-rule show', 'Show a price rule with its conditions, adjustment and a summary in words.',
                        m.PriceRuleSelectInput, m.PriceRuleOutput, plan_show, errors=['E_RECORD_NOT_FOUND'],
                        positional=['rule'])
price_rule_list = _read(
    'price-rule list', 'The price rules in priority order (active only unless include_inactive), with the conflict'
    ' preference; item or customer narrows to the rules that could price that item or customer.',
    m.PriceRuleListInput, m.PriceRulePageOutput, plan_list, errors=['E_RECORD_NOT_FOUND'])
price_rule_preview = _read(
    'price-rule preview', 'What a line for an item, customer:job, quantity and date would default to, and why: the'
    ' item price, the price level price and every matching rule as candidates, the chosen one, and a note such as'
    ' "price from rule Acme fittings: 10% off sales price". Writes nothing.',
    m.PricePreviewInput, m.PricePreviewOutput, plan_preview, errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND'])
price_rule_preferences = _read(
    'price-rule preferences', 'How a line chooses among its price level and matching price rules: lowest_price (the'
    ' default) or priority.', m.PreferencesInput, m.PreferencesOutput, plan_preferences, errors=[])
price_rule_set_preferences = _write(
    'price-rule set-preferences', 'Set how a line chooses among its price level and matching price rules:'
    ' lowest_price (the lowest wins; a tie goes to the price level, then the first rule by priority) or priority (the'
    ' first matching rule by priority wins, over the price level). Saved documents keep their prices.',
    m.PreferencesSetInput, m.PreferencesOutput, plan_set_preferences, rules.apply_preferences, errors=['E_VALIDATION'])
