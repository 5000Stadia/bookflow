"""The shared conformance suite every transport passes (design/document-delivery.md §4, criterion 7).

A transport is checked through the whole dispatcher, against a company: ``desk`` gives ``run`` (a command as the
company's owner), ``clock.advance(minutes)`` and ``ready()`` (a fresh message released with a person's go, its
number). The ``harness`` stands for the far side: ``entry`` (the registered ``TransportKind``), ``plan(*outcomes)``
arranging what the server answers next (accepted, temporary, permanent, unknown), ``deliveries_of(number)`` (how many
copies reached a recipient) and ``delivered`` (what arrived, each with ``number``, ``subject``, ``to``,
``rfc_message_id`` and ``attachments``). The fake's harness is its backend; a real adapter's wraps a local test server.

Cases: accepted; temporary then accepted; permanent; unknown; a replayed attempt key; attachments intact byte for byte.
"""
from __future__ import annotations

import base64
import hashlib

from bookflow.delivery import transport as t

CASES = ('accepted', 'temporary_then_accepted', 'permanent', 'unknown', 'replay', 'attachments_intact')


def _run(desk):
    return desk['run']('delivery run', {}, reason='Send what is due')


def _show(desk, number, **extra):
    return desk['run']('delivery show', {'message': number, **extra})


def _events(desk, number):
    return [e['event'] for e in desk['run']('delivery history', {'message': number})['events']]


def accepted(harness, desk):
    number = desk['ready']()
    harness.plan('accepted')
    out = _run(desk)
    assert out['sent'] == 1 and out['claimed'] == 1, out
    message = _show(desk, number)
    assert message['status'] == 'sent' and message['sent_via'] == 'connection' and message['provider_ref']
    assert message['delivery'] == 'accepted' and message['status_label'] == 'Sent' and message['attempts'] == 1
    assert _events(desk, number)[-2:] == ['claimed', 'accepted']
    assert harness.deliveries_of(number) == 1


def temporary_then_accepted(harness, desk):
    number = desk['ready']()
    harness.plan('temporary', 'accepted')
    out = _run(desk)
    assert out['retrying'] == 1, out
    waiting = _show(desk, number)
    assert waiting['status'] == 'ready' and waiting['next_attempt_at'] and waiting['failure_code'] == 'busy'
    assert _run(desk)['claimed'] == 0  # not due yet
    desk['clock'].advance(5)
    assert _run(desk)['sent'] == 1
    assert _show(desk, number)['attempts'] == 2 and harness.deliveries_of(number) == 1
    assert _events(desk, number)[-4:] == ['claimed', 'attempt_failed', 'claimed', 'accepted']


def permanent(harness, desk):
    number = desk['ready']()
    harness.plan('permanent')
    assert _run(desk)['failed'] == 1
    message = _show(desk, number)
    assert message['status'] == 'failed' and message['failure_code'] == 'rejected' and message['failure_text']
    desk['clock'].advance(180)
    assert _run(desk)['claimed'] == 0 and harness.deliveries_of(number) == 0


def unknown(harness, desk):
    number = desk['ready']()
    harness.plan('unknown')
    assert _run(desk)['held'] == 1
    message = _show(desk, number)
    assert message['status'] == 'held' and message['hold_reason'] == 'outcome_unknown'
    desk['clock'].advance(600)
    assert _run(desk)['claimed'] == 0  # never retried automatically
    assert harness.deliveries_of(number) <= 1 and _show(desk, number)['attempts'] == 1


def replay(harness, desk):
    conn = t.ConnectionInfo('C1', 'Contract', harness.entry.kind, dict(harness.settings), harness.secret_ref)
    sender = harness.entry.open(conn)
    pdf = b'%PDF-1.4 contract replay'
    message = t.OutgoingMessage('MSG-REPLAY', 'M-REPLAY', '<replay@bookflow.invalid>', 'Demo', None, None,
                                ('ap@example.test',), (), (), 'Replay', 'Body',
                                (t.Attachment('replay.pdf', 'application/pdf', pdf, hashlib.sha256(pdf).hexdigest()),))
    harness.plan('accepted')
    first = sender.send(message, 'MSG-REPLAY:1')
    again = sender.send(message, 'MSG-REPLAY:1')
    assert first.outcome == again.outcome == 'accepted' and first.provider_ref == again.provider_ref
    assert harness.deliveries_of('M-REPLAY') == 1


def attachments_intact(harness, desk):
    number = desk['ready']()
    harness.plan('accepted')
    assert _run(desk)['sent'] == 1
    shown = _show(desk, number, include_files=True)
    [arrived] = [m for m in harness.delivered if m.number == number]
    assert arrived.rfc_message_id == shown['message_id'] and list(arrived.to) == shown['to']
    assert arrived.subject == shown['subject']
    assert [(a.file_name, a.content) for a in arrived.attachments] == [
        (doc['file_name'], base64.b64decode(doc['content_base64'])) for doc in shown['documents']]
    assert all(hashlib.sha256(a.content).hexdigest() == a.sha256 for a in arrived.attachments)


def check(case, harness, desk):
    globals()[case](harness, desk)
