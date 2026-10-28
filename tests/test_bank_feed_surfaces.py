"""R108 increment 1: the bank inbox through Python, the CLI, HTTP and MCP.

Each surface gets an identical demo copy. It previews the sample OFX statement into Checking, imports it, refuses the
same file again and an OFX file for another account number, then reads the import back through show and query and
the inbox through bank-line query; moves it and undoes that (increment 2); imports it again and sorts it: a match and
its unmatch, a --lines ignore undone, an add that asks, then a check added and a transfer sent (increment 3). Every
surface must end with the same counts, keys and refusals.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix
from tests.test_bank_feeds import ofx, sample

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('bank-feed import', 'bank-feed show', 'bank-feed query', 'bank-line query',
                      'bank-feed move', 'bank-feed reverse',
                      'bank-line match', 'bank-line unmatch', 'bank-line ignore', 'bank-line add', 'bank-line transfer',
                      'bank-line undo'))


@pytest.mark.timeout(900)
def test_the_same_bank_import_through_python_cli_http_and_mcp(root, tmp_path):
    content = sample('checking-2026-09.ofx')
    other = ofx([('2026-09-02', '-2.00', 'B1', 'Y')], acct='000099990001')

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = matrix.call
                preview = await call(surface, 'bank-feed import', {'account': 'Checking', 'content': content},
                                     dry_run=True)
                done = await call(surface, 'bank-feed import', {'account': 'Checking', 'content': content})
                again = await call(surface, 'bank-feed import', {'account': 'Checking', 'content': content},
                                   rejected=True)
                mismatch = await call(surface, 'bank-feed import', {'account': 'Checking', 'content': other},
                                      rejected=True)
                shown = await call(surface, 'bank-feed show', {'import_id': done['import_id'], 'state': 'waiting'})
                listed = await call(surface, 'bank-feed query', {'account': 'Checking'})
                inbox = await call(surface, 'bank-line query', {'account': 'Checking', 'state': 'waiting'})
                # Increment 2: the import was the wrong account's -- moved in one step, then that undone.
                where = {'import_id': done['import_id'], 'to': 'Payment Example Bank'}
                move_plan = await call(surface, 'bank-feed move', where, dry_run=True)
                moved = await call(surface, 'bank-feed move', where)
                undo = {'import_id': moved['target']['import_id']}
                undo_plan = await call(surface, 'bank-feed reverse', undo, dry_run=True)
                undone = await call(surface, 'bank-feed reverse', {**undo, 'fingerprint': undo_plan['fingerprint']})
                # Increment 3: imported again and sorted -- matched and back, a --lines ignore undone, the fee
                # added as a check, the late deposit sent to another account as a transfer.
                await call(surface, 'bank-feed import', {'account': 'Checking', 'content': content})
                waiting = (await call(surface, 'bank-line query', {'account': 'Checking', 'state': 'waiting'}))['lines']
                deposit = next(v['id'] for v in waiting if v['amount'] == 20000)
                fee = next(v['id'] for v in waiting if v['amount'] == -1200)
                matched = await call(surface, 'bank-line match', {'line': deposit})
                unmatched = await call(surface, 'bank-line unmatch', {'line': deposit})
                ignored = await call(surface, 'bank-line ignore', {'lines': [fee], 'ignore_reason': 'other'})
                sort_plan = await call(surface, 'bank-line undo', {'batch': ignored['batch_id']}, dry_run=True)
                sort_undone = await call(surface, 'bank-line undo', {'batch': ignored['batch_id']})
                asked = await call(surface, 'bank-line add', {'line': fee, 'account': 'Bank Fees'}, rejected=True)
                add = {'line': fee, 'payee': 'Central Supply', 'account': 'Bank Fees'}
                add_plan = await call(surface, 'bank-line add', add, dry_run=True)
                added = await call(surface, 'bank-line add', add)
                sent = await call(surface, 'bank-line transfer', {'line': deposit, 'to': 'Payment Example Bank'})
                results[surface] = dict(preview=preview, done=done, again=again, mismatch=mismatch, shown=shown,
                                        listed=listed, inbox=inbox, move_plan=move_plan, moved=moved,
                                        undo_plan=undo_plan, undone=undone, matched=matched, unmatched=unmatched,
                                        ignored=ignored, sort_plan=sort_plan, sort_undone=sort_undone, asked=asked,
                                        add_plan=add_plan, added=added, sent=sent)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes, moves = set(), set()
    for surface, r in results.items():
        assert r['preview']['import_id'] is None and r['preview']['counts'] == r['done']['counts'], surface
        assert (r['done']['counts']['new'], r['done']['counts']['matched']) == (10, 8), surface
        assert 'E_ALREADY_IMPORTED' in str(r['again']) and 'E_ACCOUNT_MISMATCH' in str(r['mismatch']), surface
        assert r['shown']['by_state'] == {'matched': 8, 'waiting': 2} and len(r['shown']['lines']) == 2
        assert r['listed']['imports'][0]['id'] == r['done']['import_id'], surface
        assert r['inbox']['counts'] == {'matched': 8, 'waiting': 2}, surface
        assert (r['move_plan']['reversal']['lines'], r['move_plan']['reversal']['matches']) == (10, 8), surface
        assert r['move_plan']['target']['counts']['new'] == 10 and r['moved']['reversal']['state'] == 'reversed'
        assert r['moved']['target']['counts']['new'] == 10, surface
        assert (r['undo_plan']['lines'], r['undone']['state']) == (10, 'reversed'), surface
        moves.add((tuple(sorted(r['moved']['target']['counts'].items())), r['undone']['summary']))
        assert (r['matched']['lines'][0]['state'], r['unmatched']['lines'][0]['state']) == ('matched', 'waiting')
        assert r['ignored']['lines'][0]['state'] == 'ignored' and r['sort_plan']['lines'] == 1, surface
        assert r['sort_undone']['state'] == 'reversed' and 'E_VALIDATION' in str(r['asked']), surface
        assert r['add_plan']['entries'][0]['command'] == 'check post' and r['add_plan']['entries'][0]['entry_id'] is None
        assert (r['added']['lines'][0]['state'], r['added']['entries'][0]['command']) == ('added', 'check post')
        assert (r['sent']['lines'][0]['state'], r['sent']['entries'][0]['command']) == ('transfer', 'transfer post')
        moves.add((r['added']['summary'][:20], r['sent']['entries'][0]['input']['amount']))
        shapes.add((tuple(sorted(r['done']['counts'].items())), tuple(v['line_key'] for v in r['done']['lines']),
                    tuple((v['date'], v['amount'], v['state']) for v in r['inbox']['lines'])))
    assert len(shapes) == 1 and len(moves) == 2
