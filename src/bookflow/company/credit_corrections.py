"""Credit corrections over immutable captures and one writer transaction."""
from bookflow.company import credits, document_effects as effects, journals, schema as c
from bookflow.company.credit_models import CreditMemoWriteOutput
from bookflow.company.sales_facts import SalesLineProfile
from bookflow.company.sales_models import _invalid
from bookflow.core.ids import new_id
from bookflow.company.credit_restatement import dependencies
from bookflow.core.registry import Plan


def saved_lines(s, revision):
    result = {}
    for envelope in effects.rows(s, c.document_lines,
                                c.document_lines.c.revision_id == revision['id'],
                                order=c.document_lines.c.position):
        line = effects.rows(s, c.credit_line_profiles,
                            c.credit_line_profiles.c.document_line_id == envelope['id'])[0]
        result[envelope['line_id']] = dict(envelope, **line)
    return result


def release_claims(s, transaction_id, pending, created, event):
    from bookflow.company.credit_validation import _claims_made_by
    for claim in _claims_made_by(s, transaction_id):
        pending['credit_source_claims'].append(dict(
            claim, **created(), audit_event_id=event, kind='release', reverses_claim_id=claim['id']))


def retained_lines(s, saved, pending):
    """Copy commercial captures, including the exact intervals, without resolving masters."""
    result = []
    for line_id, old in saved.items():
        cells = effects.rows(s, c.credit_tax_components,
                             c.credit_tax_components.c.document_line_id == old['document_line_id'],
                             order=c.credit_tax_components.c.id)
        releases = [row for row in pending['credit_source_claims']
                    if row['kind'] == 'release' and row['credit_document_line_id'] == old['document_line_id']]
        claims = [dict(row, id=new_id(), kind='claim', reverses_claim_id=None) for row in releases]
        pending['credit_source_claims'].extend(claims)
        source = None
        if old['source_transaction_id']:
            first = claims[0]
            source = dict(transaction_id=old['source_transaction_id'], revision_id=old['source_revision_id'],
                          document_line_id=old['source_document_line_id'], line_id=old['source_line_id'],
                          base_quantity_microunits=first['source_base_quantity_microunits'],
                          net_minor_units=first['source_net_minor_units'])
        ordinal = effects.rows(s, c.sales_tax_line_keys, c.sales_tax_line_keys.c.line_id == line_id)[0]['tax_ordinal']
        result.append(dict(
            **{k: old[k] for k in ('item_id', 'description', 'quantity_microunits', 'unit_id',
                                   'unit_factor_nanounits', 'base_quantity_microunits',
                                   *credits.MONEY_COLUMNS)},
            line_id=line_id, profile=SalesLineProfile.model_validate_json(old['item_snapshot']),
            taxes=[dict(rule=None, source=cell, captured=cell,
                        source_tax_component_id=cell['source_tax_component_id']) for cell in cells],
            source=source, intervals=[(row['start_microunits'], row['end_microunits']) for row in claims],
            claim_rows=claims, tax_ordinal=ordinal))
    return result


def prepare_update(s, ctx, inp):
    source = credits.facts(s, inp.credit_memo, write=True)
    header, revision = source['header'], source['revision']
    if inp.expected_version is not None:
        journals.version_meta(s, header, inp.expected_version)
    if header['status'] != 'posted':
        raise _invalid('credit_memo', 'a voided credit memo cannot be corrected')
    # An empty patch has no accounting effect and does not create another revision.
    fields = inp.model_fields_set - {'credit_memo', 'expected_version', 'expected_facts_fingerprint'}
    if not fields:
        return unchanged_plan(s, inp, source)
    return credits.prepare(s, ctx, inp, previous=source)


def unchanged(s, previous, resolved):
    import json
    revision = previous['revision']
    stored = credits.profile_row(s, revision)
    if (resolved['date'], resolved['number'], resolved['memo'], resolved['origin']) != (
            revision['date'], revision['number'], revision['memo'], stored['origin']):
        return False
    if resolved['profile'].model_dump(mode='json') != json.loads(stored['profile_snapshot']):
        return False
    if resolved['custom_plan'].snapshot != json.loads(revision['custom_fields_snapshot']):
        return False
    pending = {'credit_source_claims': []}
    release_claims(s, previous['header']['id'], pending, lambda: {'id': new_id()}, new_id())
    old_lines = retained_lines(s, saved_lines(s, revision), pending)
    return ([line['line_id'] for line in old_lines] == [line.get('line_id') for line in resolved['lines']]
            and [credits._line_semantic(line) for line in old_lines]
            == [credits._line_semantic(line) for line in resolved['lines']])


def unchanged_plan(s, inp, source, fingerprint=None):
    header, revision = source['header'], source['revision']
    return Plan(CreditMemoWriteOutput(
        **credits.summary(header, revision, credits.profile_row(s, revision)),
        revision=credits.revision_output(s, revision),
        source_current=credits._source_current(s, header['id']), changed=False,
        facts_fingerprint=fingerprint), dict(input=inp, operation='update', changed=False))
