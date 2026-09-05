"""Raw authenticated binary routes, with bounded transport waits and owned cleanup."""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from bookflow.adapters.http import auth
from bookflow.core.dispatch import guard
from bookflow.core.errors import BookflowError
from bookflow.core.transfer_protocol import decode_input, encode_input
from bookflow.core.transfers import CHUNK_BYTES, HostedTransfer


def disposition(filename: str) -> str:
    fallback = re.sub(r'[^A-Za-z0-9._ -]', '_', filename).strip(' .') or 'attachment'
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(filename, safe="")}'


class _TransferOwner:
    """Keep resources alive until actual worker completion, including cancellation."""

    def __init__(self):
        self._lock = threading.Lock()
        self._active = 0
        self._closing = False
        self._transfer = None

    def adopt(self, transfer):
        with self._lock:
            self._transfer = transfer

    def run(self, operation, *args):
        with self._lock:
            if self._closing:
                raise BookflowError("E_IO", details={"check": "transfer_cancelled"})
            self._active += 1
        try:
            return operation(*args)
        finally:
            with self._lock:
                self._active -= 1
                transfer = self._transfer if self._closing and not self._active else None
            if transfer is not None:
                transfer.close()

    def close(self):
        # Mark abandonment synchronously: another cancellation cannot skip it.
        # An active worker performs cleanup itself when it actually returns.
        with self._lock:
            self._closing = True
            transfer = self._transfer if not self._active else None
        if transfer is not None:
            transfer.close()


class LeasedDownload(StreamingResponse):
    def __init__(self, transfer, owner):
        self.transfer = transfer
        self.owner = owner
        info = transfer.resource.info
        metadata = transfer.prepared.metadata or transfer.output
        headers = {"Content-Disposition": disposition(metadata["original_filename"]),
                   "Content-Length": str(info.size_bytes), "Cache-Control": "no-store",
                   "X-Content-Type-Options": "nosniff", "X-Bookflow-SHA256": info.sha256,
                   "X-Bookflow-Output": encode_input(transfer.output)}
        super().__init__(self.chunks(), media_type=metadata["media_type"], headers=headers)

    async def chunks(self):
        transfer = self.transfer
        digest, size = hashlib.sha256(), 0
        while True:
            await run_in_threadpool(self.owner.run, transfer.check_output)
            chunk = await run_in_threadpool(self.owner.run, transfer.reader.read, CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > transfer.resource.info.size_bytes:
                raise BookflowError("E_IO", details={"check": "download_size"})
            digest.update(chunk)
            yield chunk
        if size != transfer.resource.info.size_bytes or digest.hexdigest() != transfer.resource.info.sha256:
            raise BookflowError("E_IO", details={"check": "download_digest"})

    async def __call__(self, scope, receive, send):
        async def bounded_send(message):
            timeout = min(30.0, self.transfer.resource.lease.remaining_seconds())
            try:
                await asyncio.wait_for(send(message), timeout=timeout)
            except TimeoutError:
                raise BookflowError("E_IO", details={"check": "download_timeout"}) from None
        try:
            await super().__call__(scope, receive, bounded_send)
        finally:
            self.owner.close()


def install(app, host, *, credential, lookup, selector_of, make_context, secret_of):
    @app.post("/companies/{company_id}/transfers/{route}")
    async def transfer_command(company_id: str, route: str, request: Request):
        owner = _TransferOwner()
        def begin():
            cred = credential(request)
            cmd = lookup(route)
            selector, source = selector_of(request, company_id)
            raw = decode_input(request.headers.get("x-bookflow-input", ""))
            dry_run = request.query_params.get("dry_run") in ("1", "true")
            if cmd.transfer is None:
                raise BookflowError("E_USAGE", message="This command does not transfer a binary body.")
            if cmd.transfer.direction == "input" and request.headers.get("content-type", "").lower() != "application/octet-stream":
                raise BookflowError("E_VALIDATION", message="Upload body must be application/octet-stream.")
            secret = secret_of(request)

            def recheck(s):
                row = auth.resolve_token(s.hub, secret)
                if row["id"] != cred.token_id or row["user_id"] != cred.user_id or row.get("on_behalf_of") != cred.on_behalf_of:
                    raise BookflowError("E_UNAUTHENTICATED", details={"reason": "credential changed"})
            transfer = guard(lambda: HostedTransfer(host, cmd, raw, make_context(request, cred),
                         cred.user_id, cred.login, selector, source, dry_run, recheck), cred.hub_admin)
            owner.adopt(transfer)
            return transfer

        keep = False
        try:
            transfer = await run_in_threadpool(owner.run, begin)
            iterator = request.stream().__aiter__()
            while True:
                timeout = min(30.0, transfer.resource.lease.remaining_seconds())
                try:
                    chunk = await asyncio.wait_for(iterator.__anext__(), timeout=timeout)
                except StopAsyncIteration:
                    break
                except (TimeoutError, ClientDisconnect):
                    raise BookflowError("E_IO", details={"check": "upload_interrupted"}) from None
                if transfer.cmd.transfer.direction == "output":
                    if chunk:
                        raise BookflowError("E_VALIDATION", message="A download request must have an empty body.")
                else:
                    for start in range(0, len(chunk), CHUNK_BYTES):
                        await run_in_threadpool(owner.run, transfer.body.write, chunk[start:start + CHUNK_BYTES])
            if transfer.cmd.transfer.direction == "input":
                return await run_in_threadpool(owner.run, lambda: guard(transfer.finish_input))
            response = LeasedDownload(transfer, owner)
            keep = True
            return response
        finally:
            if not keep:
                owner.close()
