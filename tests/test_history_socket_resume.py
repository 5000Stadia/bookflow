"""Actual HTTP disconnect/reconnect preserves the last processed event bookmark."""
import json
import time
from types import SimpleNamespace
from urllib.request import Request, urlopen

import pytest

from bookflow.adapters.http.app import create_app
from tests.test_history_public_stream_resume import feed
from tests.test_row3_host import live as live_server
from tests.test_history_stream import fields


@pytest.fixture
def socket_site(feed):
    host,*_=feed
    yield from live_server.__wrapped__(SimpleNamespace(handle=SimpleNamespace(app=create_app(host,secure_cookies=False))))


def first_frame(site,bookmark):
    request=Request(site+'/hub-events?limit=2',headers={'Authorization':'Bearer secret-A','Last-Event-ID':bookmark})
    with urlopen(request,timeout=30) as response:
        assert response.status==200
        assert response.headers.get_content_type()=='text/event-stream'
        lines=[]
        while True:
            line=response.readline().decode()
            assert line, 'Stream ended before an event'
            if line.strip():lines.append(line)
            elif lines:
                frame=fields(''.join(lines))
                if frame.get('event')=='audit':return frame
                lines=[]


def drained(host):
    deadline=time.monotonic()+10
    while host._readers_attached and time.monotonic()<deadline:time.sleep(.02)
    assert host._readers_attached==0


@pytest.mark.timeout(120)
def test_real_socket_reconnect_after_first_processed_event(feed,socket_site):
    host,_,start,first,second=feed
    partial=first_frame(socket_site,start)
    assert json.loads(partial['data'])['id']==first
    assert partial['id']!=start and not partial['id'].isdecimal()
    drained(host)
    following=first_frame(socket_site,partial['id'])
    assert json.loads(following['data'])['id']==second
    assert following['id']!=partial['id']
    drained(host)
