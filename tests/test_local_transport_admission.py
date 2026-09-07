import socket
import pytest
from bookflow.core.publication_admission import Admission, AdmissionCancelled
from bookflow.adapters.http.local import AdmittedSocket
from bookflow.core.transfer_protocol import _IO
import time


def test_real_short_write_then_cancel_no_final_or_restored_suffix():
    gate=Admission();a,b=socket.socketpair()
    try:
        a.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,4096)
        wire=AdmittedSocket(a,gate,lambda:None)
        count=wire.send(b'a'*65536)
        assert 0 < count < 65536
        barrier=gate.close_for_commit();gate.finish_commit(barrier,committed=False)
        with pytest.raises(AdmissionCancelled):wire.send(b'final')
        assert b.recv(65536)==b'a'*count
        b.setblocking(False)
        with pytest.raises(BlockingIOError):b.recv(1)
    finally:a.close();b.close()


@pytest.mark.parametrize('stage',['before_dequeue','after_read','after_upload_staging'])
def test_fixed_operation_generation_cannot_publish_after_barrier(stage):
    gate=Admission();a,b=socket.socketpair()
    try:
        wire=AdmittedSocket(a,gate,lambda:None)
        barrier=gate.close_for_commit();gate.finish_commit(barrier,committed=True)
        with pytest.raises(AdmissionCancelled):wire.send(stage.encode())
        b.setblocking(False)
        with pytest.raises(BlockingIOError):b.recv(1)
    finally:a.close();b.close()


def test_ready_body_final_wire_success_and_partial_io_loop():
    gate=Admission();a,b=socket.socketpair()
    try:
        wire=AdmittedSocket(a,gate,lambda:None)
        io=_IO(10,10,lambda:None,time.monotonic)
        for data in (b'READY',b'body',b'FINAL'):io.write(wire,data)
        assert b.recv(14)==b'READYbodyFINAL'
    finally:a.close();b.close()
