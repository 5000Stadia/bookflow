"""Independent ordering outcomes on owned sockets, with no database fixtures."""
import socket
import threading
import pytest
from bookflow.core.publication_admission import Admission, AdmissionCancelled


def test_post_read_before_dequeue_and_rollback_never_resurrect():
    gate = Admission()
    read = gate.begin_validation()
    barrier = gate.close_for_commit()
    with pytest.raises(AdmissionCancelled): gate.begin_validation()
    with pytest.raises(AdmissionCancelled): gate.admit(read, 'post read')
    gate.finish_commit(barrier, committed=False)
    with pytest.raises(AdmissionCancelled): gate.admit(read, 'queued')
    fresh = gate.admit(gate.begin_validation(), 'fresh')
    assert gate.finish(fresh) == 0
    with pytest.raises(ValueError): gate.finish_commit(barrier, committed=True)


def test_partial_release_then_commit_cancels_suffix_and_terminal():
    gate = Admission()
    a, b = socket.socketpair()
    try:
        a.setblocking(False)
        frame = gate.admit(gate.begin_validation(), 'body')
        assert gate.socket_send(frame, a, b'first') == 5
        assert b.recv(5) == b'first'
        barrier = gate.close_for_commit()
        for data in [b'second', b'final']:
            with pytest.raises(AdmissionCancelled): gate.socket_send(frame, a, data)
        gate.finish_commit(barrier, committed=True)
        with pytest.raises(AdmissionCancelled): gate.finish(frame)
        b.setblocking(False)
        with pytest.raises(BlockingIOError): b.recv(1)
    finally: a.close(); b.close()


def test_writer_ack_does_not_wait_for_pending_frames_or_cleanup():
    gate = Admission()
    frames = [gate.admit(gate.begin_validation(), 'pending') for _ in range(1000)]
    done = threading.Event()
    def writer():
        barrier = gate.close_for_commit()
        gate.finish_commit(barrier, committed=True)
        done.set()
    thread = threading.Thread(target=writer);thread.start();thread.join(1)
    assert done.is_set()
    for frame in frames:
        with pytest.raises(AdmissionCancelled): gate.finish(frame)


def test_reject_blocking_or_user_supplied_sink_and_foreign_ticket():
    gate = Admission(); frame = gate.admit(gate.begin_validation(), 'body')
    a, b = socket.socketpair()
    try:
        with pytest.raises(TypeError): gate.socket_send(frame, a, b'x')
        with pytest.raises(TypeError): gate.transport_write(frame, object(), b'x')
        with pytest.raises(AdmissionCancelled): Admission().finish(frame)
    finally: a.close(); b.close()
