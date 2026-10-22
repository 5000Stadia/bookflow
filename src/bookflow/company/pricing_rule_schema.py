"""Price rules and the pricing-rule preference (co0086, R110).

A price rule changes the price a sales line defaults to when its conditions all hold: which items
(by item, or by item category), which customers (by customer:job, or by customer type), which class,
which sales rep, a date window on the transaction date and a minimum quantity. Its adjustment moves
the item's sales price or cost up or down by a percent or an amount, optionally by quantity break,
and rounds the result. ``priority`` orders rules: 1 is first.

``price_rule_preferences`` holds the company's one choice of how a line picks among the price level
and the rules that match it: the lowest price, or the rule first in priority order. A company with no
row uses the lowest price.

A line that took its price from a rule captures the rule (its id, name, version and adjustment) in
its line facts, so editing or retiring a rule never changes a saved document.
"""
import sqlalchemy as sa

ADJUSTMENT_KINDS = ('percent', 'amount')
DIRECTIONS = ('down', 'up')
BASES = ('sales_price', 'cost')
ROUNDINGS = ('cent', 'x.99', 'x.95', 'dollar')
CONFLICT_POLICIES = ('lowest_price', 'priority')


def _in(column, values):
    return f"{column} IN ({','.join(repr(v) for v in values)})"


def define_tables(metadata, C, T, common):
    rules = T('price_rules',
        *common(),
        C('name', sa.String(200), 'Rule name as entered, such as Acme pipe fittings 10% off.', nullable=False),
        C('name_key', sa.String(400), 'NFC-normalized, trimmed and case-folded name; unique within the company.', nullable=False),
        C('description', sa.String(500), 'What the rule is for, as the person wrote it.', nullable=True),
        C('active', sa.Boolean, 'Whether the rule prices new lines; an inactive rule is kept for history.', nullable=False, default=True),
        C('priority', sa.Integer, 'Order among rules, 1 first; ties go to the name, then the id.', nullable=False),
        C('date_from', sa.String(10), 'First transaction date, YYYY-MM-DD, the rule applies to; null for no start.', nullable=True),
        C('date_to', sa.String(10), 'Last transaction date, YYYY-MM-DD, the rule applies to; null for no end.', nullable=True),
        C('min_quantity_microunits', sa.BigInteger, 'Smallest line quantity in base units, in millionths, the rule applies to; null for any.', nullable=True),
        C('conditions', sa.Text, 'Canonical JSON of the id lists the line must match: items, item_categories, customers, customer_types, classes, sales_reps; an empty list matches all.', nullable=False),
        C('adjustment_kind', sa.String(8), 'percent or amount.', nullable=False),
        C('direction', sa.String(8), 'down (a discount) or up (a markup).', nullable=False),
        C('basis', sa.String(12), 'sales_price (the item price) or cost (the item cost) the adjustment starts from.', nullable=False),
        C('value', sa.BigInteger, 'Percent in millionths of a point, or amount in minor units; null when quantity breaks give the value.', nullable=True),
        C('breaks', sa.Text, 'Canonical JSON quantity breaks [{min_quantity_microunits, value}] ascending; null for one value.', nullable=True),
        C('rounding', sa.String(8), 'cent (to the cent), x.99, x.95 (up to the next price ending so) or dollar (nearest whole unit).', nullable=False),
        sa.UniqueConstraint('name_key', name='uq_price_rule_name_key'),
        sa.CheckConstraint('length(trim(name)) > 0', name='ck_price_rule_name_nonblank'),
        sa.CheckConstraint('active IN (0,1)', name='ck_price_rule_active'),
        sa.CheckConstraint('priority >= 1', name='ck_price_rule_priority'),
        sa.CheckConstraint('date_from IS NULL OR date_to IS NULL OR date_from <= date_to', name='ck_price_rule_dates'),
        sa.CheckConstraint('min_quantity_microunits IS NULL OR min_quantity_microunits > 0', name='ck_price_rule_min_quantity'),
        sa.CheckConstraint(_in('adjustment_kind', ADJUSTMENT_KINDS), name='ck_price_rule_kind'),
        sa.CheckConstraint(_in('direction', DIRECTIONS), name='ck_price_rule_direction'),
        sa.CheckConstraint(_in('basis', BASES), name='ck_price_rule_basis'),
        sa.CheckConstraint(_in('rounding', ROUNDINGS), name='ck_price_rule_rounding'),
        sa.CheckConstraint('(value IS NULL) != (breaks IS NULL)', name='ck_price_rule_value'),
        sa.CheckConstraint('value IS NULL OR value >= 0', name='ck_price_rule_value_sign'),
        description='Price rules: conditions, an adjustment and rounding that price matching sales lines (Advanced Pricing).')

    preferences = T('price_rule_preferences',
        *common(),
        C('singleton', sa.Integer, 'Always 1: the company has at most one row.', nullable=False, default=1),
        C('conflict_policy', sa.String(16), 'lowest_price: the lowest of the price level and matching rules wins; priority: the first matching rule wins.', nullable=False),
        sa.UniqueConstraint('singleton', name='uq_price_rule_preferences_singleton'),
        sa.CheckConstraint('singleton = 1', name='ck_price_rule_preferences_singleton'),
        sa.CheckConstraint(_in('conflict_policy', CONFLICT_POLICIES), name='ck_price_rule_preferences_policy'),
        description='How a sales line chooses among its price level and matching price rules.')
    return {'price_rules': rules, 'price_rule_preferences': preferences}
