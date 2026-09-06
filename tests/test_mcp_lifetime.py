"""Host-owned MCP lifetime, independently of client calls or ASGI startup hooks."""

import threading

from bookflow.adapters.mcp.intents import OwnedIntents
from tests.test_mcp_intents import Clock, OWNER
from tests.test_row3_host import hosted


def test_idle_intent_is_reaped_without_polling_and_host_shutdown_closes_owner(hosted):
    clock = Clock()
    owner = OwnedIntents(hosted.handle.host, clock=clock)
    cleaned = threading.Event()
    intent = owner.admit(OWNER, cleanup=cleaned.set)
    owner.ready(intent, {"input": {}})
    clock.now = 31
    assert cleaned.wait(2), "abandoned intent required a client call to clean up"
    assert intent.reference not in owner.active
    hosted.handle.host.begin_shutdown()
    assert owner.closed and not owner._reaper.is_alive()
