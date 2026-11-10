"""Wider transport admission before origin rewriting or credential resolution."""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import time
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlsplit

from starlette.responses import JSONResponse, Response
from bookflow.core import public_origin as po

SESSION = "bookflow_session"
PRELOGIN = "__Host-bookflow_prelogin"
CSRF = "x-bookflow-csrf"
SAFE = frozenset(("GET", "HEAD", "OPTIONS"))
SINGLE = frozenset((b"host", b"origin", b"referer", b"authorization", b"x-bookflow-csrf", b"x-bookflow-workbench"))


class TransportSecurity:
    def __init__(self, app, *, deployment):
        deployment.require_transport()
        self.app, self.deployment = app, deployment
        self.key = secrets.token_bytes(32)
        self.authority = urlsplit(deployment.public_origin).netloc

    def mac(self, purpose: str, value: str) -> str:
        return hmac.new(self.key, (purpose + "\0" + value).encode(), hashlib.sha256).hexdigest()

    def prelogin_valid(self, value: str) -> bool:
        try:
            stamp, random, signature = value.split(".")
            return (0 <= time.time() - int(stamp) < 7200 and bool(re.fullmatch(r"[0-9a-f]{64}", random))
                    and hmac.compare_digest(signature, self.mac("prelogin", stamp + "." + random)))
        except (ValueError, TypeError):
            return False

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        headers = scope.get("headers", [])
        values = {}
        invalid = False
        for key, val in headers:
            key = key.lower()
            if key in SINGLE and key in values:
                invalid = True
            values[key] = val.decode("latin-1")
        # Do not parse a forwarded chain or accept forwarded authentication.
        peer = scope.get("client")
        if (invalid or not peer or peer[0] != self.deployment.trusted_proxy
                or values.get(b"host") != self.authority):
            await self.refuse(scope, receive, send, "E_ORIGIN_REFUSED")
            return
        cookies = {}
        try:
            for key, val in headers:
                if key.lower() != b"cookie":
                    continue
                for part in val.decode("latin-1").split(";"):
                    name, sep, raw = part.strip().partition("=")
                    if not sep or not name or name in cookies:
                        raise ValueError
                    parsed = SimpleCookie()
                    parsed.load(part.strip())
                    if name not in parsed:
                        raise ValueError
                    cookies[name] = parsed[name].value
        except (ValueError, CookieError):
            # CookieError and malformed cookie syntax all have the same refusal.
            await self.refuse(scope, receive, send, "E_UNAUTHENTICATED")
            return
        authorization = values.get(b"authorization")
        bearer = bool(authorization and re.fullmatch(r"Bearer [A-Za-z0-9._~-]+", authorization, re.I))
        if authorization is not None and (not bearer or SESSION in cookies or PRELOGIN in cookies):
            await self.refuse(scope, receive, send, "E_UNAUTHENTICATED")
            return
        unsafe = scope["method"] not in SAFE
        # Supplied browser source must always be classifiable, even for bearer GETs.
        origin = values.get(b"origin")
        referer = values.get(b"referer")
        try:
            source = origin if origin is not None else po.origin_of(referer)
        except ValueError:
            source = None
        if (origin is not None or referer is not None or (unsafe and not bearer)) and source != self.deployment.public_origin:
            await self.refuse(scope, receive, send, "E_ORIGIN_REFUSED")
            return
        login = scope["path"] == "/login" or scope["path"].startswith("/setup/")
        prelogin = cookies.get(PRELOGIN, "")
        valid_prelogin = self.prelogin_valid(prelogin)
        new_prelogin = False
        if login and bearer:
            await self.refuse(scope, receive, send, "E_UNAUTHENTICATED")
            return
        if not valid_prelogin and not unsafe and not bearer and (login or SESSION not in cookies):
            payload = str(int(time.time())) + "." + secrets.token_hex(32)
            prelogin = payload + "." + self.mac("prelogin", payload)
            new_prelogin = True
        binding = "prelogin:" + prelogin if login or SESSION not in cookies else "session:" + cookies[SESSION]
        token = self.mac("csrf", binding)
        if unsafe and not bearer:
            if (values.get(b"x-bookflow-workbench") != "1" or
                    (login and not valid_prelogin) or
                    (not login and SESSION not in cookies) or
                    (not re.fullmatch(r"[0-9a-f]{64}", values.get(b"x-bookflow-csrf", "")) or
                     not hmac.compare_digest(values.get(b"x-bookflow-csrf", ""), token))):
                await self.refuse(scope, receive, send, "E_PERMISSION")
                return
        # Stable for a session so trusted script fragments work under the original
        # document's CSP. Separate domain from CSRF and never expose the cookie secret.
        nonce = self.mac("csp", binding)
        state = scope.setdefault("state", {})
        state.update(csrf_token=token, csp_nonce=nonce, wider=True)
        scope = dict(scope, headers=[(k, v) for k, v in headers if
                     not k.lower().startswith((b"x-forwarded-", b"tailscale-", b"remote-user", b"x-remote-"))
                     and k.lower() != b"forwarded"])

        started = False

        async def secured_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                extra = self.response_headers(nonce)
                existing = [(k, v) for k, v in message.get("headers", []) if k.lower() not in dict(extra)]
                # Downloads never get an executable same-origin MIME type.
                if any(k.lower() == b"content-disposition" and v.lower().startswith(b"attachment") for k, v in existing):
                    existing = [(k, v) for k, v in existing if k.lower() != b"content-type"]
                    extra.append((b"content-type", b"application/octet-stream"))
                holder = Response()
                if new_prelogin:
                    holder.set_cookie(PRELOGIN, prelogin, secure=True, httponly=True, samesite="strict", path="/", max_age=7200)
                if login and unsafe and message["status"] < 300:
                    holder.delete_cookie(PRELOGIN, secure=True, httponly=True, samesite="strict", path="/")
                extra.extend((k, v) for k, v in holder.raw_headers if k == b"set-cookie")
                message = dict(message, headers=existing + extra)
            await send(message)
        try:
            await self.app(scope, receive, secured_send)
        except Exception:
            # Do not hand a SQL/body-bearing traceback to the server logger.
            logging.getLogger("bookflow.http").error("Wider request failed (E_INTERNAL)")
            if started:
                raise ConnectionAbortedError("Response abandoned") from None
            response = JSONResponse({"code": "E_INTERNAL", "message": "Internal failure.", "details": {}}, status_code=500)
            await response(scope, receive, secured_send)

    @staticmethod
    def response_headers(nonce=""):
        return [(b"cache-control", b"private, no-store"), (b"referrer-policy", b"no-referrer"),
                (b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"),
                (b"content-security-policy", (
                    "default-src 'self'; script-src 'self' 'wasm-unsafe-eval' 'nonce-" + nonce + "'; "
                    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; "
                    "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
                ).encode())]

    async def refuse(self, scope, receive, send, code):
        response = JSONResponse({"code": code, "message": "Request refused by transport policy.", "details": {}}, status_code=403)
        response.raw_headers.extend(self.response_headers())
        await response(scope, receive, send)


class SecurityHeaders:
    """Also cover publication refusals, without buffering or changing release admission."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def secured_send(message):
            if message['type'] == 'http.response.start':
                extra = TransportSecurity.response_headers(scope.get('state', {}).get('csp_nonce', ''))
                headers = [(k, v) for k, v in message.get('headers', []) if k.lower() not in dict(extra)]
                message = dict(message, headers=headers + extra)
            await send(message)
        await self.app(scope, receive, secured_send)
