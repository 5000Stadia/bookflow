"""Cancelled downloads retain their descriptor until the active worker exits."""

import asyncio
import threading

import pytest

from tests.test_attachment_http import hosted, target, upload, request_for, endpoint


def test_cancelled_download_retains_reader_until_worker_finishes(hosted):
    added = upload(hosted, target(hosted)).json()
    started, release, completed = threading.Event(), threading.Event(), threading.Event()

    async def exercise():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            pass

        req = request_for(hosted, {"attachment": added["attachment"]["id"]}, receive)
        response = await endpoint(hosted)(hosted.company_id, "attachment.get", req)
        transfer = response.transfer
        reader = transfer.reader

        class WaitingReader:
            def read(self, size):
                started.set()
                try:
                    assert release.wait(5)
                    assert not reader.closed
                    return reader.read(size)
                finally:
                    completed.set()

        transfer.reader = WaitingReader()
        scope = dict(req.scope, asgi={"version": "3.0", "spec_version": "2.4"})
        task = asyncio.create_task(response(scope, receive, send))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert transfer.resource.lease in hosted.handle.host._transfers
            assert not reader.closed
        finally:
            release.set()
            assert await asyncio.to_thread(completed.wait, 5)
            for _ in range(100):
                if transfer.resource.lease not in hosted.handle.host._transfers:
                    break
                await asyncio.sleep(.01)
            assert reader.closed
            assert transfer.resource.lease not in hosted.handle.host._transfers

    asyncio.run(exercise())
