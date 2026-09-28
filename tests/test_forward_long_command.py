"""R74: a forwarded command runs as long as it needs; only a silent host is given up on."""
import time

import pytest

from bookflow.adapters.http import execution
from bookflow.core import forward, transfer_protocol
from bookflow.core.context import Context, Interface
from bookflow.core.errors import BookflowError
from tests.test_row3_host import hosted  # noqa: F401 - fixture

SLOW = 2.0


@pytest.fixture
def slow_host(hosted, monkeypatch):  # noqa: F811
    """Every forwarded command takes SLOW seconds longer, and every wire bound is 1 s."""
    original = execution.run_hosted

    def slow(*args, **kwargs):
        time.sleep(SLOW)
        return original(*args, **kwargs)
    monkeypatch.setattr(execution, 'run_hosted', slow)
    wire = transfer_protocol._IO
    monkeypatch.setattr(transfer_protocol, '_IO',
                        lambda lifetime, idle, check, clock: wire(lifetime, min(idle, 1.0), check, clock))
    return hosted


def envelope(hosted):  # noqa: F811
    ctx = Context.new(Interface.cli, 'bookflow-cli').model_dump(mode='json')
    return {'command': 'company list', 'input': {}, 'context': ctx}


def test_a_command_longer_than_every_silence_bound_completes(slow_host, monkeypatch):
    monkeypatch.setattr(forward, 'HEARTBEAT_SECONDS', 0.2)
    started = time.monotonic()
    reply = forward.call_host(str(slow_host.handle.socket), envelope(slow_host), timeout=1.0)
    assert time.monotonic() - started >= SLOW
    # The reply is written with its own bounds, although the request's 1 s idle bound
    # expired while the command ran; the caller's 1 s silence bound was kept by heartbeats.
    assert reply['output']['items'][0]['company_id'] == slow_host.company_id


def test_a_host_that_goes_silent_is_still_given_up_on(slow_host, monkeypatch):
    monkeypatch.setattr(forward, 'HEARTBEAT_SECONDS', 30.0)
    with pytest.raises(BookflowError) as caught:
        forward.call_host(str(slow_host.handle.socket), envelope(slow_host), timeout=1.0)
    assert caught.value.code == 'E_IO' and caught.value.details['outcome'] == 'unknown'
    time.sleep(SLOW)  # let the host finish the command before the fixture stops it
