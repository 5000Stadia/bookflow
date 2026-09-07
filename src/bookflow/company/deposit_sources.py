"""Whole-cash adapters shared by stored and validated prospective source graphs.

No source application balance is used as cash capacity. Prospective physical IDs
remain private until the owning aggregate is persisted.
"""
import json
import sqlalchemy as sa
from bookflow.company import schema as c, document_effects as effects
from bookflow.company.deposit_models import CashSource, CashComponent, SemanticKey, Dimensions
from bookflow.company.payment_outputs import PaymentProfileOutput
from bookflow.company.sales_facts import SalesProfile, SalesLineProfile, SalesTaxComponent
from bookflow.core.errors import BookflowError

def require(condition):
    if not condition:
        raise BookflowError('E_DEPOSIT_SOURCE_INVALID')


TABLES = ('transaction_revisions', 'posting_batches', 'posting_lines', 'posting_line_sources',
          'document_lines', 'payment_profiles', 'payment_components', 'payment_component_keys',
          'sales_profiles', 'sales_line_profiles', 'sales_tax_components')


def graph(s, identity, pending=None, header=None):
    """Read the complete owned graph; overlay only actual validated pending rows."""
    from bookflow.company.payment_authority import authorize
    found = effects.rows(s, c.transactions, c.transactions.c.id == identity)
    if len(found) != 1 or found[0]['type'] not in ('payment', 'sales_receipt'):
        raise BookflowError('E_RECORD_NOT_FOUND')
    authorize(s, [identity])
    result = {'header': header or found[0]}
    for name in TABLES:
        table = getattr(c, name)
        old = effects.rows(s, table, table.c.transaction_id == identity)
        added = [r for r in (pending or {}).get(name, ()) if r['transaction_id'] == identity]
        require(not ({r.get('id', r.get('revision_id', r.get('document_line_id'))) for r in old} &
                     {r.get('id', r.get('revision_id', r.get('document_line_id'))) for r in added}))
        result[name] = old + added
    return result


def _project(g, *, uf_account, home_currency):
    """Validate ownership and exact positive UF partition, independent of SQL."""
    h = g['header']; identity = h['id']; revision = h['current_revision_id']
    if h['type'] not in ('payment', 'sales_receipt') or h['status'] != 'posted':
        raise BookflowError('E_DEPOSIT_SOURCE_INELIGIBLE')
    for name in TABLES:
        require(all(row['transaction_id'] == identity for row in g[name]))
    def current(name):
        return [r for r in g[name] if r.get('revision_id', r.get('id')) == revision]
    revs = [r for r in g['transaction_revisions'] if r['id'] == revision]
    require(len(revs) == 1)
    rev = revs[0]
    require(rev['currency'] == home_currency and rev['total_minor_units'] > 0)
    inverses = {r['reverses_batch_id'] for r in g['posting_batches'] if r['kind'] == 'reversal'}
    batches = [r for r in g['posting_batches'] if r['kind'] in ('original', 'replacement') and r['id'] not in inverses]
    require(len(batches) == 1 and batches[0]['revision_id'] == revision and batches[0]['effective_date'] == rev['date'])
    batch = batches[0]
    legs = [r for r in g['posting_lines'] if r['batch_id'] == batch['id']]
    require(len({r['id'] for r in legs}) == len(legs))
    cash_legs = {r['id']: r for r in legs if r['account_id'] == uf_account}
    if not cash_legs:
        raise BookflowError('E_DEPOSIT_SOURCE_INELIGIBLE')
    require(all(r['debit_minor_units'] > 0 and r['credit_minor_units'] == 0 and r['currency'] == home_currency for r in cash_legs.values()))
    require(sum(r['debit_minor_units'] for r in cash_legs.values()) == rev['total_minor_units'])
    require(sum(r['debit_minor_units'] - r['credit_minor_units'] for r in legs) == 0)
    srcs = [r for r in g['posting_line_sources'] if r['posting_line_id'] in cash_legs]
    require(len({r['id'] for r in srcs}) == len(srcs))
    for leg_id, leg in cash_legs.items():
        require(sum(r['amount_minor_units'] for r in srcs if r['posting_line_id'] == leg_id) == leg['debit_minor_units'])
    envelopes = {r['id']: r for r in current('document_lines')}
    require(len(envelopes) == len(current('document_lines')))
    payment = h['type'] == 'payment'
    profiles = current('payment_profiles' if payment else 'sales_profiles')
    require(len(profiles) == 1)
    profile = (PaymentProfileOutput if payment else SalesProfile).model_validate_json(profiles[0]['profile_snapshot'])
    if (profile.deposit_account.id if payment else profile.control_account.id) != uf_account:
        raise BookflowError('E_DEPOSIT_SOURCE_INELIGIBLE')
    presence = []
    if payment:
        keys = {r['id']: r for r in g['payment_component_keys']}
        require(len(keys) == len(g['payment_component_keys']))
        presence = [SemanticKey(kind='payment', identity=k) for k in keys]
        physical = {r['id']: r for r in current('payment_components')}
        require(len(physical) == len(current('payment_components')))
        require(sum(r['amount_minor_units'] for r in physical.values()) == rev['total_minor_units'])
    else:
        lines = {r['document_line_id']: r for r in current('sales_line_profiles')}
        require(set(lines) == set(envelopes))
        physical = {r['id']: r for r in current('sales_tax_components')}
        for line_id, row in lines.items():
            presence.append(SemanticKey(kind='sale_net', identity=envelopes[line_id]['line_id']))
        for row in physical.values():
            require(row['document_line_id'] in envelopes)
            presence.append(SemanticKey(kind='sale_tax', identity=envelopes[row['document_line_id']]['line_id'], tax_item=row['tax_item_id']))
    components = []
    for src in srcs:
        require(src['revision_id'] == revision and src['document_line_id'] in envelopes and src['currency'] == home_currency and src['amount_minor_units'] > 0 and src['reversed_source_id'] is None)
        envelope = envelopes[src['document_line_id']]
        leg = cash_legs[src['posting_line_id']]
        dims = Dimensions(party_kind=leg['name_type'], party_id=leg['name_id'], party_name=leg['party_name'], class_id=leg['class_id'], class_name=leg['class_name'])
        extra = {}
        if payment:
            require(src.get('tax_component_id') is None and src.get('payment_component_id') in physical)
            component = physical[src['payment_component_id']]
            require(component['document_line_id'] == envelope['id'] and component['component_key_id'] in keys and component['amount_minor_units'] == src['amount_minor_units'] and component['currency'] == home_currency)
            key = keys[component['component_key_id']]
            require(key['line_id'] == envelope['line_id'] and key['currency'] == home_currency)
            require(dims.party_kind == 'customer' and dims.party_id == profile.payer.id and dims.class_id is None)
            semantic = SemanticKey(kind='payment', identity=key['id'])
            captured=json.loads(component['component_snapshot'])
            require(captured['party']['id']==key['party_id'] and captured['ar_account']['id']==key['ar_account_id'])
            extra = dict(credit_owner_party=key['party_id'], credit_owner_ar=key['ar_account_id'])
            physical_id = component['id']
        else:
            require(src.get('payment_component_id') is None)
            line = lines[envelope['id']]
            facts = SalesLineProfile.model_validate_json(line['item_snapshot'])
            require(dims.party_kind == 'customer' and dims.party_id == profile.customer.id and dims.class_id == (facts.class_id.id if facts.class_id else None))
            physical_id = src['tax_component_id']
            extra = dict(sale_line=facts)
            if physical_id is None:
                require(src['amount_minor_units'] == line['net_minor_units'])
                semantic = SemanticKey(kind='sale_net', identity=envelope['line_id'])
            else:
                require(physical_id in physical)
                tax = physical[physical_id]
                require(tax['document_line_id'] == envelope['id'] and tax['tax_minor_units'] == src['amount_minor_units'])
                semantic = SemanticKey(kind='sale_tax', identity=envelope['line_id'], tax_item=tax['tax_item_id'])
                extra['tax'] = SalesTaxComponent.model_validate_json(tax['component_snapshot'])
                require(extra['tax'].tax_item.id==tax['tax_item_id'] and extra['tax'].agency.id==tax['agency_id'] and extra['tax'].liability_account.id==tax['liability_account_id'])
        components.append(CashComponent(key=semantic, capacity=src['amount_minor_units'], document_line_id=envelope['id'],
            posting_line_id=leg['id'], posting_source_id=src['id'], physical_component_id=physical_id, cash=dims, **extra))
    require(len({x.key for x in components}) == len(components) and len(set(presence)) == len(presence))
    require(sum(x.capacity for x in components) == rev['total_minor_units'])
    return CashSource(source_type=h['type'], transaction_id=identity, expected_header_version=h['version'], revision_id=revision,
        business_batch_id=batch['id'], receipt_date=rev['date'], currency=home_currency, cash_minor_units=rev['total_minor_units'],
        uf_account=uf_account, source_memo=rev['memo'], source_reference=profiles[0]['reference'] if payment else profile.payment_reference, profile=profile, semantic_presence=tuple(sorted(presence,key=lambda k:k.order())),
        components=tuple(sorted(components,key=lambda c:c.key.order())), dependencies=(identity,))


def project(g, *, uf_account, home_currency):
    try:
        return _project(g, uf_account=uf_account, home_currency=home_currency)
    except (ValueError, KeyError, TypeError) as exc:
        raise BookflowError('E_DEPOSIT_SOURCE_INVALID') from exc


def load(s, identity):
    uf = s.company.conn.execute(sa.select(c.accounts).where(c.accounts.c.system_role == 'undeposited_funds')).mappings().all()
    if len(uf)!=1 or not uf[0]['active'] or uf[0]['currency']!=s.company_info_row['home_currency']:
        raise BookflowError('E_DEPOSIT_SOURCE_INELIGIBLE')
    return project(graph(s, identity), uf_account=uf[0]['id'], home_currency=s.company_info_row['home_currency'])
