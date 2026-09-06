"""Private authenticated intent routes; business work stays in the shared host."""

import json
from dataclasses import dataclass

import anyio
from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core import registry
from bookflow.core.context import Interface
from bookflow.core.errors import BookflowError
from bookflow.core.ids import is_ulid
from bookflow.adapters.http.publication import protect

from .delivery import Delivery, binary_chunks
from .envelopes import validate
from .runtime import Runtime, owner, unknown


@dataclass(repr=False)
class IntentGuard:
    runtime: object
    intent: object
    credential: object
    permit: object

    def check(self, **_kwargs):
        if self.permit is not None:
            self.permit.check(self.runtime.host, self.credential)
        else:
            self.runtime._header_authority(self.intent, self.credential)


def protect_intent(rt, intent, credential):
    from bookflow.core.publication import PublicationPermit
    permit = PublicationPermit.from_retained(intent.publication) if intent.publication is not None else None
    protect(IntentGuard(rt, intent, credential, permit), original_response=False)


def command_context(request, credential, arguments, selection, make_context):
    if not isinstance(selection, dict) or set(selection) != {"value", "source"} or selection["source"] not in {"option", "env", "default", "none"} or (selection["value"] is not None and not isinstance(selection["value"], str)):
        raise BookflowError("E_VALIDATION", details={"reason": "malformed_selection"})
    session = request.headers.get("X-Bookflow-Session-Id", "")
    if not is_ulid(session):
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "session_id", "problem": "process ULID required"}]})
    ctx = make_context(request, credential, Interface.mcp)
    return ctx.model_copy(update={"session_id": session,
        "client_host": request.headers.get("X-Bookflow-Client-Host", ""),
        "reason": arguments.reason, "source_ref": arguments.source_ref,
        "directive_id": arguments.directive, "idempotency_key": arguments.idempotency_key})


async def input_object(request, runtime, intent):
    """One active parsed value; no retained serialized copy or total-byte cap."""
    import ijson
    from decimal import Decimal
    builder = ijson.ObjectBuilder()
    def events():
        while True:
            event, value = yield
            # Match the existing HTTP json.loads numeric types. Keep ijson's
            # arbitrary-sized integers; non-integral JSON numbers have the same
            # float representation as other adapters, never a new Decimal->str
            # coercion through a business model. Money's string/integer checks
            # remain core-owned.
            if event == "number" and isinstance(value, Decimal):
                value = float(value)
            builder.event(event, value)
    target = events()
    next(target)
    parser = ijson.basic_parse_coro(target, use_float=False)
    try:
        with anyio.fail_after(300):
            stream = request.stream().__aiter__()
            while True:
                with anyio.fail_after(30):
                    try:
                        chunk = await anext(stream)
                    except StopAsyncIteration:
                        break
                for start in range(0, len(chunk), 65536):
                    runtime.intents.progress(intent)
                    parser.send(chunk[start:start + 65536])
            parser.close()
        value = builder.value
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, UnicodeError, ijson.JSONError, AttributeError):
        raise BookflowError("E_VALIDATION", details={"reason": "invalid_input_object"}) from None


def mount_transport(app, host, authenticate, make_context, response):
    def runtime():
        return Runtime.for_host(host)

    async def authenticated(request, reference=None):
        credential = await run_in_threadpool(authenticate, request)
        rt = runtime()
        intent = None if reference is None else await run_in_threadpool(rt.lookup, reference, credential)
        return rt, credential, intent

    def status(rt, intent):
        return {"operation_ref": intent.reference, "state": intent.state,
            "reason": intent.reason, "receipt_available": intent.receipt is not None,
            "inspection_available": intent.completed is not None and intent.publication is not None,
            "limits": {"active_host": 8, "active_principal": 2, "preexecution_idle_seconds": 30,
                "preexecution_absolute_seconds": 300, "prepared_host_bytes": 64 * 1024**2,
                "prepared_principal_bytes": 8 * 1024**2, "receipt_bytes": 1024**2,
                "completed_host": 128, "completed_principal": 16,
                "completed_host_bytes": 32 * 1024**2, "completed_principal_bytes": 4 * 1024**2,
                "completed_idle_seconds": 60, "completed_absolute_seconds": 300,
                "json_delivery_seconds": rt.json_seconds},
            "outcome": "not_submitted" if intent.reason in {"expired_before_submission", "released_before_submission", "rejected_before_submission"} else "unknown"}

    @app.post("/adapters/mcp/intents/new", include_in_schema=False)
    async def admit(request: Request):
        rt, credential, _ = await authenticated(request)
        envelope = await request.json()
        if not isinstance(envelope, dict) or set(envelope) != {"arguments", "company_selection"}:
            raise BookflowError("E_VALIDATION", details={"reason": "malformed_admission"})
        arguments = envelope["arguments"]
        if not isinstance(arguments, dict) or set(arguments) - {"command", "company", "reason", "source_ref", "directive", "idempotency_key", "dry_run"}:
            raise BookflowError("E_USAGE", details={"reason": "admission_context_only"})
        args = validate("bookflow_run", {**arguments, "input": {}})
        cmd = registry.get(args.command)
        if cmd is None:
            raise BookflowError("E_USAGE", details={"reason": "unknown_command"})
        selection = envelope["company_selection"]
        ctx = command_context(request, credential, args, selection, make_context)
        intent = await run_in_threadpool(rt.admit, cmd, ctx, credential,
            selection["value"], selection["source"], args.dry_run)
        protect_intent(rt, intent, credential)
        return response(status(rt, intent))

    @app.post("/adapters/mcp/intents/{reference}/prepare", include_in_schema=False)
    async def prepare(reference: str, request: Request):
        rt, credential, intent = await authenticated(request, reference)
        try:
            with rt.intents.preparation_worker(intent):
                raw = await input_object(request, rt, intent)
                if registry.get(intent.header["command"]).transfer:
                    await run_in_threadpool(rt.prepare_transfer, intent, raw, credential)
                else:
                    await run_in_threadpool(rt.prepare_json, intent, raw, credential)
            await run_in_threadpool(rt.lookup, reference, credential)
            protect_intent(rt, intent, credential)
            return response(status(rt, intent))
        except BaseException:
            rt.intents.release(reference, owner(credential))
            raise

    @app.post("/adapters/mcp/intents/{reference}/input", include_in_schema=False)
    async def receive_input(reference: str, request: Request):
        rt, credential, intent = await authenticated(request, reference)
        with rt.intents.lock:
            if getattr(intent, "upload_claimed", False):
                raise BookflowError("E_USAGE", details={"reason": "input_already_received"})
            intent.upload_claimed = True
        try:
            with rt.intents.preparation_worker(intent):
                if intent.state != "receiving" or not getattr(intent, "transfer", None):
                    raise BookflowError("E_USAGE", details={"reason": "intent_not_receiving"})
                transfer = intent.transfer
                with anyio.fail_after(300):
                    stream = request.stream().__aiter__()
                    while True:
                        with anyio.fail_after(30):
                            try:
                                chunk = await anext(stream)
                            except StopAsyncIteration:
                                break
                        for start in range(0, len(chunk), 65536):
                            await run_in_threadpool(rt.lookup, reference, credential)
                            rt.intents.progress(intent)
                            await run_in_threadpool(transfer.body.write, chunk[start:start + 65536])
                # The launcher closes/verifies its source before the separate seal.
                protect_intent(rt, intent, credential)
                return response({"sha256": transfer.body.digest.hexdigest(), "size_bytes": transfer.body.size})
        except BaseException:
            rt.intents.release(reference, owner(credential))
            raise

    @app.post("/adapters/mcp/intents/{reference}/seal", include_in_schema=False)
    async def seal(reference: str, request: Request):
        rt, credential, intent = await authenticated(request, reference)
        try:
            value = await request.json()
            if not isinstance(value, dict) or set(value) != {"sha256", "size_bytes"} or not isinstance(value["sha256"], str) or type(value["size_bytes"]) is not int:
                raise BookflowError("E_VALIDATION", details={"reason": "malformed_seal"})
            await run_in_threadpool(rt.seal_transfer, intent, credential, value["sha256"], value["size_bytes"])
            protect_intent(rt, intent, credential)
            return response(status(rt, intent))
        except BaseException:
            rt.intents.release(reference, owner(credential))
            raise

    async def execute(rt, credential, intent, raw=None):
        try:
            if raw is not None:
                payload = await run_in_threadpool(rt.prepare_json, intent, raw, credential, retain=False)
                document = await run_in_threadpool(rt.execute_json, intent, credential, direct=payload)
            elif getattr(intent, "transfer", None):
                document = await run_in_threadpool(rt.execute_transfer, intent, credential)
            else:
                document = await run_in_threadpool(rt.execute_json, intent, credential)
        except BookflowError as exc:
            document = getattr(exc, "publication_document", None)
            if document is None:
                await run_in_threadpool(rt.intents.finish, intent, reason="execution_result_unavailable")
                raise
        if document is None:
            # Recovery never calls core again. A complete cached receipt is guarded
            # anew on this request; binary bytes require a deliberately new read.
            if intent.receipt is not None:
                from bookflow.adapters.http.execution import PublishedDocument
                from bookflow.core.publication import PublicationPermit
                document = PublishedDocument(json.loads(intent.receipt), PublicationPermit.from_retained(intent.publication), host, credential)
                protect(document, original_response=False)
                if not rt.intents.resume_delivery(intent):
                    return response(status(rt, intent))
                try:
                    transfer = (await run_in_threadpool(rt.reopen_output, intent, credential)
                                if document.permit.cmd.transfer and document.permit.cmd.transfer.direction == "output" else None)
                    return Delivery(rt, intent, document, binary=binary_chunks(transfer) if transfer else None, recovery=True)
                except BaseException:
                    await run_in_threadpool(rt.intents.finish, intent, receipt=intent.receipt, publication=intent.publication)
                    raise
            protect_intent(rt, intent, credential)
            return response(status(rt, intent))
        protect(document)
        transfer = getattr(intent, "transfer", None)
        binary = binary_chunks(transfer) if transfer and transfer.cmd.transfer.direction == "output" else None
        return Delivery(rt, intent, document, binary=binary)

    @app.post("/adapters/mcp/intents/{reference}/run", include_in_schema=False)
    async def run(reference: str, request: Request):
        rt, credential, intent = await authenticated(request, reference)
        try:
            with rt.intents.preparation_worker(intent):
                raw = await input_object(request, rt, intent)
            return await execute(rt, credential, intent, raw)
        except BaseException:
            rt.intents.release(reference, owner(credential))
            raise

    @app.post("/adapters/mcp/intents/{reference}/{action}", include_in_schema=False)
    async def recover(reference: str, action: str, request: Request):
        rt, credential, intent = await authenticated(request, reference)
        if action == "execute":
            return await execute(rt, credential, intent)
        if action == "status":
            protect_intent(rt, intent, credential)
            return response(status(rt, intent))
        if action == "release":
            protect_intent(rt, intent, credential)
            rt.intents.release(reference, owner(credential))
            return response({"operation_ref": reference, "released": True})
        raise BookflowError("E_USAGE", details={"reason": "unknown_recovery_action"})
