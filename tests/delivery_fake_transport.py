"""A fake transport for document delivery's tests only (R112 increment 2). Nothing here touches a network.

``FakeBackend`` stands for a mail server: the test scripts what it answers next (``plan``) and reads what reached it
(``received`` per attempt key, ``delivered`` -- what a customer would actually get). An ``unknown`` or ``raise``
outcome still delivers, as a timeout after the bytes left would. A replayed attempt key is answered as before and
delivers nothing more, as a server that honours the key would. ``registered(backend)`` puts the kind ``fake`` in the
registry for the length of a block; production registers nothing.
"""
from __future__ import annotations

from contextlib import contextmanager

from bookflow.delivery import transport as t

KIND = 'fake'
SETTINGS = {'from_address': 'office@fake-mail.example', 'from_name': 'Demo Plumbing'}


class FakeBackend:
    def __init__(self):
        self.script: list[str] = []
        self.received: dict[str, t.OutgoingMessage] = {}
        self.answers: dict[str, t.TransportResult] = {}
        self.delivered: list[t.OutgoingMessage] = []
        self.events: list[t.TransportEvent] = []
        self.opened = 0
        self.checks = 0
        self.settings, self.secret_ref = dict(SETTINGS), 'env:FAKE_MAIL_TOKEN'

    @property
    def entry(self) -> t.TransportKind:
        return entry(self)

    def plan(self, *outcomes: str) -> None:
        """What the next sends answer, in order: accepted, temporary, permanent, unknown or raise."""
        self.script.extend(outcomes)

    def report(self, provider_ref: str, event: str, text: str = '') -> None:
        self.events.append(t.TransportEvent(provider_ref, event, None, text))

    def deliveries_of(self, number: str) -> int:
        return sum(1 for m in self.delivered if m.number == number)


def _validate(settings):
    unknown = set(settings) - {'from_address', 'from_name'}
    if unknown:
        raise ValueError(f"unknown settings for the fake transport: {', '.join(sorted(unknown))} (a secret belongs "
                         'in secret_ref, never in settings)')
    return dict(settings)


class FakeTransport:
    kind = KIND

    def __init__(self, backend: FakeBackend, conn: t.ConnectionInfo):
        self.backend, self.conn = backend, conn
        backend.opened += 1

    def check(self, conn):
        self.backend.checks += 1
        return t.CheckResult(True, 'the fake server answered')

    def send(self, message, attempt_key):
        backend = self.backend
        if attempt_key in backend.answers:
            return backend.answers[attempt_key]  # a replay: the same answer, nothing delivered twice
        outcome = backend.script.pop(0) if backend.script else 'accepted'
        backend.received[attempt_key] = message
        if outcome in ('accepted', 'unknown', 'raise'):
            backend.delivered.append(message)
        if outcome == 'raise':
            raise TimeoutError('the fake server stopped answering after the message was sent')
        result = {
            'accepted': lambda: t.TransportResult.accepted(f'fake-{len(backend.received)}',
                                                          self.conn.settings.get('from_address')),
            'temporary': lambda: t.TransportResult.temporary('busy', 'try again later'),
            'permanent': lambda: t.TransportResult.permanent('rejected', 'no such mailbox'),
            'unknown': lambda: t.TransportResult.unknown('the connection dropped before the server answered'),
        }[outcome]()
        backend.answers[attempt_key] = result
        return result

    def events(self, conn, refs):
        return [e for e in self.backend.events if e.provider_ref in refs]


def entry(backend: FakeBackend) -> t.TransportKind:
    return t.TransportKind(KIND, open=lambda conn: FakeTransport(backend, conn), validate=_validate,
                           description='Tests only.')


@contextmanager
def registered(backend: FakeBackend):
    with t.registered(entry(backend)):
        yield backend
