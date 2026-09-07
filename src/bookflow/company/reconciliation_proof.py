"""Independent raw-leg coverage, inverse bijection and dated GL witnesses."""
from collections import Counter
from bookflow.company.reconciliation_adapters import require


def prove(g, history, current, account_id, cutoff):
    """Amounts alone are insufficient: prove unique ownership before summing."""
    legs = g.by_id('posting_lines'); sources = g.by_id('posting_line_sources'); batches = g.by_id('posting_batches')
    require(len(legs)==len(g.rows['posting_lines']) and len(sources)==len(g.rows['posting_line_sources']), 'duplicate_physical_identity')
    originals = {key:r for key,r in legs.items() if batches[r['batch_id']]['kind'] != 'reversal'}
    bank = {key:r for key,r in originals.items() if g.accounts[r['account_id']]['type'] in ('bank','credit_card')}
    represented = Counter(key for v in history if v.active for key in v.posting_line_ids)
    require(represented == Counter({key:1 for key in bank}), 'business_leg_coverage')
    for value in history:
        if not value.active:
            require(not value.posting_line_ids and not value.source_ids, 'inactive_coverage')
            continue
        owned = [legs[key] for key in value.posting_line_ids]
        require(all((r['transaction_id'],r['batch_id'],r['account_id'],r['currency']) ==
            (value.ref.transaction_id,value.business_batch_id,value.account_id,value.currency) for r in owned), 'effect_leg_owner')
        require(sum(r['debit_minor_units']-r['credit_minor_units'] for r in owned)==value.signed_debit, 'effect_leg_amount')
        actual_sources = {r['id'] for r in sources.values() if r['posting_line_id'] in value.posting_line_ids}
        require(set(value.source_ids)==actual_sources and len(value.source_ids)==len(actual_sources), 'effect_source_coverage')
    # All legs (including nonbank offsets) have complete positive attribution.
    for leg in legs.values():
        allocations = [r for r in sources.values() if r['posting_line_id']==leg['id']]
        require(bool(allocations) and sum(r['amount_minor_units'] for r in allocations)==leg['debit_minor_units']+leg['credit_minor_units'], 'source_amount_coverage')
        require(all(r['transaction_id']==leg['transaction_id'] and r['currency']==leg['currency'] for r in allocations), 'source_owner')
    reversal_batches = [b for b in batches.values() if b['kind']=='reversal']
    require(len({b['reverses_batch_id'] for b in reversal_batches})==len(reversal_batches), 'double_batch_inverse')
    inverted = set()
    for batch in reversal_batches:
        original = batches[batch['reverses_batch_id']]
        require(original['kind']!='reversal' and (original['transaction_id'],original['effective_date'])==(batch['transaction_id'],batch['effective_date']), 'inverse_batch_anchor')
        expected = {r['id'] for r in legs.values() if r['batch_id']==original['id']}
        actual = [r for r in legs.values() if r['batch_id']==batch['id']]
        require(Counter(r['reversed_line_id'] for r in actual)==Counter({key:1 for key in expected}), 'inverse_leg_bijection')
        for row in actual:
            old = legs[row['reversed_line_id']]
            ignored = {'id','batch_id','created_at','created_by','created_via','reversed_line_id','debit_minor_units','credit_minor_units'}
            require({k:v for k,v in row.items() if k not in ignored}=={k:v for k,v in old.items() if k not in ignored}, 'inverse_leg_facts')
            require((row['debit_minor_units'],row['credit_minor_units'])==(old['credit_minor_units'],old['debit_minor_units']), 'inverse_leg_amount')
            expected_sources = {r['id'] for r in sources.values() if r['posting_line_id']==old['id']}
            copied = [r for r in sources.values() if r['posting_line_id']==row['id']]
            require(Counter(r['reversed_source_id'] for r in copied)==Counter({key:1 for key in expected_sources}), 'inverse_source_bijection')
            for src in copied:
                prior = sources[src['reversed_source_id']]
                ignored_source = {'id','posting_line_id','created_at','created_by','created_via','reversed_source_id'}
                require({k:v for k,v in src.items() if k not in ignored_source}=={k:v for k,v in prior.items() if k not in ignored_source}, 'inverse_source_facts')
        inverted.update(expected)
    active = Counter(key for v in current if v.active for key in v.posting_line_ids)
    require(active==Counter({key:1 for key in bank if key not in inverted}), 'current_leg_coverage')
    require(len({v.ref for v in current})==len(current), 'duplicate_current_reference')
    groups = {}
    for value in history:
        if value.active:
            signature=(value.account_id,value.signed_debit>0)
            require(groups.setdefault(value.movement_key,signature)==signature, 'mixed_movement')
    total = sum(v.signed_debit for v in current if v.active and v.account_id==account_id and v.effective_date<=cutoff)
    gl = sum(r['debit_minor_units']-r['credit_minor_units'] for r in legs.values()
        if r['account_id']==account_id and batches[r['batch_id']]['effective_date']<=cutoff)
    require(total==gl, 'dated_gl_equivalence')
    return total, gl
