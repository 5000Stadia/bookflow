"""Fence response publication, including delayed workbench receipts."""

from contextvars import ContextVar
from dataclasses import dataclass

from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError
from bookflow.adapters.mcp.catalog import BRIDGE_VERSION

_guards = ContextVar("bookflow_publication_guards", default=None)


@dataclass(repr=False)
class AuthenticationGuard:
    host: object
    credential: object

    def check(self, **_kwargs):
        from bookflow.core.publication import publication_reader
        with publication_reader(self.host, self.credential) as session:
            self.credential.revalidate(session.hub)


def protect_credentials(host, credential):
    pending = _guards.get()
    if pending is not None and not any(isinstance(item, AuthenticationGuard) and item.credential.token_id == credential.token_id for item, _ in pending):
        pending.append((AuthenticationGuard(host, credential), False))


def protect(document, *, original_response=True):
    pending = _guards.get()
    if pending is not None and hasattr(document, "check"):
        pending.append((document, original_response))


class PublicationMiddleware:
    def __init__(self, app, *, host=None):
        self.app = app
        self.host = host

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from .admission import bounded_messages, send_frame
        from bookflow.core.publication_admission import ResponseRelease, AdmissionCancelled
        gate = self.host.publication_admission if self.host is not None else None
        integrated = gate is not None and scope.get("bookflow.transport_admission", False)
        response_state = scope.get("bookflow.response_release") or ResponseRelease()
        disconnected = scope.get("bookflow.disconnected", lambda: False)
        def cancelled():
            return disconnected() or bool(getattr(self.host, "_stopping", False))
        guards = []
        token = _guards.set(guards)
        denied, started = False, False

        def check():
            covered = {document.credential.token_id for document, _ in guards if not isinstance(document, AuthenticationGuard)}
            for document, original in guards:
                if isinstance(document, AuthenticationGuard) and document.credential.token_id in covered:
                    continue  # a command permit also checks credentials, with its narrow own-effect receipt rule
                try:
                    document.check(original_response=original)
                except BookflowError as exc:
                    if isinstance(document, AuthenticationGuard):
                        exc.publication_auth_only = True
                    raise

        def resources():
            return {id(transfer): transfer for document, _ in guards
                    if (transfer := getattr(document, 'publication_transfer', None)) is not None}.values()

        async def before_wait():
            for transfer in resources():
                await run_in_threadpool(transfer.suspend_publication)

        async def after_wait():
            for transfer in resources():
                await run_in_threadpool(transfer.resume_publication)

        async def release(message, *, validate=True):
            async def validate_current():
                if validate:
                    await run_in_threadpool(check)
            for part in bounded_messages(message):
                if not integrated:
                    await validate_current()
                    await send(part)
                    continue
                for transfer in resources():
                    deadline = transfer.resource.lease._deadline
                    response_state.deadline = deadline if response_state.deadline is None else min(response_state.deadline, deadline)
                response_state.start()
                while True:
                    try:
                        generation = gate.begin_validation()
                        await validate_current()
                        break
                    except AdmissionCancelled:
                        response_state.retry()
                        await before_wait()
                        await gate.wait_open(response_state, cancelled)
                        await after_wait()
                await send_frame(send, part, gate, generation, response=response_state,
                                 validate=validate_current, cancelled=cancelled, before_wait=before_wait, after_wait=after_wait)

        async def checked_send(message):
            nonlocal denied, started
            if denied:
                return
            if message["type"] in {"http.response.start", "http.response.body"}:
                try:
                    await release(message)
                except BookflowError as exc:
                    if started or response_state.accepted or response_state.aborted:
                        # Headers/bytes cannot be recalled. Closing the response
                        # without a complete body is an interrupted result.
                        raise ConnectionAbortedError("Publication authority changed") from None
                    denied = True
                    document = (exc.to_dict() if getattr(exc, "publication_auth_only", False) else
                                BookflowError(exc.code, details={"stage": "publication", "outcome": "unknown"}).to_dict())
                    response = JSONResponse(document, status_code=401 if exc.code == "E_UNAUTHENTICATED" else 403,
                        headers={"Cache-Control": "no-store", **({"X-Bookflow-MCP-Version": str(BRIDGE_VERSION)} if scope["path"].startswith("/adapters/mcp") else {})})
                    async def safe_send(part):
                        await release(part, validate=False)
                    await response(scope, receive, safe_send)
                    return
            else:
                raise ValueError("unsupported response frame")
            if message["type"] == "http.response.start":
                started = True

        try:
            await self.app(scope, receive, checked_send)
        finally:
            _guards.reset(token)


def replace_guard(previous, document):
    """Keep only the current SSE batch certificate, including idle heartbeats."""
    pending = _guards.get()
    if pending is not None:
        pending[:] = [(item, original) for item, original in pending if item is not previous]
    protect(document)
