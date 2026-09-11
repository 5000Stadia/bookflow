"""An independent check of a prepared credit memo, run before any row is written.

Independent is the whole point: nothing here is allowed to ask the preparer what it meant. The
amounts are re-added from the rows themselves, the postings are re-balanced leg by leg, the
returned cells are recomputed from the source invoice's own capture through the endpoint rule,
and the claimed intervals are re-checked against the live residue. A preparer and a checker
that share a calculation agree with each other and are wrong together.
"""
import sqlalchemy as sa

from bookflow.company import credit_returns as returns
from bookflow.company import schema as c
from bookflow.core.errors import BookflowError


def _require(condition, detail):
    if not condition:
        raise BookflowError('E_INTERNAL', message='Prepared credit memo is inconsistent: ' + detail)


def validate(plan, s, ctx):
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

    _balanced(pending, header, profile, revision, gross)
    _capacity(pending, header, revision, lines, profile)
    _returns(s, pending, lines, profile)


def _balanced(pending, header, profile, revision, gross):
    batches = pending['posting_batches']
    _require(len(batches) == 1 and batches[0]['kind'] == 'original'
             and batches[0]['effective_date'] == revision['date'], 'one original batch at the document date')
    legs = pending['posting_lines']
    debits = sum(leg['debit_minor_units'] for leg in legs)
    credits = sum(leg['credit_minor_units'] for leg in legs)
    _require(debits == credits == gross, 'the batch balances at the document gross')
    receivable = [leg for leg in legs if leg['account_id'] == profile['ar_account_id']]
    _require(len(receivable) == 1 and receivable[0]['credit_minor_units'] == gross
             and receivable[0]['debit_minor_units'] == 0, 'exactly one receivable credit for the gross')
    _require(all(leg['name_type'] == 'customer' and leg['name_id'] == profile['customer_id'] for leg in legs),
             'every leg names the exact credited party')
    _require(all(leg['debit_minor_units'] > 0 for leg in legs if leg is not receivable[0]),
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
    claims = pending['credit_source_claims']
    linked = {identifier: line for identifier, line in lines.items() if line['source_transaction_id']}
    _require(bool(linked) == (profile['origin'] == 'return'), 'origin matches what the lines claim')
    _require(not linked or len(linked) == len(lines), 'a document is all returns or no returns')
    _require({claim['credit_document_line_id'] for claim in claims} <= set(linked),
             'every claim belongs to a returned line')
    for identifier, line in linked.items():
        own = [claim for claim in claims if claim['credit_document_line_id'] == identifier]
        _require(own, 'a returned line claims at least one interval')
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
                            excluding={claim['id'] for claim in claims})
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
    _require(debit == credit == data['revision']['total_minor_units'],
             'the reversal does not balance at the credit\'s own total')
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
