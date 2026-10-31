"""Native aggregate inverses behind the shared batch door.

A native door owns its run lifecycle and aggregate write authority. Its dry run is
checked beside the whole batch plan; its execution still uses the shared engine.
"""
from __future__ import annotations

from bookflow import BookflowError
from bookflow.company import batches
from bookflow.core import registry

DELEGATES = {
    'bank-import': ('bank-feed reverse', 'import_id'),
    'bank-sort': ('bank-line undo', 'batch'),
    'opening': ('opening-draft reverse', 'draft'),
    'bulk-edit': ('bulk-edit reverse', 'run'),
    'import': ('import reverse', 'run'),
}


def reverse(s, ctx, inp, *, dry_run: bool) -> dict | None:
    batch = batches._batch(s, inp.batch)
    delegate = DELEGATES.get(batch['kind'])
    if delegate is None:
        return None
    command, selector = delegate
    payload = {selector: batch['id'] if selector == 'batch' else batch['source_id']}
    if 'limit' in registry.get(command).input_model.model_fields:
        payload['limit'] = inp.limit
    # Native fingerprints may include a run or account in addition to batch effects.
    # Check the shared door's fingerprint here, and let the native door plan anew.
    found = batches.plan(s, ctx, inp.batch, probe=True)
    try:
        batches.run_nested(s, ctx, command, payload, dry_run=True,
                           source_ref=f"reverse:{inp.batch}", recovery=False)
    except BookflowError as error:
        if error.code == 'E_BATCH_BLOCKED':
            for blocker in error.details.get('blockers', []):
                if blocker not in found['blockers']:
                    found['blockers'].append(blocker)
        else:
            found['blockers'].append(dict(
                seq=None, record_type='batch', record_id=inp.batch, code=error.code,
                message=f'{command}: {error.message}', details=error.details,
                clears=f'Clear the refusal from `{command}` before reversing this batch.'))
    if found['blockers']:
        raise batches.refuse(found)
    starting = batch['state'] not in ('reversing', 'blocked')
    if starting and not found['steps']:
        raise BookflowError('E_VALIDATION', message='This batch has nothing reversible.',
                            details={'field': 'batch', 'batch': inp.batch, 'problem': 'nothing_reversible'})
    if inp.fingerprint is not None and inp.fingerprint != found['fingerprint']:
        raise BookflowError('E_VERSION_CONFLICT', message='The batch changed since that dry run; run it again.',
                            details={'field': 'fingerprint', 'current': found['fingerprint']})
    if dry_run:
        return batches._result(s, found, state=batch['state'], reversed_now=0, dry_run=True)
    native = batches.run_nested(s, ctx, command, payload, dry_run=False,
                                source_ref=f"reverse:{inp.batch}", recovery=False)
    current = batches._batch(s, inp.batch)
    if current['state'] == 'reversed':
        return dict(batch=batches.describe(s, current), state='reversed',
                    reversed_now=native.get('reversed_now', len(found['work'])), remaining=0,
                    steps=[], blockers=[], fingerprint=None, reversal_batch=current['reversed_by'],
                    event_id=current['reversal_event_id'], dry_run=False)
    remaining = batches.plan(s, ctx, inp.batch, probe=False)
    return batches._result(s, remaining, state=current['state'],
                           reversed_now=native.get('reversed_now', found['pending'] - remaining['pending']))
