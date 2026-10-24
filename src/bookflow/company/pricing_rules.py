"""Price rules (R110): the anchor's Advanced Pricing, beyond price levels.

A rule prices a sales line when every condition it names holds:

- the item is one it lists (a parent item covers its subitems) or sits in a category it lists
  (with subcategories);
- the customer:job is one it lists (a customer covers its jobs) or has a customer type it lists
  (the job's own type, else the nearest parent's; with subtypes);
- the line's class (else the sale's) is one it lists (with subclasses);
- the sale's sales rep is one it lists;
- the transaction date is inside ``date_from`` .. ``date_to`` (both inclusive, either open);
- the line's quantity in the item's base unit is at least ``min_quantity``, and at least the first
  quantity break when it has breaks.

An empty list matches everything. The rule then starts from the item's sales price or its cost
(both as captured on the line), moves it down or up by a percent or by an amount per base unit (the
break value for the line's quantity, when it has breaks), never below zero, and rounds it:
``cent`` to the cent (half to even, as price levels round), ``x.99``/``x.95`` up to the next price
ending in .99/.95, ``dollar`` to the nearest whole unit (half to even). Money stays integer minor units
throughout.

Choosing (deterministic). Candidates are each matching rule's price, plus the price level's price
when a price level actually priced the line. Under the company preference ``lowest_price`` (the
default) the lowest candidate wins; a tie goes to the price level, then to the rule first by
priority. Under ``priority`` the first matching rule by priority wins, over the price level. Rules
order by priority (1 first), then name, then id. With no matching rule the line keeps the price level
or item price exactly as before.

``line_price`` is the one hook sales line resolution calls. The rule it chose is captured on the line
(``pricing_rule`` in the line facts: id, name, version, adjustment and the prices before and after),
so a later edit to the rule never changes a saved document; a line re-prices only when its price
would be defaulted again anyway (a new line, a new item, unit, quantity or customer, or refresh).
"""
from __future__ import annotations

import json
from typing import Any, Callable

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company.list_service import resolve_selector
from bookflow.company.lists import get_list_definition, normalize_display_name
from bookflow.company.pricing_rule_models import (
    BreakOutput, Candidate, Named, PreferencesOutput, PricePreviewOutput, PriceRuleOutput,
    PriceRulePageOutput, PriceRuleWriteOutput,
)
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import (
    format_percentage_millionths, format_quantity_micro_units, parse_percentage_millionths,
    parse_quantity_micro_units,
)
from bookflow.core.ids import is_ulid, new_id, normalize_ulid
from bookflow.core.money import Money, minor_units_of
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

RECORD_TYPE = 'price_rule'
PREFERENCE_RECORD_TYPE = 'price_rule_preference'
DEFAULT_POLICY = 'lowest_price'
# condition key -> (list noun, table, hierarchical)
CONDITIONS = {
    'items': ('item', 'items', True),
    'item_categories': ('item-category', 'item_categories', True),
    'customers': ('customer', 'customers', True),
    'customer_types': ('customer-type', 'customer_types', True),
    'classes': ('class', 'classes', True),
    'sales_reps': ('sales-rep', 'sales_reps', False),
}
_EDITABLE = ('name', 'description', 'priority', 'date_from', 'date_to', 'min_quantity_microunits', 'conditions',
             'adjustment_kind', 'direction', 'basis', 'value', 'breaks', 'rounding')


def _invalid(field: str, problem: str) -> BookflowError:
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})


def _home(db) -> str:
    return db.conn.execute(sa.select(c.company_info.c.home_currency)).scalar_one()


def _today(s) -> str:
    from bookflow.company.memorized_schedule import today
    return today(getattr(s, 'company_tz', None))


# ---------------------------------------------------------------------------- values

def parse_value(kind: str, text: str, currency: str, field: str = 'value') -> int:
    if kind == 'percent':
        units = parse_percentage_millionths(text, field=field)
        if not 0 <= units <= 1_000_000_000_000:
            raise BookflowError('E_VALUE_RANGE', details={'fields': [{'field': field, 'problem': 'a percent from 0 through 1000000'}]})
        return units
    parsed = Money.parse({'amount': text, 'currency': currency} if isinstance(text, str) else text)
    if parsed.minor_units < 0:
        raise _invalid(field, 'must not be negative; choose direction down for a discount')
    return parsed.minor_units


def value_text(kind: str, units: int, currency: str) -> str:
    return format_percentage_millionths(units) if kind == 'percent' else Money(units, currency).amount


def _quantity(text: str, field: str) -> int:
    units = parse_quantity_micro_units(text, field=field)
    if units <= 0:
        raise _invalid(field, 'must be above 0')
    return units


def rounding_steps(rounding: str, currency: str) -> tuple[int, int, str]:
    """(increment, offset, mode) for _rounded, as an "up" rule rounds."""
    unit = 10 ** minor_units_of(currency)
    if rounding == 'cent':
        return 1, 0, 'nearest'
    if rounding == 'dollar':
        return unit, 0, 'nearest'
    if unit < 100:
        raise _invalid('rounding', f'{rounding} needs a currency with cents')
    return unit, unit - unit // (100 if rounding == 'x.99' else 20), 'up'


def _rounded(moved: int, percent: int, increment: int, offset: int, mode: str) -> int:
    """sales_calculations.adjusted_price arithmetic, without refusing a result below zero (the caller decides)."""
    from bookflow.core.exact import round_ratio_half_even
    numerator = moved * (100_000_000 + percent) - offset * 100_000_000
    denominator = increment * 100_000_000
    steps = (round_ratio_half_even(numerator, denominator) if mode == 'nearest' else
             -(-numerator // denominator) if mode == 'up' else numerator // denominator)
    return steps * increment + offset


def rule_price(rule: dict, base: int | None, value: int, currency: str) -> int | None:
    """One rule's price per base unit from its basis price, exactly; None when the rule must not apply.

    A rule never makes a line free and never turns a discount into an increase (or a markup into a
    cut): a cost basis of zero or less is no basis; a result at or below zero, before rounding or
    after rounding to the cent or dollar, is skipped, never clamped; a down rule's x.99/x.95 rounds
    down to the next lower ending; an x.99/x.95 ending at or below zero, or any rounding that lands
    on the wrong side of the base, falls back to the cent.
    """
    if base is None or base <= 0 and rule['basis'] == 'cost':
        return None
    down = rule['direction'] == 'down'
    sign = -1 if down else 1
    if rule['adjustment_kind'] == 'percent':
        percent, moved = sign * value, base
        if percent <= -100_000_000:
            return None
    else:
        percent, moved = 0, base + sign * value
        if moved <= 0:
            return None
    increment, offset, mode = rounding_steps(rule['rounding'], currency)
    if down and rule['rounding'] in ('x.99', 'x.95'):
        mode = 'down'
    rounded = _rounded(moved, percent, increment, offset, mode)
    if rounded <= 0 and rule['rounding'] in ('cent', 'dollar'):
        return None  # free is never a rule's price
    if rounded <= 0 or (rounded > base if down else rounded < base):
        rounded = _rounded(moved, percent, 1, 0, 'nearest')
    return rounded if rounded > 0 else None


def _tier(rule: dict, quantity: int | None) -> int | None:
    """Which quantity break (by position) a quantity falls in; None below the first or with no breaks."""
    if rule['breaks'] is None or quantity is None:
        return None
    found = None
    for index, tier in enumerate(rule['breaks']):
        if quantity >= tier['min_quantity_microunits']:
            found = index
    return found


def _break_value(rule: dict, quantity: int) -> int | None:
    if rule['breaks'] is None:
        return rule['value']
    chosen = None
    for tier in rule['breaks']:
        if quantity >= tier['min_quantity_microunits']:
            chosen = tier['value']
    return chosen


def summary(rule: dict, currency: str, names: dict[str, list[Named]] | None = None) -> str:
    kind, direction = rule['adjustment_kind'], rule['direction']
    word = 'off' if direction == 'down' else 'over'
    basis = 'sales price' if rule['basis'] == 'sales_price' else 'cost'

    def amount(units):
        return (format_percentage_millionths(units) + '%') if kind == 'percent' else Money(units, currency).amount

    if rule['breaks'] is None:
        text = f"{amount(rule['value'])} {word} {basis}"
    else:
        text = '; '.join(f"{format_quantity_micro_units(t['min_quantity_microunits'])}+ at {amount(t['value'])} {word}"
                         for t in rule['breaks']) + f' {basis}'
    scope = []
    named = []
    for key, label in (('items', 'items'), ('item_categories', 'categories'), ('customers', 'customers'),
                       ('customer_types', 'customer types'), ('classes', 'classes'), ('sales_reps', 'reps')):
        chosen = (names or {}).get(key) or []
        if chosen:
            named.append(f"{label} {', '.join(n.label for n in chosen)}")
    if named:
        text += ' for ' + ', '.join(named)
    if names is not None and rule.get('min_quantity_microunits'):
        scope.append(f"quantity {format_quantity_micro_units(rule['min_quantity_microunits'])} and over")
    if names is not None and (rule.get('date_from') or rule.get('date_to')):
        scope.append(f"{rule.get('date_from') or 'any date'} through {rule.get('date_to') or 'any date'}")
    rounding = {'cent': 'rounded to the cent', 'x.99': 'rounded up to .99', 'x.95': 'rounded up to .95',
                'dollar': 'rounded to the nearest whole unit'}[rule['rounding']]
    return '; '.join([text, *scope, rounding])


# ---------------------------------------------------------------------------- rows

def _decode(row) -> dict:
    out = dict(row)
    out['conditions'] = json.loads(out['conditions'])
    out['breaks'] = json.loads(out['breaks']) if out['breaks'] is not None else None
    return out


def _encode(rule: dict) -> dict:
    row = dict(rule)
    row['conditions'] = json.dumps(rule['conditions'], sort_keys=True, separators=(',', ':'))
    row['breaks'] = (json.dumps(rule['breaks'], sort_keys=True, separators=(',', ':'))
                     if rule['breaks'] is not None else None)
    return row


def resolve_rule(s, selector: str) -> dict:
    t = c.price_rules
    text = selector.strip()
    row = None
    if is_ulid(text):
        row = s.company.conn.execute(sa.select(t).where(t.c.id == normalize_ulid(text))).mappings().first()
    if row is None and text:
        _, key = normalize_display_name(text, field='rule')
        row = s.company.conn.execute(sa.select(t).where(t.c.name_key == key)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': RECORD_TYPE, 'selector': selector, 'field': 'rule'})
    return _decode(row)


def _name(s, name: str, own: str | None = None) -> tuple[str, str]:
    display, key = normalize_display_name(name, field='name')
    query = sa.select(c.price_rules.c.id).where(c.price_rules.c.name_key == key)
    if own:
        query = query.where(c.price_rules.c.id != own)
    clash = s.company.conn.execute(query).first()
    if clash:
        raise BookflowError('E_NAME_TAKEN', details={'record_type': RECORD_TYPE, 'name': display, 'record_id': clash[0]})
    return display, key


def _find(db, key, table, noun, selector):
    if key == 'sales_reps':
        # A rep is known by initials as often as by name.
        from bookflow.company.list_service import normalize_lookup_key
        found = db.conn.execute(sa.select(table).where(table.c.initials_key == normalize_lookup_key(selector))).mappings().first()
        if found is not None:
            return dict(found)
    return resolve_selector(db, table, get_list_definition(noun), selector)


def _resolve_ids(s, key: str, selectors: list[str], *, require_active: bool) -> list[str]:
    noun, table_name, _ = CONDITIONS[key]
    table = getattr(c, table_name)
    ids = []
    for selector in selectors:
        row = _find(s.company, key, table, noun, selector)
        if require_active and not row.get('active', True):
            raise BookflowError('E_INACTIVE_REFERENCE', details={'field': key, 'record_id': row['id']})
        if row['id'] not in ids:
            ids.append(row['id'])
    return sorted(ids)


def _names(s, conditions: dict) -> dict[str, list[Named]]:
    out = {}
    for key, (_, table_name, hierarchical) in CONDITIONS.items():
        ids = conditions.get(key) or []
        if not ids:
            out[key] = []
            continue
        table = getattr(c, table_name)
        label = table.c.full_name if hierarchical else table.c.name
        found = dict(s.company.conn.execute(sa.select(table.c.id, label).where(table.c.id.in_(ids))).all())
        out[key] = [Named(id=i, label=found.get(i, i)) for i in ids]
    return out


def rule_output(s, rule: dict, currency: str | None = None, cls=PriceRuleOutput, **extra) -> PriceRuleOutput:
    currency = currency or _home(s.company)
    names = _names(s, rule['conditions'])
    kind = rule['adjustment_kind']
    return cls(id=rule['id'], version=rule['version'], name=rule['name'], description=rule['description'],
               active=bool(rule['active']), priority=rule['priority'], date_from=rule['date_from'],
               date_to=rule['date_to'],
               min_quantity=(format_quantity_micro_units(rule['min_quantity_microunits'])
                             if rule['min_quantity_microunits'] is not None else None),
               **names, kind=kind, direction=rule['direction'], basis=rule['basis'],
               value=value_text(kind, rule['value'], currency) if rule['value'] is not None else None,
               breaks=[BreakOutput(min_quantity=format_quantity_micro_units(t['min_quantity_microunits']),
                                   value=value_text(kind, t['value'], currency)) for t in rule['breaks']]
               if rule['breaks'] is not None else None,
               rounding=rule['rounding'], currency=currency, summary=summary(rule, currency, names), **extra)


# ---------------------------------------------------------------------------- writes

def _version_check(row: dict, expected: int | None) -> None:
    if expected is not None and expected != row['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': RECORD_TYPE, 'record_id': row['id'], 'current_version': row['version'],
            'expected_version': expected, 'updated_at': row['updated_at'], 'updated_by': row['updated_by']})


def _next_priority(s) -> int:
    return (s.company.conn.execute(sa.select(sa.func.max(c.price_rules.c.priority))).scalar() or 0) + 1


def _apply_fields(s, rule: dict, inp, currency: str) -> dict:
    """Fold supplied fields into the rule and validate the whole."""
    given = inp.model_fields_set
    if 'description' in given:
        rule['description'] = (inp.description or '').strip() or None
    if inp.priority is not None:
        rule['priority'] = inp.priority
    for field in ('date_from', 'date_to'):
        if field in given:
            rule[field] = getattr(inp, field)
    if 'min_quantity' in given:
        rule['min_quantity_microunits'] = (_quantity(inp.min_quantity, 'min_quantity')
                                           if inp.min_quantity not in (None, '') else None)
    conditions = dict(rule['conditions'])
    for key in CONDITIONS:
        if key in given:
            conditions[key] = _resolve_ids(s, key, getattr(inp, key) or [], require_active=True)
    rule['conditions'] = {key: conditions.get(key, []) for key in CONDITIONS}
    for field, column in (('kind', 'adjustment_kind'), ('direction', 'direction'), ('basis', 'basis'),
                          ('rounding', 'rounding')):
        if getattr(inp, field) is not None:
            rule[column] = getattr(inp, field)
    kind = rule['adjustment_kind']
    if inp.value is not None and inp.breaks is not None:
        raise _invalid('value', 'give value or breaks, not both')
    if inp.value is not None:
        rule['value'], rule['breaks'] = parse_value(kind, inp.value, currency), None
    elif inp.breaks is not None:
        if not inp.breaks:
            raise _invalid('breaks', 'give at least one quantity break, or a value')
        tiers = []
        for index, tier in enumerate(inp.breaks):
            tiers.append({'min_quantity_microunits': _quantity(tier.min_quantity, f'breaks.{index}.min_quantity'),
                          'value': parse_value(kind, tier.value, currency, f'breaks.{index}.value')})
        quantities = [t['min_quantity_microunits'] for t in tiers]
        if quantities != sorted(set(quantities)):
            raise _invalid('breaks', 'quantity breaks rise strictly from one to the next')
        rule['value'], rule['breaks'] = None, tiers
    elif 'kind' in given and kind != rule.get('_kind_before'):
        raise _invalid('value', 'changing between percent and amount needs the new value or breaks')
    if rule['value'] is None and rule['breaks'] is None:
        raise _invalid('value', 'give the percent or amount as value, or quantity breaks')
    if kind == 'percent' and rule['direction'] == 'down':
        for units in [rule['value']] if rule['breaks'] is None else [t['value'] for t in rule['breaks']]:
            if units > 100_000_000:
                raise BookflowError('E_VALUE_RANGE', details={'fields': [{'field': 'value', 'problem': 'a discount of at most 100 percent'}]})
    if rule['date_from'] and rule['date_to'] and rule['date_from'] > rule['date_to']:
        raise _invalid('date_to', 'must be on or after date_from')
    rounding_steps(rule['rounding'], currency)
    rule.pop('_kind_before', None)
    return rule


def plan_create(s, ctx, inp) -> Plan:
    currency = _home(s.company)
    display, key = _name(s, inp.name)
    rule = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()), name=display, name_key=key,
                description=None, active=True, priority=_next_priority(s), date_from=None, date_to=None,
                min_quantity_microunits=None, conditions={k: [] for k in CONDITIONS}, adjustment_kind='percent',
                direction='down', basis='sales_price', value=None, breaks=None, rounding='cent')
    rule = _apply_fields(s, rule, inp, currency)
    return Plan(rule_output(s, rule, currency, cls=PriceRuleWriteOutput), dict(row=rule, before=None))


def _bumped(rule: dict, s, ctx) -> dict:
    return dict(rule, version=rule['version'] + 1, updated_at=clock.now_iso(), updated_by=s.actor.id,
                updated_via=ctx.interface.value)


def plan_update(s, ctx, inp) -> Plan:
    currency = _home(s.company)
    old = resolve_rule(s, inp.rule)
    _version_check(old, inp.expected_version)
    new = dict(old, _kind_before=old['adjustment_kind'])
    if inp.name is not None:
        new['name'], new['name_key'] = _name(s, inp.name, old['id'])
    new = _apply_fields(s, new, inp, currency)
    changed = any(new[k] != old[k] for k in _EDITABLE)
    if changed:
        new = _bumped(new, s, ctx)
    out = rule_output(s, new, currency, cls=PriceRuleWriteOutput, changed=changed)
    return Plan(out, dict(row=new, before=old if changed else None, changed=changed))


def plan_state(s, ctx, inp, active: bool) -> Plan:
    old = resolve_rule(s, inp.rule)
    _version_check(old, inp.expected_version)
    changed = bool(old['active']) != active
    new = _bumped(dict(old, active=active), s, ctx) if changed else old
    out = rule_output(s, new, cls=PriceRuleWriteOutput, changed=changed)
    return Plan(out, dict(row=new, before=old if changed else None, changed=changed,
                          action='activate' if active else 'deactivate'))


def apply_rule(plan: Plan, ctx, s) -> Applied:
    row, before = plan.data['row'], plan.data['before']
    if before is None and plan.data.get('changed') is False:
        return Applied(plan.preview, [], 'no change')
    stored = _encode(row)
    if before is None:
        s.company.conn.execute(c.price_rules.insert().values(**stored))
        return Applied(plan.preview, [Touched(RECORD_TYPE, row['id'], 'create', None, 1, stored, db='company')],
                       f"added price rule {row['name']}")
    done = s.company.conn.execute(c.price_rules.update().where(
        c.price_rules.c.id == row['id'], c.price_rules.c.version == before['version']).values(**stored)).rowcount
    if done != 1:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_type': RECORD_TYPE, 'record_id': row['id']})
    action = plan.data.get('action', 'update')
    return Applied(plan.preview, [Touched(RECORD_TYPE, row['id'], action, before['version'], row['version'], stored,
                                          _encode(before), db='company')], f"{action}d price rule {row['name']}"
                   if action != 'update' else f"updated price rule {row['name']}")


# ---------------------------------------------------------------------------- reads

def _ancestors(path: str | None) -> set[str]:
    return set(path.split('/')) if path else set()


def rule_show(s, inp) -> PriceRuleOutput:
    return rule_output(s, resolve_rule(s, inp.rule))


def rule_list(s, inp) -> PriceRulePageOutput:
    t = c.price_rules
    query = sa.select(t).order_by(t.c.priority, t.c.name_key, t.c.id)
    if not inp.include_inactive:
        query = query.where(t.c.active.is_(True))
    rules = [_decode(r) for r in s.company.conn.execute(query).mappings()]
    if inp.item is not None or inp.customer is not None:
        facts = _facts(s, item=inp.item, customer=inp.customer)
        keys = (('items', 'item_categories') if inp.item is not None else ()) + (
            ('customers', 'customer_types') if inp.customer is not None else ())
        rules = [r for r in rules if _could_match(r, facts, keys)]
    currency = _home(s.company)
    return PriceRulePageOutput(rules=[rule_output(s, r, currency) for r in rules], conflict_policy=conflict_policy(s.company))


def _preference_row(db) -> dict | None:
    row = db.conn.execute(sa.select(c.price_rule_preferences)).mappings().first()
    return dict(row) if row else None


def conflict_policy(db) -> str:
    row = _preference_row(db)
    return row['conflict_policy'] if row else DEFAULT_POLICY


def preferences_show(s, inp) -> PreferencesOutput:
    row = _preference_row(s.company)
    return PreferencesOutput(conflict_policy=row['conflict_policy'] if row else DEFAULT_POLICY,
                             version=row['version'] if row else 0)


def plan_preferences(s, ctx, inp) -> Plan:
    old = _preference_row(s.company)
    if old is None:
        new = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()), singleton=1,
                   conflict_policy=inp.conflict_policy)
        changed = True
    else:
        changed = old['conflict_policy'] != inp.conflict_policy
        new = _bumped(dict(old, conflict_policy=inp.conflict_policy), s, ctx) if changed else old
    out = PreferencesOutput(conflict_policy=new['conflict_policy'], version=new['version'], changed=changed)
    return Plan(out, dict(row=new, before=old if (changed and old) else None, changed=changed))


def apply_preferences(plan: Plan, ctx, s) -> Applied:
    row, before = plan.data['row'], plan.data['before']
    if not plan.data['changed']:
        return Applied(plan.preview, [], 'no change')
    t = c.price_rule_preferences
    if before is None:
        s.company.conn.execute(t.insert().values(**row))
        touched = Touched(PREFERENCE_RECORD_TYPE, row['id'], 'create', None, 1, row, db='company')
    else:
        if s.company.conn.execute(t.update().where(t.c.id == row['id'], t.c.version == before['version'])
                                  .values(**row)).rowcount != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_type': PREFERENCE_RECORD_TYPE, 'record_id': row['id']})
        touched = Touched(PREFERENCE_RECORD_TYPE, row['id'], 'update', before['version'], row['version'], row, before,
                          db='company')
    return Applied(plan.preview, [touched], f"price rule conflicts now choose by {row['conflict_policy'].replace('_', ' ')}")


# ---------------------------------------------------------------------------- resolution

_SIGNATURE = sa.text("SELECT count(*), coalesce(max(updated_at), ''), coalesce(sum(version), 0), sum(active),"
                     " (SELECT count(*) || '/' || count(undone_at) FROM list_merges WHERE list_kind = 'item'),"
                     " (SELECT count(*) || '/' || count(undone_at) FROM party_merges WHERE party_kind = 'customer')"
                     " FROM price_rules")


def _through_merges(db, rules: list[dict]) -> list[dict]:
    """Rules as they match today: an item or customer merged away (R117) reads as its survivor, so a
    rule naming it keeps pricing what it was merged into, and stops doing so once the merge is undone."""
    items = dict(db.conn.execute(sa.text("SELECT merged_id, survivor_id FROM list_merges WHERE list_kind = 'item'"
                                         " AND undone_at IS NULL")).all())
    customers = dict(db.conn.execute(sa.text("SELECT merged_id, survivor_id FROM party_merges WHERE party_kind ="
                                             " 'customer' AND undone_at IS NULL")).all())
    if not items and not customers:
        return rules
    out = []
    for rule in rules:
        conditions = dict(rule['conditions'])
        for key, alias in (('items', items), ('customers', customers)):
            conditions[key] = sorted({alias.get(i, i) for i in conditions.get(key) or []})
        out.append(dict(rule, conditions=conditions))
    return out


def active_rules(db) -> list[dict]:
    """The active rules in priority order. A line resolves several times per command (preview, plan,
    checks), so the decoded list is kept on the open database until any rule is added or changed."""
    signature = tuple(db.conn.execute(_SIGNATURE).one())
    cached = getattr(db, '_bookflow_price_rules', None)
    if cached is not None and cached[0] == signature:
        return cached[1]
    t = c.price_rules
    rules = _through_merges(db, [_decode(r) for r in db.conn.execute(
        sa.select(t).where(t.c.active.is_(True)).order_by(t.c.priority, t.c.name_key, t.c.id)).mappings()])
    try:
        db._bookflow_price_rules = (signature, rules)
    except AttributeError:
        pass
    return rules


def _facts(s_or_db, *, item=None, customer=None, item_id=None, customer_id=None, class_id=None, sales_rep_id=None,
           quantity=None, date=None) -> dict:
    """What the conditions test, as sets of the ids (with ancestors) a line has."""
    db = getattr(s_or_db, 'company', s_or_db)
    if item is not None:
        item_id = resolve_selector(db, c.items, get_list_definition('item'), item)['id']
    if customer is not None:
        customer_id = resolve_selector(db, c.customers, get_list_definition('customer'), customer)['id']
    facts: dict[str, Any] = {'quantity': quantity, 'date': date, 'sales_reps': {sales_rep_id} if sales_rep_id else set()}
    conn = db.conn
    item_row = conn.execute(sa.select(c.items.c.path, c.items.c.category_id).where(c.items.c.id == item_id)).first() if item_id else None
    facts['items'] = _ancestors(item_row.path) if item_row else set()
    category = item_row.category_id if item_row else None
    facts['item_categories'] = _ancestors(conn.execute(sa.select(c.item_categories.c.path).where(
        c.item_categories.c.id == category)).scalar()) if category else set()
    customer_row = conn.execute(sa.select(c.customers.c.path).where(c.customers.c.id == customer_id)).first() if customer_id else None
    facts['customers'] = _ancestors(customer_row.path) if customer_row else set()
    types = set()
    if customer_row:
        # The job's own customer type, else its nearest parent's.
        lineage = customer_row.path.split('/')
        found = dict(conn.execute(sa.select(c.customers.c.id, c.customers.c.customer_type_id)
                                  .where(c.customers.c.id.in_(lineage))).all())
        own = next((found[i] for i in reversed(lineage) if found.get(i)), None)
        if own:
            types = _ancestors(conn.execute(sa.select(c.customer_types.c.path).where(c.customer_types.c.id == own)).scalar())
    facts['customer_types'] = types
    facts['classes'] = _ancestors(conn.execute(sa.select(c.classes.c.path).where(c.classes.c.id == class_id)).scalar()) if class_id else set()
    return facts


def _could_match(rule: dict, facts: dict, keys: tuple[str, ...]) -> bool:
    return all(not rule['conditions'].get(key) or set(rule['conditions'][key]) & facts[key] for key in keys)


def matches(rule: dict, facts: dict) -> bool:
    for key in CONDITIONS:
        wanted = rule['conditions'].get(key) or []
        if wanted and not (set(wanted) & facts[key]):
            return False
    date = facts['date']
    if (rule['date_from'] and date < rule['date_from']) or (rule['date_to'] and date > rule['date_to']):
        return False
    if rule['min_quantity_microunits'] and facts['quantity'] < rule['min_quantity_microunits']:
        return False
    return _break_value(rule, facts['quantity']) is not None


def candidates_for(rules: list[dict], facts: dict, *, standard: int | None, cost: int | None,
                   currency: str) -> list[tuple]:
    out = []
    for rule in rules:
        if not matches(rule, facts):
            continue
        value = _break_value(rule, facts['quantity'])
        price = rule_price(rule, standard if rule['basis'] == 'sales_price' else cost, value, currency)
        if price is not None:
            out.append((rule, standard if rule['basis'] == 'sales_price' else cost, value, price))
    return out


def choose(rules: list[dict], facts: dict, *, standard: int | None, cost: int | None, level: int | None,
           level_priced: bool, policy: str, currency: str,
           level_explicit: bool = False) -> tuple[int | None, dict | None, list[tuple]]:
    """(price, winning rule capture or None, candidates). ``level`` is the price without rules.

    lowest_price: the lowest rule price wins against the price level's price when a level priced the
    line, and a discount rule also against the item's own price, so no discount ever prices above
    the standard price; a tie goes to the level or item. priority: the first matching rule wins,
    except over a price level picked on the line itself, which stands (as a price chosen on the
    line stands in the anchor)."""
    candidates = candidates_for(rules, facts, standard=standard, cost=cost, currency=currency)
    if not candidates:
        return level, None, []
    if policy == 'priority':
        winner = None if (level_explicit and level_priced) else candidates[0]  # rules come in priority order
    else:
        winner = min(candidates, key=lambda x: x[3])  # the first of equal prices is the first by priority
        floors = [level] if level_priced and level is not None else []
        if winner[0]['direction'] == 'down' and standard is not None:
            floors.append(standard)
        if floors and min(floors) <= winner[3]:
            winner = None
    if winner is None:
        return level, None, candidates
    return winner[3], dict(rule=winner[0], base=winner[1], value=winner[2], price=winner[3]), candidates


def capture(chosen: dict, currency: str, policy: str):
    from bookflow.company.sales_facts import AppliedPriceRule
    rule = chosen['rule']
    one = dict(rule, value=chosen['value'], breaks=None)
    return AppliedPriceRule(
        id=rule['id'], label=rule['name'], version=rule['version'], priority=rule['priority'],
        kind=rule['adjustment_kind'], direction=rule['direction'], basis=rule['basis'], rounding=rule['rounding'],
        value=chosen['value'], base_minor_units=chosen['base'], price_minor_units=chosen['price'],
        conflict_policy=policy, summary=f"price from rule {rule['name']}: {summary(one, currency)}")


def _line_facts(s, db, profile, header, base_qty, date):
    class_ref = profile.class_id or header.class_id
    ids = (profile.item.id, header.customer.id, class_ref.id if class_ref else None,
           header.sales_rep.id if header.sales_rep else None)
    # The lists a line's facts come from cannot change while nothing is written: a 200-line sale
    # resolves each line several times, so they are kept until this connection writes again.
    raw = getattr(db, 'raw', None)
    key = getattr(raw, 'total_changes', None)
    cached = getattr(db, '_bookflow_price_facts', None)
    if cached is None or cached[0] != key or key is None:
        cached = (key, {}, _home(db), conflict_policy(db))
        try:
            db._bookflow_price_facts = cached
        except AttributeError:
            pass
    if ids not in cached[1]:
        cached[1][ids] = _facts(db, item_id=ids[0], customer_id=ids[1], class_id=ids[2], sales_rep_id=ids[3])
    return dict(cached[1][ids], quantity=base_qty, date=date or _today(s)), cached[2], cached[3]


def _kept(previous, rules, facts, profile, old_base_qty, currency):
    """The price a line keeps under the rule it captured, while that rule still matches it: the same
    rule, still active, its conditions still holding, and the quantity still in the same break. The
    captured adjustment is re-applied, never today's definition of the rule."""
    current = next((rule for rule in rules if rule['id'] == previous.id), None)
    if current is None or not matches(current, facts):
        return None
    if current['breaks'] is not None and _tier(current, old_base_qty) != _tier(current, facts['quantity']):
        return None
    captured = dict(adjustment_kind=previous.kind, direction=previous.direction, rounding=previous.rounding,
                    basis=previous.basis)
    base = profile.standard_price_minor_units if previous.basis == 'sales_price' else profile.cost_minor_units
    price = rule_price(captured, base, previous.value, currency)
    if price is None:
        return None
    return price, previous.model_copy(update=dict(base_minor_units=base, price_minor_units=price))


def line_price(s, profile, header, base_qty: int, date: str | None, level_price: Callable[[], int], *,
               old_base_qty: int | None = None, level_explicit: bool = False) -> int:
    """The R110 hook: the base-unit price a line defaults to, with the rule it came from on ``profile``.

    ``level_price`` gives the price without rules (the price level's, else the item's). With no
    active rule and no captured rule this is exactly ``level_price()``. A line that captured a rule
    keeps that rule's captured adjustment while the rule still matches (``old_base_qty`` is the
    quantity it was priced at); otherwise it is priced from today's rules.
    """
    db = s.company
    previous, profile.pricing_rule = profile.pricing_rule, None
    rules = active_rules(db)
    if not rules:
        return level_price()
    facts, currency, policy = _line_facts(s, db, profile, header, base_qty, date)
    if previous is not None:
        kept = _kept(previous, rules, facts, profile, old_base_qty, currency)
        if kept is not None:
            profile.pricing_rule = kept[1]
            return kept[0]
    try:
        level, failure = level_price(), None
    except BookflowError as exc:
        level, failure = None, exc
    level_priced = bool(profile.price_rule and profile.price_rule.matched)
    price, chosen, _ = choose(rules, facts, standard=profile.standard_price_minor_units, cost=profile.cost_minor_units,
                              level=level, level_priced=level_priced, policy=policy, currency=currency,
                              level_explicit=level_explicit)
    if chosen is None:
        if failure is not None:
            raise failure
        return level
    profile.pricing_rule = capture(chosen, currency, policy)
    return price


def preview(s, inp) -> PricePreviewOutput:
    """What a line for this item, customer:job, quantity and date would be priced at, and why.

    It resolves the line exactly as posting an invoice does (the same header and line resolution:
    the job's sales rep and price level, the item's or customer's default class, units), with sales
    tax left out because it does not touch the price, and then lists the candidates it chose among."""
    from bookflow.company import sales_defaults
    from bookflow.company.sales_calculations import selected_price
    from bookflow.core import registry
    db = s.company
    date = inp.date or _today(s)
    raw = dict(customer=inp.customer, date=date, lines=[dict(item=inp.item, quantity=inp.quantity)])
    for field in ('class_id', 'sales_rep', 'price_level'):
        if getattr(inp, field):
            raw[field] = getattr(inp, field)
    entered = registry.get('invoice post').input_model.model_validate(raw)
    header, _ = sales_defaults.resolve_header(s, entered, 'invoice')
    untaxed = header.model_copy(update=dict(preferences=header.preferences.model_copy(update=dict(sales_tax_enabled=False))))
    resolved, _ = sales_defaults.resolve_line(s, entered.lines[0], untaxed, date=date, defer_tax=True, line_kinds=True)
    profile, factor = resolved['profile'], resolved['unit_factor_nanounits']
    if profile.adjustment is not None or resolved['unit_price_minor_units'] is None:
        raise _invalid('item', 'only a sold item has a price to preview')
    currency, policy = _home(db), conflict_policy(db)
    shown = lambda units: Money(selected_price(units, factor), currency).amount  # noqa: E731
    standard, cost = profile.standard_price_minor_units, profile.cost_minor_units
    level_priced = bool(profile.price_rule and profile.price_rule.matched)
    level = None
    if level_priced:
        level = sales_defaults._derived_price(profile.model_copy(update=dict(pricing_rule=None)))
    class_ref = profile.class_id or header.class_id
    facts = _facts(db, item_id=profile.item.id, customer_id=header.customer.id,
                   class_id=class_ref.id if class_ref else None,
                   sales_rep_id=header.sales_rep.id if header.sales_rep else None,
                   quantity=resolved['base_quantity_microunits'], date=date)
    chosen_id = profile.pricing_rule.id if profile.pricing_rule else None
    source = 'rule' if chosen_id else 'price_level' if level_priced else 'item'
    rows = []
    if standard is not None:
        rows.append(Candidate(source='item', id=profile.item.id, name=profile.item.label, unit_price=shown(standard),
                              chosen=source == 'item'))
    if level_priced:
        rows.append(Candidate(source='price_level', id=profile.price_rule.id, name=profile.price_rule.label,
                              unit_price=shown(level), chosen=source == 'price_level'))
    for rule, _, _, amount in candidates_for(active_rules(db), facts, standard=standard, cost=cost, currency=currency):
        rows.append(Candidate(source='rule', id=rule['id'], name=rule['name'], priority=rule['priority'],
                              unit_price=shown(amount), chosen=rule['id'] == chosen_id))
    note = (profile.pricing_rule.summary if chosen_id else f'price from price level {profile.price_rule.label}'
            if level_priced else 'item sales price')
    return PricePreviewOutput(item=profile.item.label, customer=header.customer.label,
                              quantity=format_quantity_micro_units(resolved['quantity_microunits']),
                              date=date, conflict_policy=policy,
                              unit_price=Money(resolved['unit_price_minor_units'], currency).amount, currency=currency,
                              source=source, rule=Named(id=chosen_id, label=profile.pricing_rule.label) if chosen_id else None,
                              note=note, candidates=rows)


def preview_note(result) -> str | None:
    """For a sales form's preview: which lines took their price from a rule, in words."""
    if not isinstance(result, dict) or not isinstance(result.get('revision'), dict):
        return None
    notes = []
    for index, line in enumerate(result['revision'].get('lines') or [], 1):
        captured = (line.get('item_snapshot') or {}).get('pricing_rule') if isinstance(line, dict) else None
        if captured:
            notes.append(f"Line {line.get('position', index)}: {captured['summary']}")
    return '. '.join(notes) + '.' if notes else None


def in_use(db) -> bool:
    """Whether any price rule is active, so a line's quantity or customer can change its price."""
    return bool(db.conn.execute(_SIGNATURE).one()[3])
