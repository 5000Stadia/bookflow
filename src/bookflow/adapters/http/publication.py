"""Fence response publication, including delayed workbench receipts."""

from contextvars import ContextVar
from dataclasses import dataclass

from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError

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
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
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

        async def checked_send(message):
            nonlocal denied, started
            if denied:
                return
            if message["type"] in {"http.response.start", "http.response.body"}:
                try:
                    await run_in_threadpool(check)
                except BookflowError as exc:
                    if started:
                        # Headers/bytes cannot be recalled. Closing the response
                        # without a complete body is an interrupted result.
                        raise ConnectionAbortedError("Publication authority changed") from None
                    denied = True
                    document = (exc.to_dict() if getattr(exc, "publication_auth_only", False) else
                                BookflowError(exc.code, details={"stage": "publication", "outcome": "unknown"}).to_dict())
                    response = JSONResponse(document, status_code=401 if exc.code == "E_UNAUTHENTICATED" else 403,
                        headers={"Cache-Control": "no-store", **({"X-Bookflow-MCP-Version": "1"} if scope["path"].startswith("/adapters/mcp") else {})})
                    await response(scope, receive, send)
                    return
            await send(message)
            if message["type"] == "http.response.start":
                started = True

        try:
            await self.app(scope, receive, checked_send)
        finally:
            _guards.reset(token)
