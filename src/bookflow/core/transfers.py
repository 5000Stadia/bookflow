"""Authorized, bounded binary resources shared by command adapters."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable

from bookflow.company.attachment_store import BodyInfo, OwnedStage, StagedAttachment, open_verified
from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.transfer_resources import TransferLease

CHUNK_BYTES = 65536


@dataclass(frozen=True)
class TransferPreparation:
    store: Path
    limit: int
    info: BodyInfo | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class TransferResource:
    lease: TransferLease
    store: Path
    info: BodyInfo | None = None
    staged: StagedAttachment | None = None


def prepare(cmd, raw, ctx, s, *, selector=None, source="option", dry_run=False):
    """Authorize metadata without consuming a body or running an upload plan."""
    from bookflow.core.dispatch import authorize, validate_context, validate_input
    if cmd.transfer is None:
        raise BookflowError("E_USAGE", message="This command does not transfer a binary body.")
    if dry_run and not cmd.is_write:
        raise BookflowError("E_USAGE", message="Dry-run applies only to writes.")
    inp = validate_input(cmd, raw)
    validate_context(ctx)
    ctx = authorize(cmd, ctx, s, company_selector=selector, company_source=source,
                    dry_run=dry_run, read_only=True)
    result = cmd.transfer.prepare(inp, ctx, s)
    if not isinstance(result, TransferPreparation) or not 0 < result.limit <= 100_000_000:
        raise BookflowError("E_VALIDATION", message="Invalid transfer preparation.")
    return result


def validate_resource(cmd, inp, ctx, s):
    resource = s.transfer
    if not isinstance(resource, TransferResource) or resource.info is None:
        raise BookflowError("E_USAGE", message="Use this command's binary transfer interface.")
    if resource.lease.company_id != s.company_row["id"] or resource.lease.principal_id != s.actor.id:
        raise BookflowError("E_PERMISSION", details={"capability": cmd.capability})
    if resource.lease.state == "writer":
        resource.lease.check_start()
    else:
        resource.lease.check_io()
    expected = cmd.transfer.prepare(inp, ctx, s)
    if expected.store != resource.store:
        raise BookflowError("E_IO", details={"check": "transfer_store"})
    if cmd.transfer.direction == "input":
        if resource.info.size_bytes > expected.limit:
            raise BookflowError("E_VALUE_RANGE", details={"field": "body", "limit": expected.limit})
        if not s.dry_run and resource.staged is None:
            raise BookflowError("E_USAGE", message="An upload requires its owned staged body.")
    elif expected.info != resource.info:
        raise BookflowError("E_IO", details={"check": "transfer_body"})


class InputBody:
    """Incremental input, with no persistent stage for a dry run."""

    def __init__(self, resource: TransferResource, limit: int, dry_run: bool):
        self.resource, self.limit = resource, limit
        self.stage = None if dry_run else OwnedStage(resource.store, limit, resource.lease)
        self.digest = hashlib.sha256()
        self.size = 0
        self.completed = False

    def write(self, chunk: bytes):
        self.resource.lease.check_io()
        if self.completed or not isinstance(chunk, bytes) or len(chunk) > CHUNK_BYTES:
            raise BookflowError("E_VALIDATION", message="Invalid binary input chunk.")
        if self.size + len(chunk) > self.limit:
            raise BookflowError("E_VALUE_RANGE", details={"field": "body", "limit": self.limit})
        if self.stage is not None:
            self.stage.write(chunk)
        self.digest.update(chunk)
        self.size += len(chunk)

    def receive(self, stream: BinaryIO):
        while True:
            self.resource.lease.check_io()
            requested = min(CHUNK_BYTES, self.limit - self.size + 1)
            chunk = stream.read(requested)
            self.resource.lease.check_io()
            if not isinstance(chunk, bytes) or len(chunk) > requested:
                raise BookflowError("E_VALIDATION", message="A binary stream must return bytes.")
            if not chunk:
                break
            self.write(chunk)

    def complete(self):
        self.resource.lease.check_io()
        if self.completed:
            raise BookflowError("E_VALIDATION", message="The upload body is already complete.")
        self.resource.info = BodyInfo(self.digest.hexdigest(), self.size)
        if self.stage is not None:
            self.resource.staged = self.stage.complete()
            if self.resource.staged.info != self.resource.info:
                raise BookflowError("E_IO", details={"check": "staged_digest"})
        self.completed = True


def copy_output(reader: BinaryIO, sink: BinaryIO, info: BodyInfo, check: Callable[[], None]):
    """Verify actual emitted bytes; a caller-owned sink may contain partial data on error."""
    digest, size = hashlib.sha256(), 0
    while True:
        check()
        chunk = reader.read(min(CHUNK_BYTES, info.size_bytes - size + 1))
        if not isinstance(chunk, bytes) or len(chunk) > CHUNK_BYTES:
            raise BookflowError("E_IO", details={"check": "download_read"})
        if not chunk:
            break
        size += len(chunk)
        if size > info.size_bytes:
            raise BookflowError("E_IO", details={"check": "download_size"})
        digest.update(chunk)
        position = 0
        while position < len(chunk):
            check()
            count = sink.write(chunk[position:])
            if not isinstance(count, int) or isinstance(count, bool) or not 0 < count <= len(chunk) - position:
                raise BookflowError("E_IO", details={"check": "download_write"})
            position += count
    if size != info.size_bytes or digest.hexdigest() != info.sha256:
        raise BookflowError("E_IO", details={"check": "download_digest"})


class HostedTransfer:
    """One authorized reader admission followed by leased I/O and optional writer handoff."""

    def __init__(self, host, cmd, raw, ctx, user_id, login="", selector=None,
                 source="option", dry_run=False, authorize_session=None):
        from bookflow.core.dispatch import _close, execute
        self.host, self.cmd, self.raw, self.ctx = host, cmd, raw, ctx
        self.user_id, self.login = user_id, login
        self.selector, self.source, self.dry_run = selector, source, dry_run
        self.authorize_session = authorize_session or (lambda s: None)
        self.resource = None
        self.reader = None
        self.output = None
        if cmd.transfer is not None and cmd.transfer.direction == "input" and not dry_run:
            def recover(s):
                from bookflow.core.dispatch import authorize, validate_input, validate_context
                from bookflow.company.attachment_gc import recover_pending
                self.authorize_session(s)
                validate_input(cmd, raw)
                validate_context(ctx)
                authorize(cmd, ctx, s, company_selector=selector, company_source=source)
                recover_pending(s, ctx)
            host.run_write(user_id, login, recover)
        s = host.reader_session(user_id, login)
        try:
            try:
                self.authorize_session(s)
                self.prepared = prepare(cmd, raw, ctx, s, selector=selector, source=source, dry_run=dry_run)
                lease = host.acquire_transfer(s.actor.id, s.company_row["id"])
                self.resource = TransferResource(lease, self.prepared.store, self.prepared.info)
                if cmd.transfer.direction == "output":
                    s.transfer = self.resource
                    self.output = execute(cmd, raw, ctx, s, dry_run=False)
            finally:
                try:
                    _close(s)
                finally:
                    host.reader_done()
        except BaseException:
            self.close()
            raise
        try:
            if cmd.transfer.direction == "input":
                self.body = InputBody(self.resource, self.prepared.limit, dry_run)
            else:
                self.reader = open_verified(self.resource.store, self.resource.info)
                lease.add_cleanup(self.reader.close)
        except BaseException:
            self.close()
            raise

    def receive(self, stream):
        self.body.receive(stream)

    def finish_input(self):
        from bookflow.core.dispatch import _close, execute
        self.body.complete()

        def finish(s):
            self.authorize_session(s)
            s.transfer = self.resource
            return execute(self.cmd, self.raw, self.ctx, s, company_selector=self.selector,
                           company_source=self.source, dry_run=self.dry_run)
        if not self.dry_run:
            self.output = self.host.run_write(self.user_id, self.login, finish, resource=self.resource.lease)
        else:
            s = self.host.reader_session(self.user_id, self.login)
            try:
                self.output = finish(s)
            finally:
                try:
                    _close(s)
                finally:
                    self.host.reader_done()
        return self.output

    def check_output(self):
        from bookflow.core.dispatch import _close
        self.resource.lease.check_io()
        s = self.host.reader_session(self.user_id, self.login)
        try:
            self.authorize_session(s)
            current = prepare(self.cmd, self.raw, self.ctx, s, selector=self.selector, source=self.source)
            if current.info != self.resource.info or current.store != self.resource.store:
                raise BookflowError("E_IO", details={"check": "download_changed"})
        finally:
            try:
                _close(s)
            finally:
                self.host.reader_done()

    def close(self):
        if self.resource is not None:
            self.resource.lease.close()
