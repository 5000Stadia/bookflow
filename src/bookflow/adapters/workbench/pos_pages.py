"""The POS screen (R114 increment 5, design/point-of-sale.md §3, §11, §12): ``/c/<company>/pos``.

The menu entry *Point of sale* under Customers, its Overview tile and a POS clerk's Home land here, and only while the
company has POS on (with it off the page answers the switch's own message, as `pos-register list` does).

The page carries what ringing up needs, read once (`pos-register list`, `item list`): the registers and their payment
methods, and every active sales item with its name, price, barcode, SKU, description, stock on hand and picture, so a
tap or a scan adds its line at once, with no host. Everything that decides money goes to the host through the
command API: `pos sale preview` for the exact total after each change, `pos customer search` and `add` for the
customer chip, `pos sale complete` to post. ``static/pos.js`` keeps the cart and mirrors it to ``localStorage``.

**The starting layout (§3).** The tap boxes come from the item categories, as *Start from item categories* would make
them: one group per top-level category, holding its sales items and its subcategories, then the items with no
category. Editing the layout (`pos-box`, Start empty) arrives with increment 6; until then this is the layout.
"""
from __future__ import annotations

from typing import Any

from fastapi import Request

from bookflow.core.errors import BookflowError

# What a box can ring up: an item a sales receipt sells. Discounts and subtotals are rung up by lookup (they are not
# boxes); payments, tax items and fixed assets are never sold at the till.
BOX_TYPES = ('service', 'inventory_part', 'non_inventory_part', 'other_charge', 'inventory_assembly', 'group')
LOOKUP_TYPES = BOX_TYPES + ('discount', 'subtotal')
_SELLERS = ('standard', 'admin', 'owner')


def _picture(item: dict[str, Any]) -> str | None:
    shown = item.get('thumbnail') or item.get('picture')
    return shown.get('url') if shown else None


def _items(listed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for item in listed:
        if not item.get('active', True) or not item.get('sales_enabled') or item.get('type') not in LOOKUP_TYPES:
            continue
        price = item.get('price') or None
        out.append(dict(id=item['id'], name=item['full_name'], type=item['type'], category=item.get('category'),
                        price=price['amount'] if price else None, barcode=item.get('barcode'),
                        sku=item.get('manufacturer_part_number'), description=item.get('description'),
                        on_hand=item.get('quantity_on_hand') if item['type'] in ('inventory_part',
                                                                                 'inventory_assembly') else None,
                        tracking=item.get('tracking') or 'none', picture=_picture(item),
                        # A percentage charge has no price of its own: it is rung up by lookup, like a discount.
                        box=item['type'] in BOX_TYPES and not (item['type'] == 'other_charge' and price is None)))
    return out


def starting_layout(items: list[dict[str, Any]]) -> dict[str, Any]:
    """The home and its groups from the item categories: {'home': [...], 'groups': {path: {name, boxes}}}.

    A box is ``{'kind': 'group', 'path': 'Drinks:Coffee', 'name': 'Coffee'}`` or ``{'kind': 'item', 'id': ...}``.
    """
    groups: dict[str, dict[str, Any]] = {}

    def group(path: str) -> dict[str, Any]:
        if path not in groups:
            groups[path] = {'name': path.split(':')[-1], 'boxes': []}
            parent = path.rsplit(':', 1)[0] if ':' in path else None
            (group(parent)['boxes'] if parent else home_groups).append(
                {'kind': 'group', 'path': path, 'name': groups[path]['name']})
        return groups[path]

    home_groups: list[dict[str, Any]] = []
    home_items: list[dict[str, Any]] = []
    for item in sorted((i for i in items if i['box']), key=lambda i: i['name'].casefold()):
        box = {'kind': 'item', 'id': item['id']}
        if item.get('category'):
            group(item['category'])['boxes'].append(box)
        else:
            home_items.append(box)
    for found in groups.values():
        found['boxes'].sort(key=lambda b: (b['kind'] != 'group', b.get('name', '').casefold()))
    home_groups.sort(key=lambda b: b['name'].casefold())
    return {'home': home_groups + home_items, 'groups': groups}


def mount(app, *, render, run, credential, page_error, role_allows, holds=None):
    @app.get('/c/{company_id}/pos')
    def pos_page(request: Request, company_id: str):
        try:
            company = run(request, 'company show', {}, company_id)
            registers = run(request, 'pos-register list', {}, company_id)['items']
            cid = company['company_id']
            items = _items(run(request, 'item list', {}, cid)['items'])
            held = holds(request, cid, 'pos.price-override') if holds is not None else None
            can_override = bool(held) if held is not None else company.get('role') in _SELLERS
            data = dict(company=cid, currency=company.get('home_currency') or 'USD',
                        registers=[dict(id=r['id'], name=r['name'], methods=r['payment_methods'])
                                   for r in registers],
                        items=items, layout=starting_layout(items), can_override=can_override)
            return render('pos.html', request, company=company, company_id=cid, registers=registers, pos_data=data)
        except BookflowError as e:
            return page_error(request, e)
