"""Fresh-process binary execution with root ownership retained through cleanup."""

from __future__ import annotations

import threading
from pathlib import Path

from bookflow.core.config import Config, os_login
from bookflow.core.context import ActorKind
from bookflow.core.errors import BookflowError
from bookflow.core.fs import check_local
from bookflow.core.locks import RootLock
from bookflow.core.perms import private_umask
from bookflow.core.session import Session
from bookflow.core.transfer_resources import TransferLease
from bookflow.core.transfers import InputBody, TransferResource, copy_output, open_verified, prepare
from bookflow.storage.paths import resolve_data_root

# Failed cleanup retains its root lock until a later explicit retry succeeds.
_pending: dict[TransferLease, RootLock] = {}
_pending_lock = threading.Lock()


def retry_cleanup(root: Path):
    with _pending_lock:
        leases = [lease for lease, lock in _pending.items() if lock.path.parent == root and lease.cleanup_pending]
    for lease in leases:
        lease.close()


def run_transfer(cmd, raw, ctx, *, data_root=None, selector=None, source="option", dry_run=False,
                 login=None, input_stream=None, output_stream=None):
    from bookflow.core.dispatch import (_close, _load_actor, _migrate_hub, _open_hub, authorize,
                                        execute, guard, validate_context, validate_input)
    from bookflow.core.forward import try_forward_transfer
    direction = cmd.transfer.direction
    if (direction == "input" and (input_stream is None or output_stream is not None)
            or direction == "output" and (output_stream is None or input_stream is not None)):
        raise BookflowError("E_USAGE", message=f"This command requires one {direction} binary stream.")
    validate_input(cmd, raw)
    validate_context(ctx)
    if dry_run and not cmd.is_write:
        raise BookflowError("E_USAGE", message="Dry-run applies only to writes.")
    root = resolve_data_root(data_root)
    check_local(root)
    if not (root / "hub.db").is_file():
        raise BookflowError("E_NOT_INITIALIZED", details={"data_root": str(root)})
    forwarded = try_forward_transfer(root, cmd, raw, ctx, selector, source, dry_run,
                                     input_stream=input_stream, output_stream=output_stream)
    if forwarded is not None:
        return forwarded
    retry_cleanup(root)
    lock = RootLock(root, cmd.name)
    resource = None
    allowed = False
    effective_ctx = ctx

    def session(writable):
        nonlocal allowed, effective_ctx
        s = Session(root, login or os_login(), Config.load(root / "config.toml"), dry_run=dry_run)
        try:
            _open_hub(s, writable, ctx)
            _load_actor(s)
            allowed = s.is_hub_admin
            effective_ctx = ctx.model_copy(update={"actor_id": s.actor.id, "actor_kind": ActorKind(s.actor.kind)})
            if writable:
                _migrate_hub(s, effective_ctx)
            return s
        except BaseException:
            _close(s)
            raise

    def release(lease):
        lock.__exit__(None, None, None)
        with _pending_lock:
            _pending.pop(lease, None)

    def run_locked():
        nonlocal resource
        with private_umask():
            lock.__enter__()
            try:
                # Writable preparation migrates and converges old collection before
                # any I/O lease exists; the subsequent short read grants admission.
                if direction == "input" and not dry_run:
                    s = session(True)
                    try:
                        authorize(cmd, effective_ctx, s, company_selector=selector, company_source=source)
                        from bookflow.company.attachment_gc import recover_pending
                        recover_pending(s, effective_ctx)
                    finally:
                        _close(s)
                s = session(False)
                try:
                    prepared = prepare(cmd, raw, effective_ctx, s, selector=selector, source=source, dry_run=dry_run)
                    lease = TransferLease(s.actor.id, s.company_row["id"], release)
                    resource = TransferResource(lease, prepared.store, prepared.info)
                    with _pending_lock:
                        _pending[lease] = lock
                    if direction == "output":
                        s.transfer = resource
                        output = execute(cmd, raw, effective_ctx, s)
                finally:
                    _close(s)
                if direction == "input":
                    body = InputBody(resource, prepared.limit, dry_run)
                    body.receive(input_stream)
                    body.complete()
                    s = session(not dry_run)
                    try:
                        s.transfer = resource
                        return execute(cmd, raw, effective_ctx, s, company_selector=selector,
                                       company_source=source, dry_run=dry_run)
                    finally:
                        _close(s)
                reader = open_verified(resource.store, resource.info)
                lease.add_cleanup(reader.close)

                def check():
                    lease.check_io()
                    s = session(False)
                    try:
                        current = prepare(cmd, raw, effective_ctx, s, selector=selector, source=source)
                        if current.info != resource.info or current.store != resource.store:
                            raise BookflowError("E_IO", details={"check": "download_changed"})
                    finally:
                        _close(s)
                copy_output(reader, output_stream, resource.info, check)
                return output
            finally:
                if resource is not None:
                    resource.lease.close()
                else:
                    lock.__exit__(None, None, None)
    return guard(run_locked, lambda: allowed)
