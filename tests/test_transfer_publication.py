"""Post-execution transfer publication faults through actual hosted routes."""

from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from bookflow.core.publication import PublicationPermit
from tests.test_attachment_http import hosted, target, upload, headers


@pytest.mark.parametrize("direction", ["input", "output"])
def test_transfer_revocation_before_headers_does_not_publish_metadata(hosted, monkeypatch, direction):
    record = target(hosted)
    observer = hosted.ok("token.issue", {"label": "transfer publication observer"})["secret"]
    observer_headers = {"Authorization": "Bearer " + observer}
    attachment = upload(hosted, record).json()["attachment"]["id"] if direction == "output" else None
    reached, release = threading.Event(), threading.Event()
    original = PublicationPermit.check
    name = "attachment add" if direction == "input" else "attachment get"

    def delayed(self, host, credential, **kwargs):
        if self.cmd.name == name and not reached.is_set():
            reached.set()
            assert release.wait(10)
        return original(self, host, credential, **kwargs)

    monkeypatch.setattr(PublicationPermit, "check", delayed)
    with ThreadPoolExecutor(1) as pool:
        if direction == "input":
            future = pool.submit(upload, hosted, record)
        else:
            future = pool.submit(hosted.api.post, f"/companies/{hosted.company_id}/transfers/attachment.get",
                                 headers=headers(hosted, {"attachment": attachment}))
        try:
            assert reached.wait(10)
            result = hosted.call("token.revoke", {"token": hosted.token}, headers=observer_headers)
            assert result.status_code == 200
        finally:
            release.set()
        response = future.result(10)
    assert response.status_code == 401
    assert response.json()["details"]["outcome"] == "unknown"
    assert "x-bookflow-output" not in response.headers
    assert "Receipt" not in response.text
    # An already committed upload is retained; denial neither rolls it back nor repeats it.
    rows = hosted.call("attachment.list", {"record_type": "customer", "record_id": record},
                       company=hosted.company_id, headers=observer_headers).json()
    assert rows["count"] == 1
    assert not hosted.handle.host._transfers
    assert hosted.handle.host._readers_attached == 0
