"""Private host handoff ordering. No production authority commit calls this yet.

Only known nonblocking socket transports may execute inside the mutex. Validation,
readiness, cancellation cleanup and all filesystem work belong outside it.
"""
from dataclasses import dataclass
import socket
from threading import Lock
from asyncio.selector_events import _SelectorSocketTransport
from asyncio import get_running_loop

FRAME_BYTES = 65536


class AdmissionCancelled(ConnectionAbortedError):
    """No further suffix may be released; earlier transport bytes are not recalled."""


@dataclass(frozen=True, eq=False, repr=False)
class ReadGeneration:
    owner: object
    epoch: object


@dataclass(eq=False, repr=False)
class Frame:
    generation: ReadGeneration
    kind: str
    accepted: int = 0
    terminal: bool = False


@dataclass(frozen=True, eq=False, repr=False)
class CommitBarrier:
    owner: object


class Admission:
    def __init__(self):
        self._mutex = Lock()
        self._epoch = object()
        self._barrier = None

    def begin_validation(self):
        with self._mutex:
            if self._barrier is not None:
                raise AdmissionCancelled('publication admission closed')
            return ReadGeneration(self, self._epoch)

    def _valid(self, generation):
        if (type(generation) is not ReadGeneration or generation.owner is not self
                or generation.epoch is not self._epoch or self._barrier is not None):
            raise AdmissionCancelled('publication admission changed')

    def admit(self, generation, kind):
        if type(kind) is not str or not kind:
            raise ValueError('frame kind required')
        with self._mutex:
            self._valid(generation)
            return Frame(generation, kind)

    def _frame(self, frame):
        if type(frame) is not Frame or frame.terminal:
            raise AdmissionCancelled('publication frame ended')
        self._valid(frame.generation)

    def transport_write(self, frame, transport, data):
        # Exact type: a caller-supplied write callback/subclass is not admissible.
        if type(transport) is not _SelectorSocketTransport:
            raise TypeError('requires owned asyncio selector socket transport')
        if get_running_loop() is not transport._loop:
            raise TypeError('transport handoff requires its event loop')
        self._bytes(data)
        with self._mutex:
            self._frame(frame)
            if transport._closing or transport._conn_lost or transport._eof or transport._empty_waiter is not None:
                frame.terminal = True
                raise AdmissionCancelled('transport closed')
            try:
                # This is asyncio's own outgoing buffer, not an application queue.
                # Its writer/flow-control callbacks are armed AFTER unlocking.
                if data:
                    transport._buffer.append(data)
            except BaseException:
                frame.terminal = True
                raise
            frame.accepted += len(data)
            return len(data)

    def socket_send(self, frame, sock, data):
        if type(sock) is not socket.socket or sock.gettimeout() != 0:
            raise TypeError('requires owned nonblocking socket')
        self._bytes(data)
        with self._mutex:
            self._frame(frame)
            try:
                count = sock.send(data)
            except BlockingIOError:
                return 0
            except BaseException:
                frame.terminal = True
                raise
            if count == 0:
                frame.terminal = True
                raise AdmissionCancelled('socket closed')
            frame.accepted += count
            return count

    @staticmethod
    def _bytes(data):
        if type(data) is not bytes or len(data) > FRAME_BYTES:
            raise ValueError('bounded immutable bytes required')

    def finish(self, frame):
        with self._mutex:
            self._frame(frame)
            frame.terminal = True
            return frame.accepted

    def close_for_commit(self):
        with self._mutex:
            if self._barrier is not None:
                raise RuntimeError('commit barrier already closed')
            self._barrier = CommitBarrier(self)
            self._epoch = object()  # O(1), no response/task enumeration or wait
            return self._barrier

    def finish_commit(self, barrier, *, committed):
        if type(committed) is not bool:
            raise TypeError('transaction outcome required')
        with self._mutex:
            if barrier is not self._barrier or type(barrier) is not CommitBarrier:
                raise ValueError('foreign or completed barrier')
            self._barrier = None
