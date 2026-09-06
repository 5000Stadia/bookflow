"""Read-only work defaults and authoritative catalog/manual/markup/amount pricing."""
from __future__ import annotations

from dataclasses import dataclass

from bookflow.company import sales_defaults
from bookflow.company.sales_calculations import adjusted_price, extension, selected_price
from bookflow.company.sales_facts import Origin
from bookflow.company.sales_models import SalesLineInput, _invalid, money
from bookflow.company.work_facts import WorkLineFacts, WorkProfile
from bookflow.core.exact import parse_percentage_millionths, parse_quantity_micro_units


WORK_KINDS = ('proposal', 'estimate', 'work_order')


def resolve_header(s, inp, kind, previous=None, old_date=None):
    """Capture commercial defaults without financial controls or term due dates."""
    if kind not in WORK_KINDS:
        raise _invalid('kind', 'expected proposal, estimate or work_order')
    profile, warnings = sales_defaults.resolve_header(s, inp, kind, previous=previous, old_date=old_date)
    return WorkProfile.model_validate(profile.model_dump()), warnings


def _cost(s, inp, profile, factor, currency, previous, refresh, warnings):
    if 'estimated_unit_cost' in inp.model_fields_set:
        value = (money(inp.estimated_unit_cost, currency, 'estimated_unit_cost').minor_units
                 if inp.estimated_unit_cost is not None else None)
        return value, Origin(kind='explicit')
    dependent = previous is None or previous.item_id != profile.item.id or previous.profile.unit != profile.unit
    reset = 'estimated_unit_cost' in inp.use_defaults
    if previous is not None and not reset:
        if previous.estimated_cost_origin.kind == 'explicit':
            if dependent:
                warnings.append('estimated_unit_cost: explicit cost retained per selected unit')
            return previous.estimated_unit_cost_minor_units, previous.estimated_cost_origin
        if not dependent and not refresh:
            return previous.estimated_unit_cost_minor_units, previous.estimated_cost_origin
    if previous is not None:
        warnings.append('estimated_unit_cost: defaults resolved again')
    cost = profile.cost_minor_units
    if reset and not refresh:
        item = sales_defaults._row(s.company, 'item', profile.item.id)
        cost = item['cost_minor_units']
        if cost is not None and item['cost_currency'] != currency:
            raise _invalid('estimated_unit_cost', 'catalog cost must use home currency')
    return (selected_price(cost, factor) if cost is not None else None), Origin(kind='default', source_id=profile.item.id)


@dataclass
class _OverridePrice:
    session: object
    inp: object
    currency: str
    previous: WorkLineFacts | None
    refresh: bool
    warnings: list[str]
    mode: str
    markup: int | None
    cost: int | None = None
    origin: Origin | None = None

    def __call__(self, profile, factor):
        self.cost, self.origin = _cost(self.session, self.inp, profile, factor, self.currency, self.previous, self.refresh, self.warnings)
        profile.origins['unit_price'] = Origin(kind='explicit')
        if self.mode == 'amount':
            return None
        if self.cost is None:
            raise _invalid('estimated_unit_cost', 'markup pricing requires a known cost')
        return adjusted_price(self.cost, self.markup)


def resolve_line(s, inp, header, previous: WorkLineFacts | None = None,
                 previous_header=None, refresh=False, kind='estimate', document_tax=False):
    """Resolve one line; the service owns identities, ordering and aggregate totals."""
    if kind not in WORK_KINDS:
        raise _invalid('kind', 'expected proposal, estimate or work_order')
    if kind != 'work_order' and 'completed_quantity' in inp.model_fields_set:
        raise _invalid('completed_quantity', 'only work orders support completed quantity')
    currency = sales_defaults._info(s.company)['home_currency']
    refresh = refresh or inp.refresh_defaults
    warnings = []
    mode = previous.pricing_basis if previous else 'catalog'
    for field, selected in (('unit_price', 'manual'), ('markup_percent', 'markup'), ('net_amount', 'amount')):
        if field in inp.model_fields_set:
            mode = selected
    if 'unit_price' in inp.use_defaults or ('price_level' in inp.model_fields_set and 'unit_price' not in inp.model_fields_set):
        mode = 'catalog'
    markup = (parse_percentage_millionths(inp.markup_percent, field='markup_percent')
              if 'markup_percent' in inp.model_fields_set else previous.markup_percent_millionths if previous else None)
    if mode != 'markup':
        markup = None
    net = None
    if mode == 'amount':
        net = (money(inp.net_amount, currency, 'net_amount').minor_units
               if 'net_amount' in inp.model_fields_set else previous.net_minor_units)
    payload = inp.model_dump(exclude_unset=True, include=set(SalesLineInput.model_fields))
    payload['use_defaults'] = [field for field in inp.use_defaults if field != 'estimated_unit_cost']
    if mode == 'catalog' and previous and previous.pricing_basis != 'catalog' and 'unit_price' not in payload['use_defaults']:
        payload['use_defaults'].append('unit_price')
    sales_input = SalesLineInput.model_validate(payload)
    saved = None
    if previous:
        saved = dict(quantity_microunits=previous.quantity_microunits, description=previous.description,
                     unit_price_minor_units=previous.unit_price_minor_units, item_snapshot=previous.profile.model_dump())
    override = _OverridePrice(s, inp, currency, previous, refresh, warnings, mode, markup) if mode in ('markup', 'amount') else None
    resolved, shared_warnings = sales_defaults.resolve_line(
        s, sales_input, header, previous=saved, previous_header=previous_header, refresh=refresh,
        nonposting=True, price_override=override, net_override=net, defer_tax=document_tax,
    )
    warnings.extend(shared_warnings)
    profile = resolved['profile']
    if override is None:
        cost, origin = _cost(s, inp, profile, resolved['unit_factor_nanounits'], currency, previous, refresh, warnings)
    else:
        cost, origin = override.cost, override.origin
    for field in ('markup_percent', 'net_amount'):
        profile.origins.pop(field, None)
    if mode in ('markup', 'amount'):
        profile.origins['markup_percent' if mode == 'markup' else 'net_amount'] = Origin(kind='explicit')
    if previous and previous.item_id != profile.item.id and mode == 'manual' and 'unit_price' not in inp.model_fields_set:
        warnings.append('unit_price: explicit price retained per selected unit')
    completed = (parse_quantity_micro_units(inp.completed_quantity, field='completed_quantity')
                 if 'completed_quantity' in inp.model_fields_set else previous.completed_quantity_microunits if previous else 0)
    if kind != 'work_order' and completed:
        raise _invalid('completed_quantity', 'only work orders support completed quantity')
    if completed > resolved['quantity_microunits']:
        raise _invalid('completed_quantity', 'cannot exceed ordered quantity')
    from bookflow.company.work_tax_facts import WorkLineFacts2
    model = WorkLineFacts2 if document_tax else WorkLineFacts
    return model(**resolved, completed_quantity_microunits=completed,
        estimated_unit_cost_minor_units=cost,
        estimated_cost_minor_units=extension(resolved['quantity_microunits'], cost) if cost is not None else None,
        estimated_cost_origin=origin, pricing_basis=mode, markup_percent_millionths=markup,
        billable=inp.billable if 'billable' in inp.model_fields_set else previous.billable if previous else True), warnings
