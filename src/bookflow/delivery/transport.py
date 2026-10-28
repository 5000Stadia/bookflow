"""The transport seam: what a sender implements, and the registry of kinds (design/document-delivery.md §4).

A **transport** hands one outgoing message to something that delivers mail -- a mail MCP server, SMTP with an app
password -- and says what happened. Bookflow never talks to a mail server itself: the dispatcher
(``company/delivery_dispatch.py``) claims a message, calls ``send`` with no database transaction open, and records the
``TransportResult``.

**This build registers no kind.** ``REGISTRY`` is empty in production, so ``delivery-connection add`` refuses every
kind and nothing can leave the machine. A real sender is a later item: it implements ``Transport``, registers its kind
with ``register``, and passes the shared conformance suite (``tests/delivery_transport_contract.py``). It needs no
schema or command change.

**Keys never live in a project or company file.** A connection row holds non-secret ``settings`` and a
``secret_ref`` naming where the secret is: ``env:NAME`` (an environment variable the host reads) or
``keyring:service/name`` (the operating system's keyring). ``resolve_secret`` reads it at the moment a transport is
opened, in memory only; nothing writes it back.
"""
from __future__ import annotations

import importlib
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Literal, Protocol, runtime_checkable

SECRET_REF = re.compile(r'^(env:[A-Za-z_][A-Za-z0-9_]{0,127}|keyring:[A-Za-z0-9_.@-]{1,100}/[A-Za-z0-9_.@-]{1,100})$')


@dataclass(frozen=True)
class Attachment:
    file_name: str
    media_type: str
    content: bytes
    sha256: str


@dataclass(frozen=True)
class OutgoingMessage:
    """One email exactly as it is to leave: the frozen attachments as bytes, a Message-ID fixed at draft."""
    message_id: str            # the outbox row's id
    number: str                # M-1001
    rfc_message_id: str        # <...@bookflow.invalid>, the same on every attempt
    from_name: str | None
    from_address: str | None
    reply_to: str | None
    to: tuple[str, ...]
    cc: tuple[str, ...]
    bcc: tuple[str, ...]
    subject: str
    body: str
    attachments: tuple[Attachment, ...]


@dataclass(frozen=True)
class ConnectionInfo:
    """What a transport is opened with: the connection's non-secret settings and where its secret is."""
    id: str
    name: str
    kind: str
    settings: dict[str, Any]
    secret_ref: str | None


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    text: str = ''


@dataclass(frozen=True)
class TransportResult:
    outcome: Literal['accepted', 'temporary', 'permanent', 'unknown']
    provider_ref: str | None = None
    actual_from: str | None = None
    code: str | None = None
    text: str = ''

    @classmethod
    def accepted(cls, provider_ref: str, actual_from: str | None = None) -> 'TransportResult':
        return cls('accepted', provider_ref=provider_ref, actual_from=actual_from)

    @classmethod
    def temporary(cls, code: str, text: str = '') -> 'TransportResult':
        return cls('temporary', code=code, text=text)

    @classmethod
    def permanent(cls, code: str, text: str = '') -> 'TransportResult':
        return cls('permanent', code=code, text=text)

    @classmethod
    def unknown(cls, text: str = '') -> 'TransportResult':
        return cls('unknown', text=text)


@dataclass(frozen=True)
class TransportEvent:
    provider_ref: str
    event: Literal['delivered', 'bounced']
    at: str | None = None
    text: str = ''


@runtime_checkable
class Transport(Protocol):
    kind: str

    def check(self, conn: ConnectionInfo) -> CheckResult: ...

    def send(self, message: OutgoingMessage, attempt_key: str) -> TransportResult: ...


@dataclass(frozen=True)
class TransportKind:
    """A registered kind: how its settings are validated and how a transport is opened for one connection.

    ``validate`` returns the settings to store (it raises ``ValueError`` naming the problem); ``open`` returns a
    ``Transport`` for the connection, reading its secret with ``resolve_secret`` if it needs one. A transport may also
    define ``events(conn, refs) -> list[TransportEvent]`` (delivered, bounced); the dispatcher polls it when present.
    """
    kind: str
    open: Callable[[ConnectionInfo], Transport]
    validate: Callable[[dict[str, Any]], dict[str, Any]] = lambda settings: dict(settings)
    description: str = ''


REGISTRY: dict[str, TransportKind] = {}


def register(entry: TransportKind) -> None:
    if not entry.kind or len(entry.kind) > 64:
        raise ValueError('a transport kind is 1 to 64 characters')
    REGISTRY[entry.kind] = entry


def unregister(kind: str) -> None:
    REGISTRY.pop(kind, None)


def kinds() -> list[str]:
    return sorted(REGISTRY)


def get(kind: str) -> TransportKind | None:
    return REGISTRY.get(kind)


@contextmanager
def registered(entry: TransportKind) -> Iterator[TransportKind]:
    """Register a kind for the length of a block (the tests' fake transport)."""
    previous = REGISTRY.get(entry.kind)
    register(entry)
    try:
        yield entry
    finally:
        if previous is None:
            unregister(entry.kind)
        else:
            REGISTRY[entry.kind] = previous


def resolve_secret(secret_ref: str | None) -> str | None:
    """The secret a ``secret_ref`` names, read now and held only by the caller. None when there is no ref."""
    if secret_ref is None:
        return None
    if not SECRET_REF.match(secret_ref):
        raise ValueError('secret_ref must be env:NAME or keyring:service/name')
    scheme, _, rest = secret_ref.partition(':')
    if scheme == 'env':
        return os.environ.get(rest)
    service, _, name = rest.partition('/')
    keyring = importlib.import_module('keyring')  # optional; only a real sender's host needs it
    return keyring.get_password(service, name)


@dataclass
class Outcome:
    """A claimed attempt and what its transport said (or an exception it raised, which is an unknown outcome)."""
    message_id: str
    attempt: int
    connection_id: str
    result: TransportResult
    events: list[TransportEvent] = field(default_factory=list)


def call_send(transport: Transport, message: OutgoingMessage, attempt_key: str) -> TransportResult:
    """``send``, with anything it raises turned into an unknown outcome: the bytes may have left."""
    try:
        result = transport.send(message, attempt_key)
    except Exception as error:  # noqa: BLE001 - a sender's failure is an outcome, never the dispatcher's crash
        return TransportResult.unknown(f'{type(error).__name__}: {error}'[:2000])
    if not isinstance(result, TransportResult):
        return TransportResult.unknown('the transport returned no result')
    return result
