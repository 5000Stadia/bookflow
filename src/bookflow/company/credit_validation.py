"""An independent check of a prepared credit memo, run before any row is written.

Independent is the whole point: nothing here is allowed to ask the preparer what it meant. The
amounts are re-added from the rows themselves, the postings are re-balanced leg by leg, the
returned cells are recomputed from the source invoice's own capture through the endpoint rule,
and the claimed intervals are re-checked against the live residue. A preparer and a checker
that share a calculation agree with each other and are wrong together.

The stock a returned line brings back is checked the same way: the inventory pair is found off
the posting rows alone, the cost is recomputed from the source invoice line's own issue, and
every movement is tied back to the leg that carries its value -- which is what keeps the
inventory asset on the balance sheet and the total on the stock reports one figure.
"""
import sqlalchemy as sa

from bookflow.company import credit_returns as returns
from bookflow.company import schema as c
from bookflow.core.errors import BookflowError


def _require(condition, detail):
    if not condition:
        raise BookflowError('E_INTERNAL', message='Prepared credit memo is inconsistent: ' + detail)


def validate(plan, s, ctx, *, batch_kind='original', movements=True):
    data = plan.data
    if not data['changed']:
        return
    pending, header = data['pending'], data['header']
    revision = pending['transaction_revisions'][0]
    profile = pending['credit_profiles'][0]
    _require(header['type'] == 'credit_memo' and header['status'] == 'posted', 'wrong document type')
    _require(profile['revision_id'] == revision['id'] and revision['transaction_id'] == header['id'],
             'revision ownership')

    envelopes = {row['id']: row for row in pending['document_lines']}
    _require(all(row['kind'] == 'credit' for row in pending['document_lines']), 'line envelope kind')
    lines = {row['document_line_id']: row for row in pending['credit_line_profiles']}
    _require(set(lines) == set(envelopes), 'one commercial line per envelope')
    cells = {}
    for row in pending['credit_tax_components']:
        cells.setdefault(row['document_line_id'], []).append(row)

    subtotal = tax = gross = 0
    for identifier, line in lines.items():
        own = cells.get(identifier, [])
        line_tax = sum(cell['tax_minor_units'] for cell in own)
        _require(line['tax_minor_units'] == line_tax, 'line tax is the sum of its cells')
        _require(line['gross_minor_units'] == line['net_minor_units'] + line['tax_minor_units'],
                 'line gross is net plus tax')
        _require((line['posting_source_id'] is None) == (line['net_minor_units'] == 0),
                 'a line posts an income leg exactly when it is worth something')
        for cell in own:
            _require((cell['posting_source_id'] is None) == (cell['tax_minor_units'] == 0),
                     'a tax cell posts a leg exactly when it is worth something')
        subtotal += line['net_minor_units']
        tax += line_tax
        gross += line['gross_minor_units']
    _require(profile['subtotal_minor_units'] == subtotal and profile['tax_minor_units'] == tax,
             'header totals are the sum of the lines')
    _require(revision['total_minor_units'] == gross == subtotal + tax and gross > 0, 'document total')

    stock = _stock_pair(s, pending, profile)
    _balanced(pending, header, profile, revision, gross, batch_kind, stock)
    _posting_attribution(pending, lines, profile, stock)
    _capacity(pending, header, revision, lines, profile)
    _returns(s, pending, lines, profile)
    if movements:
        _movements(s, data)


def _stock_pair(s, pending, profile):
    """The inventory legs in this batch, found off the posting rows and nothing else.

    A credit memo posts exactly one credit -- the receivable -- and debits for everything else.
    A returned stock line adds one more pair: a debit to an inventory-asset control account and
    a credit of the same amount to the cost-of-goods account its sale captured. Both halves are
    identified by those two facts alone and checked against each other here, so the batch
    arithmetic below never has to be told which legs they are, and a stray credit leg on any
    other account is a failure rather than something quietly counted as stock.
    """
    from bookflow.company import inventory
    control = inventory.asset_account_ids(s)
    legs = pending['posting_lines']
    assets = [leg for leg in legs if leg['account_id'] in control]
    offsets = [leg for leg in legs if leg['credit_minor_units']
               and leg['account_id'] != profile['ar_account_id']]
    _require(all(leg['debit_minor_units'] and not leg['credit_minor_units'] for leg in assets),
             'an inventory-asset leg of a credit memo is not a debit')
    total = sum(leg['debit_minor_units'] for leg in assets)
    _require(len(assets) == len(offsets)
             and sum(leg['credit_minor_units'] for leg in offsets) == total,
             'the stock a credit brings back does not come out of cost of goods sold')
    return dict(assets=assets, offsets=offsets, total=total)


def _balanced(pending, header, profile, revision, gross, batch_kind, stock):
    batches = pending['posting_batches']
    _require(len(batches) == 1 and batches[0]['kind'] == batch_kind
             and batches[0]['effective_date'] == revision['date'], 'one business batch at the document date')
    legs = pending['posting_lines']
    debits = sum(leg['debit_minor_units'] for leg in legs)
    credits = sum(leg['credit_minor_units'] for leg in legs)
    # The inventory pair is equal and opposite, so it adds the same amount to each side and
    # the receivable still stands alone against the document gross.
    _require(debits == credits == gross + stock['total'], 'the batch balances at the document gross')
    receivable = [leg for leg in legs if leg['account_id'] == profile['ar_account_id']]
    _require(len(receivable) == 1 and receivable[0]['credit_minor_units'] == gross
             and receivable[0]['debit_minor_units'] == 0, 'exactly one receivable credit for the gross')
    _require(all(leg['name_type'] == 'customer' and leg['name_id'] == profile['customer_id'] for leg in legs),
             'every leg names the exact credited party')
    cost_legs = {id(leg) for leg in stock['offsets']}
    _require(all(leg['debit_minor_units'] > 0 for leg in legs
                 if leg is not receivable[0] and id(leg) not in cost_legs),
             'every other leg is a debit')
    by_line = {}
    for source in pending['posting_line_sources']:
        _require(source['tax_component_id'] is None, 'a credit attributes to its line, not to a sales tax cell')
        by_line.setdefault(source['posting_line_id'], []).append(source)
    for leg in legs:
        attributed = sum(source['amount_minor_units'] for source in by_line.get(leg['id'], []))
        _require(attributed == leg['debit_minor_units'] + leg['credit_minor_units'],
                 'every posted cent is attributed to an entered line')


def _capacity(pending, header, revision, lines, profile):
    keys = pending['credit_source_keys']
    _require(len(keys) == 1 and keys[0]['party_id'] == profile['customer_id']
             and keys[0]['ar_account_id'] == profile['ar_account_id']
             and keys[0]['currency'] == revision['currency'], 'one exact-party credit source')
    sources = {row['id']: row for row in pending['posting_line_sources']}
    total = 0
    for component in pending['credit_components']:
        _require(component['key_id'] == keys[0]['id'], 'a component belongs to this credit source')
        line = lines[component['document_line_id']]
        _require(component['amount_minor_units'] == line['gross_minor_units'],
                 'a component carries exactly its line gross')
        attribution = sources[component['posting_source_id']]
        _require(attribution['document_line_id'] == component['document_line_id']
                 and attribution['amount_minor_units'] == component['amount_minor_units'],
                 'a component names the receivable attribution that created it')
        total += component['amount_minor_units']
    _require(total == revision['total_minor_units'], 'capacity is the whole gross, once')


def _returns(s, pending, lines, profile):
    releases = {row['reverses_claim_id'] for row in pending['credit_source_claims'] if row['kind'] == 'release'}
    claims = [row for row in pending['credit_source_claims'] if row['kind'] == 'claim']
    linked = {identifier: line for identifier, line in lines.items() if line['source_transaction_id']}
    _require(bool(linked) == (profile['origin'] == 'return'), 'origin matches what the lines claim')
    _require(not linked or len(linked) == len(lines), 'a document is all returns or no returns')
    _require({claim['credit_document_line_id'] for claim in claims} <= set(linked),
             'every claim belongs to a returned line')
    for identifier, line in linked.items():
        own = [claim for claim in claims if claim['credit_document_line_id'] == identifier]
        _require(own, 'a returned line claims at least one interval')
        returns.merged([(row['start_microunits'], row['end_microunits']) for row in own])
        _require(all(row['source_transaction_id'] == line['source_transaction_id']
                     and row['source_revision_id'] == line['source_revision_id']
                     and row['source_document_line_id'] == line['source_document_line_id']
                     and row['source_line_id'] == line['source_line_id'] for row in own),
                 'claim names its credited source capture')
        source = _source(s, line)
        _require(source['profile']['customer_id'] == profile['customer_id']
                 and source['profile']['control_account_id'] == profile['ar_account_id'],
                 'a returned line belongs to an invoice of the exact credited party')
        quantity = int(source['line']['base_quantity_microunits'])
        net = int(source['line']['net_minor_units'])
        _require(all(claim['source_base_quantity_microunits'] == quantity
                     and claim['source_net_minor_units'] == net for claim in own), 'claims price the live capture')
        _require(sum(claim['end_microunits'] - claim['start_microunits'] for claim in own)
                 == line['base_quantity_microunits'], 'claimed quantity is the credited quantity')
        live = _live_claims(s, claim_source=line['source_transaction_id'], line_id=line['source_line_id'],
                            excluding=releases)
        live += [row for row in claims if row['credit_document_line_id'] != identifier
                 and row['source_transaction_id'] == line['source_transaction_id']
                 and row['source_line_id'] == line['source_line_id']]
        # Recomputed here from the intervals alone, never taken from the preparer.
        priced = returns.priced([(claim['start_microunits'], claim['end_microunits']) for claim in own],
                                quantity, net, source['taxes'])
        _require(priced['net_minor_units'] == line['net_minor_units'],
                 'a returned net is the source capture, not a recomputed price')
        expected = [cell['tax_minor_units'] for cell in priced['taxes']]
        actual = [row['tax_minor_units'] for row in sorted(
            (row for row in pending['credit_tax_components'] if row['document_line_id'] == identifier),
            key=lambda row: row['source_tax_component_id'] or '')]
        _require(expected == actual, 'every returned tax cent is the source capture')
        available = returns.available([(row['start_microunits'], row['end_microunits']) for row in live], quantity)
        for claim in own:
            _require(any(start <= claim['start_microunits'] and claim['end_microunits'] <= end
                         for start, end in available), 'a claimed interval was still there to claim')


def _live_claims(s, *, claim_source, line_id, excluding):
    table = c.credit_source_claims
    released = sa.select(table.c.reverses_claim_id).where(table.c.kind == 'release')
    rows = s.company.conn.execute(sa.select(table).where(
        table.c.source_transaction_id == claim_source, table.c.source_line_id == line_id,
        table.c.kind == 'claim', table.c.id.notin_(released))).mappings().all()
    return [dict(row) for row in rows if row['id'] not in excluding]


def _source(s, line):
    row = s.company.conn.execute(sa.select(c.sales_line_profiles).where(
        c.sales_line_profiles.c.transaction_id == line['source_transaction_id'],
        c.sales_line_profiles.c.revision_id == line['source_revision_id'],
        c.sales_line_profiles.c.document_line_id == line['source_document_line_id'])).mappings().all()
    _require(len(row) == 1, 'the claimed source line exists')
    taxes = [dict(cell) for cell in s.company.conn.execute(sa.select(c.sales_tax_components).where(
        c.sales_tax_components.c.document_line_id == line['source_document_line_id']).order_by(
        c.sales_tax_components.c.id)).mappings()]
    source_profile = s.company.conn.execute(sa.select(c.sales_profiles).where(
        c.sales_profiles.c.revision_id == line['source_revision_id'])).mappings().all()
    _require(len(source_profile) == 1, 'the claimed source revision exists')
    return dict(line=dict(row[0]), taxes=taxes, profile=dict(source_profile[0]))


def validate_void(plan, s, ctx):
    """An independent reading of what voiding a credit memo is about to store.

    The reversal is compared leg for leg with the batch it undoes, read again from storage
    rather than from the preparer's copy, and every claim release is compared cell for cell
    with the claim it gives back. What is checked last is what the refusal is for: nothing may
    still stand on the credit at the moment of the write, because a settled invoice or a paid
    refund would otherwise be left pointing at a document that is gone.
    """
    from bookflow.company import credits, document_effects as effects

    data = plan.data
    if not data['changed']:
        return
    pending, header, before = data['pending'], data['header'], data['before']
    _require(header['type'] == 'credit_memo' and header['status'] == 'voided', 'wrong document state')
    _require(header['version'] == before['version'] + 1, 'wrong credit memo version')
    mutable = {'version', 'updated_at', 'updated_by', 'updated_via', 'status', 'voided_at',
               'voided_by', 'void_reason', 'void_posting_batch_id'}
    _require({k: v for k, v in header.items() if k not in mutable}
             == {k: v for k, v in before.items() if k not in mutable}, 'a void changes only the state')
    for table in ('transaction_revisions', 'credit_profiles', 'credit_line_profiles',
                  'credit_tax_components', 'credit_source_keys', 'credit_components',
                  'document_lines', 'document_line_identities', 'sales_tax_line_keys'):
        _require(not pending[table], 'a void writes no new commercial history')
    batches = pending['posting_batches']
    _require(len(batches) == 1 and batches[0]['kind'] == 'reversal', 'one reversing batch')
    _require(header['void_posting_batch_id'] == batches[0]['id'], 'the void names its own reversal')
    original = effects.rows(s, c.posting_batches, c.posting_batches.c.id == batches[0]['reverses_batch_id'])
    _require(len(original) == 1 and original[0]['revision_id'] == data['revision']['id'],
             'the reversal undoes this revision\'s own effect')
    _require(batches[0]['effective_date'] == original[0]['effective_date'],
             'a void is dated where the credit is')
    old_legs = {row['id']: row for row in effects.rows(
        s, c.posting_lines, c.posting_lines.c.batch_id == original[0]['id'])}
    _require({row['reversed_line_id'] for row in pending['posting_lines']} == set(old_legs),
             'the reversal is not leg for leg')
    debit = credit = 0
    for leg in pending['posting_lines']:
        old = old_legs[leg['reversed_line_id']]
        _require(leg['debit_minor_units'] == old['credit_minor_units']
                 and leg['credit_minor_units'] == old['debit_minor_units'], 'a leg is not inverted')
        _require(leg['account_id'] == old['account_id'] and leg['name_id'] == old['name_id'],
                 'a reversal moves a leg to another account or party')
        debit += leg['debit_minor_units']
        credit += leg['credit_minor_units']
    # The stock pair the credit posted is reversed leg for leg with everything else, so the
    # reversal balances at the gross plus that pair -- the same figure the original did.
    from bookflow.company import inventory
    control = inventory.asset_account_ids(s)
    stock_total = sum(leg['debit_minor_units'] + leg['credit_minor_units']
                      for leg in pending['posting_lines'] if leg['account_id'] in control)
    _require(debit == credit == data['revision']['total_minor_units'] + stock_total,
             'the reversal does not balance at the credit\'s own total')
    _movements(s, data)
    fields = ('credit_transaction_id', 'credit_revision_id', 'credit_document_line_id',
              'source_transaction_id', 'source_revision_id', 'source_document_line_id',
              'source_line_id', 'start_microunits', 'end_microunits',
              'source_base_quantity_microunits', 'source_net_minor_units', 'effective_date')
    live = {row['id']: row for row in _claims_made_by(s, header['id'])}
    _require({row['reverses_claim_id'] for row in pending['credit_source_claims']} == set(live),
             'a void releases every interval this credit claimed, and only those')
    for row in pending['credit_source_claims']:
        _require(row['kind'] == 'release', 'a void writes releases')
        claim = live[row['reverses_claim_id']]
        _require(all(row[name] == claim[name] for name in fields), 'a release is not exact')
    _require(not credits.active_applications(s, header['id']),
             'a credit with a live application cannot be voided')
    _require(not credits.active_consumptions(s, key_id=data['source']['key']['id']),
             'a credit a refund has paid out cannot be voided')


def _claims_made_by(s, transaction_id):
    from bookflow.company import document_effects as effects
    claims = c.credit_source_claims
    released = sa.select(claims.c.reverses_claim_id).where(claims.c.kind == 'release')
    return effects.rows(s, claims, claims.c.credit_transaction_id == transaction_id,
                        claims.c.kind == 'claim', claims.c.id.notin_(released), order=claims.c.id)


def validate_update(plan, s, ctx):
    """Check both batches against their own facts, and the release against stored claims."""
    from bookflow.company import credits, document_effects as effects
    from bookflow.core.registry import Plan

    data = plan.data
    if not data['changed']:
        return
    pending, header, before = data['pending'], data['header'], data['before']
    previous = data['previous']
    _require(header['version'] == before['version'] + 1, 'correction version')
    revision = pending['transaction_revisions'][0]
    _require(revision['supersedes_revision_id'] == previous['revision']['id']
             and revision['revision_number'] == previous['revision']['revision_number'] + 1,
             'correction revision chain')
    _require(header['current_revision_id'] == revision['id'], 'current revision pointer')
    key = credits.current_key(s, pending['credit_components'], pending)
    _require(len(pending['credit_source_keys']) <= 1, 'one current dimension key')
    batches = pending['posting_batches']
    inverses = [row for row in batches if row['kind'] == 'reversal']
    replacements = [row for row in batches if row['kind'] == 'replacement']
    _require(len(batches) == 2 and len(inverses) == len(replacements) == 1,
             'one reversal and one replacement')
    inverse, replacement = inverses[0], replacements[0]
    original = effects.rows(s, c.posting_batches, c.posting_batches.c.id == inverse['reverses_batch_id'])[0]
    _require(original['revision_id'] == previous['revision']['id']
             and inverse['revision_id'] == original['revision_id']
             and inverse['effective_date'] == original['effective_date']
             and replacement['replaces_batch_id'] == original['id']
             and replacement['effective_date'] == revision['date'],
             'correction dates and batch linkage')
    old_legs = {row['id']: row for row in effects.rows(s, c.posting_lines, c.posting_lines.c.batch_id == original['id'])}
    legs = [row for row in pending['posting_lines'] if row['batch_id'] == inverse['id']]
    _require(len(legs) == len(old_legs) and {row['reversed_line_id'] for row in legs} == set(old_legs),
             'exact reversal leg coverage')
    metadata = {'id', 'created_at', 'created_by', 'created_via', 'batch_id',
                'debit_minor_units', 'credit_minor_units', 'reversed_line_id'}
    for leg in legs:
        old = old_legs[leg['reversed_line_id']]
        _require({k: v for k, v in leg.items() if k not in metadata}
                 == {k: v for k, v in old.items() if k not in metadata}
                 and leg['debit_minor_units'] == old['credit_minor_units']
                 and leg['credit_minor_units'] == old['debit_minor_units'], 'exact reversal leg')
        old_sources = {row['id']: row for row in effects.rows(
            s, c.posting_line_sources, c.posting_line_sources.c.posting_line_id == old['id'])}
        sources = [row for row in pending['posting_line_sources'] if row['posting_line_id'] == leg['id']]
        _require(len(sources) == len(old_sources)
                 and {row['reversed_source_id'] for row in sources} == set(old_sources), 'reversal attribution coverage')
        ignored = {'id', 'created_at', 'created_by', 'created_via', 'posting_line_id', 'reversed_source_id'}
        for row in sources:
            _require({k: v for k, v in row.items() if k not in ignored}
                     == {k: v for k, v in old_sources[row['reversed_source_id']].items() if k not in ignored},
                     'exact reversal attribution')
    live = {row['id']: row for row in _claims_made_by(s, header['id'])}
    releases = [row for row in pending['credit_source_claims'] if row['kind'] == 'release']
    _require(len(releases) == len(live) and {row['reverses_claim_id'] for row in releases} == set(live),
             'release every old claim exactly once')
    ignored = {'id', 'created_at', 'created_by', 'created_via', 'audit_event_id', 'kind', 'reverses_claim_id'}
    for row in releases:
        _require({k: v for k, v in row.items() if k not in ignored}
                 == {k: v for k, v in live[row['reverses_claim_id']].items() if k not in ignored},
                 'exact claim release')
    # Validate the replacement using the same independently reconstructed commercial arithmetic
    # as an original. Batch topology and all reversed facts have been checked above.
    business = dict(pending)
    business['posting_batches'] = [replacement]
    business['posting_lines'] = [row for row in pending['posting_lines'] if row['batch_id'] == replacement['id']]
    ids = {row['id'] for row in business['posting_lines']}
    business['posting_line_sources'] = [row for row in pending['posting_line_sources'] if row['posting_line_id'] in ids]
    business['credit_source_keys'] = [key]
    # The movement check is held back from that call and run below against the whole write,
    # because a correction's reversal movements hang off legs the replacement batch does not
    # hold and a narrowed row set cannot see them.
    validate(Plan(plan.preview, dict(data, pending=business)), s, ctx, batch_kind='replacement',
             movements=False)
    _movements(s, data)
    from bookflow.company.credit_restatement import compatible, validate as validate_uses
    compatible(s, ctx, previous, data['resolved'])
    validate_uses(s, data)


def _posting_attribution(pending, lines, profile, stock):
    """Each business leg must post the captured account and amount of its own component."""
    import json
    sources = {row['id']: row for row in pending['posting_line_sources']}
    legs = {row['id']: row for row in pending['posting_lines']}
    expected = {}

    def add(identifier, account, amount, document_line, debit):
        if not amount:
            _require(identifier is None, 'zero component has no posting')
            return
        _require(identifier in sources, 'commercial component has a posting attribution')
        row = sources[identifier]
        _require(row['amount_minor_units'] == amount and row['document_line_id'] == document_line,
                 'commercial component amount and attribution')
        leg = legs[row['posting_line_id']]
        _require(leg['account_id'] == account and leg['name_id'] == profile['customer_id'],
                 'commercial component captured account and party')
        _require((leg['debit_minor_units'] > 0) == debit, 'commercial component posting direction')
        expected[identifier] = amount

    for identifier, line in lines.items():
        account = json.loads(line['item_snapshot'])['income_account']['id']
        add(line['posting_source_id'], account, line['net_minor_units'], identifier, True)
    for cell in pending['credit_tax_components']:
        add(cell['posting_source_id'], cell['liability_account_id'], cell['tax_minor_units'],
            cell['document_line_id'], True)
    for component in pending['credit_components']:
        add(component['posting_source_id'], profile['ar_account_id'], component['amount_minor_units'],
            component['document_line_id'], False)
    # The inventory pair is owned by the entered line it was returned on, the same way every
    # other leg is; without this the set below would not close and the pair would be posting
    # cents nothing accounts for.
    for leg in stock['assets'] + stock['offsets']:
        own = [row for row in sources.values() if row['posting_line_id'] == leg['id']]
        _require(len(own) == 1 and own[0]['document_line_id'] in lines
                 and own[0]['amount_minor_units']
                 == leg['debit_minor_units'] + leg['credit_minor_units'],
                 'a stock leg is attributed to exactly one credited line')
        expected[own[0]['id']] = own[0]['amount_minor_units']
    _require(set(expected) == set(sources), 'every business attribution has exactly one commercial owner')


def _movements(s, data):
    """What this credit does to the stock ledger, read back off the rows it will write.

    The tie is the point, exactly as it is on the sale: every posting to an inventory control
    account is claimed by one movement and carries that movement's value, which is what makes
    the inventory asset on the balance sheet and the total on the stock reports one number.
    Beyond that the cost each returned line brings back is recomputed here from the source
    invoice line's own live issue, through the same endpoint rule the net and the tax cells go
    through -- never taken from the preparer.
    """
    from bookflow.company import document_effects as effects, inventory, inventory_effects
    from bookflow.company.inventory_costing import replay
    from bookflow.company.sales_facts import SalesLineProfile

    change = data.get('stock')
    if change is None:
        return
    header, pending = data['header'], data['pending']
    movements = [movement.values for movement in change.movements]
    legs = {row['id']: row for row in pending['posting_lines']}
    batches = {row['id']: row for row in pending['posting_batches']}
    control = inventory.asset_account_ids(s)
    claimed = [row['posting_line_id'] for row in movements if row['value_minor_units']]
    _require(len(claimed) == len(set(claimed)), 'two stock movements claim one posting line')
    _require(set(claimed) == {leg['id'] for leg in legs.values() if leg['account_id'] in control},
             'an inventory-asset posting is not attributed to exactly one item')

    lines = {row['document_line_id']: row for row in pending['credit_line_profiles']}
    claims = {}
    for row in pending['credit_source_claims']:
        if row['kind'] == 'claim':
            claims.setdefault(row['credit_document_line_id'], []).append(row)
    prior = {row['id']: row for row in effects.rows(
        s, c.inventory_movements, c.inventory_movements.c.transaction_id == header['id'])}
    for row in movements:
        leg = legs.get(row['posting_line_id'])
        batch = batches.get(row['posting_batch_id'])
        _require(batch is not None and batch['transaction_id'] == row['transaction_id']
                 and batch['effective_date'] == row['effective_date'], 'stock batch ownership/date')
        if row['value_minor_units']:
            _require(leg is not None and leg['batch_id'] == row['posting_batch_id']
                     and leg['account_id'] == row['asset_account_id']
                     and leg['debit_minor_units'] - leg['credit_minor_units'] == row['value_minor_units'],
                     'stock monetary attribution')
        else:
            _require(row['posting_line_id'] is None and row['quantity_microunits'] != 0,
                     'zero stock effect must have quantity and no monetary leg')
        _require(row['kind'] in ('receipt', 'reversal'), 'a credit memo moves stock only in or back out')
        if row['kind'] == 'reversal':
            original = prior.get(row['reverses_movement_id'])
            _require(original is not None
                     and (leg['reversed_line_id'] if leg else None) == original['posting_line_id']
                     and row['quantity_microunits'] == -original['quantity_microunits']
                     and row['value_minor_units'] == -original['value_minor_units']
                     and row['item_id'] == original['item_id']
                     and row['document_line_id'] == original['document_line_id']
                     and row['revision_id'] == original['revision_id'],
                     'a stock reversal is not the exact inverse of the movement it retires')
            continue
        line = lines.get(row['document_line_id'])
        _require(line is not None and line['item_id'] == row['item_id']
                 and row['quantity_microunits'] == line['base_quantity_microunits']
                 and row['revision_id'] == line['revision_id'] and row['value_minor_units'] >= 0,
                 'a stock receipt does not match the entered line that returned it')
        facts = SalesLineProfile.model_validate_json(line['item_snapshot'])
        _require(facts.item_type in inventory.TRACKED_TYPES
                 and facts.asset_account is not None and facts.cogs_account is not None
                 and facts.asset_account.id == row['asset_account_id']
                 and facts.cogs_account.id == row['offset_account_id'],
                 'a stock receipt was written for a line that carries no stock')
        own = claims.get(row['document_line_id'], [])
        issue = inventory_effects.live_issue(
            s, line['source_transaction_id'], line['source_document_line_id'])
        _require(bool(own) and issue is not None
                 and -int(issue['quantity_microunits']) == own[0]['source_base_quantity_microunits'],
                 'the returned invoice line issued no stock, or not the quantity it captured')
        _require(row['returns_movement_id'] == issue['id'],
                 'a returned line does not name the issue it gives back')
        _require(row['effective_date'] >= issue['effective_date'],
                 'a return is dated before the sale it returns')
    for envelope_id, line in lines.items():
        facts = SalesLineProfile.model_validate_json(line['item_snapshot'])
        if facts.item_type in inventory.TRACKED_TYPES:
            _require(any(row['document_line_id'] == envelope_id and row['kind'] == 'receipt'
                         for row in movements),
                     'an entered line returned stock that no movement received')

    # What each return is worth is not taken from the preparer at all: the item's whole stored
    # history is replayed together with what this write adds, and every movement this document
    # writes must come out of that replay owing no correction. A return posted at anything but
    # its share of the issue it names owes one, so this is what makes the cost independent.
    own_ids = {row['id'] for row in movements}
    for item_id in {row['item_id'] for row in movements}:
        stored = inventory.movements(s, item_id=item_id)
        state = replay(stored + [row for row in movements if row['item_id'] == item_id])
        _require(not any(correction.target_movement['id'] in own_ids
                         for correction in state.corrections),
                 'a stock movement is posted at a cost the ledger does not agree with')
