"""Whole pending print-run cancellation; paper already printed cannot be recalled."""
from __future__ import annotations

from bookflow import BookflowError
from bookflow.company import accounts, batches, check_printing as printing
from bookflow.company.check_print_models import CheckPrintRunInput
from bookflow.core.ids import new_id

KIND = 'check-print'
COMMAND = 'check print-cancel'


def _read(s, batch):
    batches.require_read(s, 'check print-run')


def _problems(s, run_id):
    problems = []
    for item in printing._items(s, run_id):
        outcome = item.get('outcome')
        if outcome is not None:
            explanation = ('a printed cheque cannot be recalled' if outcome == 'printed' else
                           f'its print outcome is already {outcome}')
            problems.append(dict(code='print_outcome', record_type='check_print_run_item', record_id=item['id'],
                                 message=f"Cheque {item['check_number']} ({item['transaction_id']}): {explanation}.",
                                 details={'run': run_id, 'transaction_id': item['transaction_id'], 'outcome': outcome}))
    return problems


def _guard(s, run_id):
    problems = _problems(s, run_id)
    if problems:
        raise BookflowError('E_VALIDATION', message='; '.join(p['message'] for p in problems),
                            details={'blockers': problems})


def _plan(s, ctx, batch, effects, done, probe, blockers):
    pending = [e for e in reversed(effects) if (e['record_type'], e['record_id']) not in done]
    steps, work = [], []
    for effect in pending:
        run_id = effect['record_id']
        blockers.extend(dict(p, seq=effect['seq']) for p in _problems(s, run_id))
        payload = {'run': run_id}
        if probe:
            try:
                batches.run_nested(s, ctx, COMMAND, payload, source_ref=f"reverse:{batch['id']}",
                                   dry_run=True)
            except BookflowError as error:
                blockers.append(dict(seq=effect['seq'], record_type=effect['record_type'], record_id=run_id,
                                     code=error.code, message=f'Print run {run_id}: {error.message}',
                                     details=error.details))
        items = printing._items(s, run_id)
        run = printing._run_row(s, run_id)
        account = accounts.resolve_account(s.company, run['account_id'])
        pointer_before = pointer_after = account['next_check_number']
        try:
            _, writes = printing._confirm_plan(s, ctx, CheckPrintRunInput(run=run_id),
                                               batches.now(), new_id(), cancel=True)
            if writes['pointer']:
                pointer_after = writes['pointer'][1]
        except BookflowError:
            # The inverse probe or outcome preflight above already names this refusal.
            pass
        detail = {'command': COMMAND, 'input': payload,
                  'account': account['id'],
                  'next_check_number': {'before': pointer_before, 'after': pointer_after},
                  'cheques': [{'transaction_id': i['transaction_id'], 'check_number': i['check_number'],
                               'outcome': 'cancelled', 'number_released': True} for i in items]}
        steps.append(dict(seq=effect['seq'], record_type=effect['record_type'], record_id=run_id,
                          label=f'Print run {run_id}', action='cancel', version=None, steps=[detail]))
        work.append(dict(effect=effect, record_type=effect['record_type'], record_id=run_id,
                         seq=effect['seq'], label=f'Print run {run_id}', payload=payload))
    return dict(steps=steps, work=work, blockers=blockers, records=len(effects), pending=len(pending))


def _run(s, ctx, batch, reversal_id, item):
    batches.run_nested(s, ctx, COMMAND, item['payload'], source_ref=f"reverse:{batch['id']}",
                       before=lambda session: _guard(session, item['record_id']),
                       recorder=batches._reversal_recorder(reversal_id, item['effect'], 0, None))


batches.register(batches.Adapter(KIND, planner=_plan, runner=_run, reader=_read, blocks=True,
                                 label='Pending check print run'))
