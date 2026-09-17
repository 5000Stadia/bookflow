"""History uses one current-authorized read snapshot, including continuation reads."""
from concurrent.futures import ThreadPoolExecutor
import threading

from tests.test_row3_host import hosted  # noqa: F401
from tests.test_credit_windows import _books
from tests.test_customer_refund_correction_form import _refunded


def _activate(hosted):
    state = hosted.ok('permission.show')
    hosted.ok('permission.activate', {'expected_generation': state['generation'],
                                     'expected_catalog_sha256': state['catalog_sha256']})


def test_history_continuation_rejects_revoked_credential_and_foreign_member(hosted):
    books = _books(hosted)
    books['ok'] = lambda name, raw: hosted.ok(name, raw, company=hosted.company_id,
        headers={'X-Bookflow-Reason': 'History authority witness'})
    paid = _refunded(books, explicit=True)
    books['ok']('customer-refund.update', {'refund': paid['id'], 'memo': 'Corrected'})
    _activate(hosted)
    first = hosted.ok('customer-refund.history', {'refund': paid['id'], 'limit': 1}, company=hosted.company_id)
    outsider = hosted.ok('token.issue', {'user': 'outsider', 'label': 'history outsider'})['secret']
    hidden = hosted.call('customer-refund.history', {'refund': paid['id']}, company=hosted.company_id,
                        headers={'Authorization': 'Bearer ' + outsider})
    assert hidden.status_code == 404 and hidden.json()['code'] == 'E_COMPANY_NOT_FOUND'
    observer = hosted.ok('token.issue', {'label': 'history observer'})['secret']
    assert hosted.call('token.revoke', {'token': hosted.token},
                      headers={'Authorization': 'Bearer ' + observer}).status_code == 200
    denied = hosted.call('customer-refund.history', {'refund': paid['id'], 'limit': 1,
                         'cursor': first['next_cursor']}, company=hosted.company_id)
    assert denied.status_code == 401 and denied.json()['code'] == 'E_UNAUTHENTICATED'
    assert 'items' not in denied.json()
    assert hosted.handle.host._readers_attached == 0


def test_concurrent_correction_does_not_mix_header_revisions_and_effects(hosted, monkeypatch):
    from bookflow.company import refund_history
    books = _books(hosted)
    books['ok'] = lambda name, raw: hosted.ok(name, raw, company=hosted.company_id,
        headers={'X-Bookflow-Reason': 'History authority witness'})
    paid = _refunded(books, explicit=True)
    _activate(hosted)
    reached, release = threading.Event(), threading.Event()
    original = refund_history.page_state

    def pause(*args, **kwargs):
        state = original(*args, **kwargs)
        reached.set()
        assert release.wait(10)
        return state

    monkeypatch.setattr(refund_history, 'page_state', pause)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(hosted.call, 'customer-refund.history', {'refund': paid['id']},
                             company=hosted.company_id)
        try:
            assert reached.wait(10)
            corrected = books['ok']('customer-refund.update', {'refund': paid['id'], 'memo': 'Concurrent correction'})
        finally:
            release.set()
        response = future.result(10)
    assert response.status_code == 200, response.text
    history = response.json()
    assert history['version'] == 1 and history['current_revision_id'] == paid['revision']['id']
    assert history['count'] == 1
    assert [b['kind'] for b in history['items'][0]['batches']] == ['original']
    assert [r['kind'] for r in history['items'][0]['consumptions']] == ['consume']
    assert [e['command'] for e in history['items'][0]['events']] == ['customer-refund post']
    assert corrected['revision']['revision_number'] == 2
