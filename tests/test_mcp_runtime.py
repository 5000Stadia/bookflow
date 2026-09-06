"""Host executor identity witnesses before HTTP/SDK intent-route integration."""

from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from bookflow.adapters.http.execution import PublishedDocument
from bookflow.adapters.mcp.runtime import Runtime
from bookflow.core import registry
from bookflow.core.context import Context, Interface
from bookflow.core.errors import BookflowError
from tests.test_row3_host import hosted


def test_host_missing_optional_parser_rejects_preflight_with_installation_guidance(hosted, monkeypatch):
    import builtins
    original = builtins.__import__
    def unavailable(name, *args, **kwargs):
        if name == 'ijson':
            raise ImportError('missing optional parser')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', unavailable)
    response = hosted.api.get('/adapters/mcp', headers=hosted.bearer)
    assert response.status_code == 400
    assert response.json()['code'] == 'E_USAGE'
    assert 'host environment' in response.json()['message']
    assert not hasattr(hosted.handle.host, '_mcp_runtime')


def credential(hosted, monkeypatch):
    captured = []
    original = PublishedDocument.check

    def observe(self, **kwargs):
        captured.append(self.credential)
        return original(self, **kwargs)

    monkeypatch.setattr(PublishedDocument, "check", observe)
    hosted.info()
    return captured[-1]


def test_recovery_race_runs_one_real_host_write_and_one_audit_event(hosted, monkeypatch):
    cred = credential(hosted, monkeypatch)
    runtime = Runtime.for_host(hosted.handle.host)
    assert Runtime.for_host(hosted.handle.host) is runtime
    ctx = Context.new(Interface.mcp, "runtime-identity", reason="One frozen execution")
    intent = runtime.admit(registry.get("company update"), ctx, cred, hosted.company_id, "option", False)
    runtime.prepare_json(intent, {"fax": "runtime identity"}, cred)
    barrier = threading.Barrier(2)

    def recover():
        observed = runtime.lookup(intent.reference, cred)
        barrier.wait()
        return runtime.execute_json(observed, cred)

    with ThreadPoolExecutor(2) as pool:
        results = [future.result() for future in [pool.submit(recover), pool.submit(recover)]]
    assert sum(result is None for result in results) == 1
    result = next(result for result in results if result is not None)
    runtime.intents.delivery(intent)
    runtime.intents.finish(intent, receipt=json.dumps(result).encode(), publication=result.permit.retained())
    assert runtime.execute_json(runtime.lookup(intent.reference, cred), cred) is None
    events = hosted.ok("audit.list", {"command": "company update"}, company=hosted.company_id)["items"]
    assert len([row for row in events if row["client_name"] == "runtime-identity"]) == 1
    assert hosted.info()["info"]["fax"] == "runtime identity"


def test_rejected_input_releases_admission_and_cannot_be_replaced(hosted, monkeypatch):
    cred = credential(hosted, monkeypatch)
    runtime = Runtime.for_host(hosted.handle.host)
    intent = runtime.admit(registry.get("company update"), Context.new(Interface.mcp, "rejection"),
                           cred, hosted.company_id, "option", False)
    before = hosted.info()["version"]
    with pytest.raises(BookflowError):
        runtime.prepare_json(intent, {"unregistered_input": True}, cred)
    assert intent.reference not in runtime.intents.active
    with pytest.raises(BookflowError):
        runtime.prepare_json(intent, {"fax": "replacement"}, cred)
    assert hosted.info()["version"] == before


def test_real_transfer_prepares_seals_executes_once_and_downloads(hosted, monkeypatch):
    import hashlib
    from tests.test_attachment_http import target, BODY
    cred = credential(hosted, monkeypatch)
    runtime = Runtime.for_host(hosted.handle.host)
    record = target(hosted)
    ctx = Context.new(Interface.mcp, "runtime-binary", reason="Save receipt")
    intent = runtime.admit(registry.get("attachment add"), ctx, cred, hosted.company_id, "option", False)
    transfer = runtime.prepare_transfer(intent, {"record_type": "customer", "record_id": record,
        "original_filename": "receipt.pdf", "media_type": "application/pdf"}, cred)
    assert hosted.ok("attachment.list", {"record_type": "customer", "record_id": record}, company=hosted.company_id)["count"] == 0
    with runtime.intents.preparation_worker(intent):
        for offset in range(0, len(BODY), 65536):
            transfer.body.write(BODY[offset:offset + 65536])
            runtime.intents.progress(intent)
    runtime.seal_transfer(intent, cred, hashlib.sha256(BODY).hexdigest(), len(BODY))
    result = runtime.execute_transfer(intent, cred)
    assert result['attachment']['sha256'] == hashlib.sha256(BODY).hexdigest()
    assert runtime.execute_transfer(intent, cred) is None
    runtime.intents.delivery(intent)
    runtime.intents.finish(intent, receipt=json.dumps(result).encode(), publication=result.permit.retained())
    read = runtime.admit(registry.get("attachment get"), Context.new(Interface.mcp, "download"), cred,
                         hosted.company_id, "option", False)
    download = runtime.prepare_transfer(read, {"attachment": result['attachment']['id']}, cred)
    assert download.output is None
    fetched = runtime.execute_transfer(read, cred)
    assert fetched['sha256'] == hashlib.sha256(BODY).hexdigest()
    assert download.reader.read() == BODY
    runtime.intents.delivery(read)
    runtime.intents.finish(read, receipt=json.dumps(fetched).encode(), publication=fetched.permit.retained())
    assert not hosted.handle.host._transfers
