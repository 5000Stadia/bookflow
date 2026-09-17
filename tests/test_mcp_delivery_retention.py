"""Pre-send receipt accounting and constructor failure release real intent slots."""
from types import SimpleNamespace

import pytest
from starlette.responses import StreamingResponse

from bookflow.adapters.mcp.delivery import Delivery
from bookflow.adapters.mcp.framing import json_chunks
from bookflow.adapters.mcp.intents import Intents, MIB


class Document(dict):
    permit = SimpleNamespace(retained=lambda: {'certificate':'final'})


@pytest.mark.parametrize('extra', [0, 1])
def test_pre_send_receipt_cap_keeps_only_complete_json(extra):
    store = Intents()
    intent = store.admit(('token','actor','principal'))
    store.ready(intent, {'input':{}})
    store.queue(intent)
    store.start(intent)
    overhead = len(b''.join(json_chunks({'value':''})))
    document = Document(value='x' * (MIB - overhead + extra))
    delivery = Delivery(SimpleNamespace(intents=store, json_seconds=60), intent, document)
    assert delivery.recovery['receipt_available'] is (extra == 0)
    store.finish(intent)
    if extra:
        assert intent.receipt is None
    else:
        assert len(intent.receipt) == MIB
        assert intent.receipt == b''.join(json_chunks(document))
    assert intent.publication == {'certificate':'final'}
    assert not store.active and not store.reservations


def test_constructor_failure_after_reservation_releases_owned_capacity(monkeypatch):
    store = Intents()
    cleaned = []
    intent = store.admit(('token','actor','principal'), cleanup=lambda: cleaned.append(True))
    store.ready(intent, {'input':{}})
    store.queue(intent)
    store.start(intent)
    def failed(*args, **kwargs):
        raise RuntimeError('constructor failed')
    monkeypatch.setattr(StreamingResponse, '__init__', failed)
    with pytest.raises(RuntimeError, match='constructor failed'):
        Delivery(SimpleNamespace(intents=store, json_seconds=60), intent, Document(value='original'))
    assert cleaned == [True]
    assert not store.active and not store.reservations
    assert intent.receipt == b'{"value":"original"}'
    assert not intent.publication_pending
