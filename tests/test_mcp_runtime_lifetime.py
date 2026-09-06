"""Actual hosted resource owners behind the existing monotonic lifetime fixture."""
from pathlib import Path
import json
import threading

import pytest

from bookflow.adapters.mcp.runtime import Runtime, owner
from bookflow.core import registry
from bookflow.core.context import Context, Interface
from bookflow.core.errors import BookflowError
from tests.test_mcp_intents import Clock
from tests.test_mcp_runtime import credential
from tests.test_row3_host import hosted


@pytest.mark.parametrize('binary', [False, True])
def test_real_prepared_resources_expire_without_client_polling(hosted, monkeypatch, binary):
    from tests.test_attachment_http import target, BODY
    rid = target(hosted)
    store = Path(hosted.info()['path']) / 'attachments'
    before = {str(p.relative_to(store)): p.read_bytes() for p in store.rglob('*') if p.is_file()}
    cred = credential(hosted, monkeypatch)
    runtime = Runtime.for_host(hosted.handle.host)
    clock = Clock()
    runtime.intents.clock = clock
    command = registry.get('attachment add' if binary else 'company update')
    intent = runtime.admit(command, Context.new(Interface.mcp, 'lifetime-owner', reason='Never submitted'),
                           cred, hosted.company_id, 'option', False)
    cleaned = threading.Event()
    if binary:
        transfer = runtime.prepare_transfer(intent, {'record_type': 'customer', 'record_id': rid,
            'original_filename': 'receipt.pdf', 'media_type': 'application/pdf'}, cred)
        with runtime.intents.preparation_worker(intent):
            for offset in range(0, len(BODY), 65536):
                transfer.body.write(BODY[offset:offset + 65536])
            runtime.intents.progress(intent)
        assert hosted.handle.host._transfers
    else:
        runtime.prepare_json(intent, {'fax': 'Never submitted'}, cred)
    actual_cleanup = intent.cleanup
    def cleanup():
        if actual_cleanup:
            actual_cleanup()
        cleaned.set()
    intent.cleanup = cleanup
    clock.now = 31
    assert cleaned.wait(3), 'Actual owner required a client request to release abandoned resources'
    assert not runtime.intents.active and not hosted.handle.host._transfers
    assert intent.frozen is None and intent.prepared_bytes == 0
    assert {str(p.relative_to(store)): p.read_bytes() for p in store.rglob('*') if p.is_file()} == before
    tombstone = runtime.intents.observe(intent.reference, owner(cred))
    assert tombstone.reason == 'expired_before_submission'
    # A retained completed tombstone is observed, never rescheduled.
    assert runtime.execute_json(intent, cred) is None
    assert hosted.info()['info']['fax'] != 'Never submitted'
    assert not [row for row in hosted.ok('audit.list', {'limit': 200}, company=hosted.company_id)['items'] if row['client_name'] == 'lifetime-owner']


def test_real_committed_receipt_absolute_expiry_never_executes_again(hosted, monkeypatch):
    cred = credential(hosted, monkeypatch)
    runtime = Runtime.for_host(hosted.handle.host)
    clock = Clock()
    runtime.intents.clock = clock
    intent = runtime.admit(registry.get('company update'),
        Context.new(Interface.mcp, 'lifetime-receipt', reason='One committed change'),
        cred, hosted.company_id, 'option', False)
    runtime.prepare_json(intent, {'fax': 'Committed once'}, cred)
    result = runtime.execute_json(intent, cred)
    runtime.intents.delivery(intent)
    runtime.intents.finish(intent, receipt=json.dumps(result).encode(), publication=result.permit.retained())
    for second in range(0, 300, 20):
        clock.now = second
        assert runtime.lookup(intent.reference, cred).receipt is not None
    clock.now = 300
    with pytest.raises(BookflowError) as caught:
        runtime.lookup(intent.reference, cred)
    assert caught.value.code == 'E_IO' and caught.value.details['outcome'] == 'unknown'
    assert intent.receipt is None and intent.publication is None
    assert not runtime.intents.active and not runtime.intents.completed
    assert hosted.info()['info']['fax'] == 'Committed once'
    events = hosted.ok('audit.list', {'command': 'company update'}, company=hosted.company_id)['items']
    assert len([e for e in events if e['client_name'] == 'lifetime-receipt']) == 1
