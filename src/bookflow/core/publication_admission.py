"""Host handoff ordering, shared with actual-owner conservative commit barriers.

Only known nonblocking socket transports may execute inside the mutex. Validation,
readiness, cancellation cleanup and all filesystem work belong outside it.
"""
from dataclasses import dataclass
import asyncio
import time
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
class ResponseRelease:
    """Private whole-response evidence; HTTP interim100 is a separate class."""
    accepted: int = 0
    interim_accepted: int = 0
    cutoff: float | None = None
    deadline: float | None = None
    aborted: bool = False

    def start(self):
        if self.cutoff is None:
            self.cutoff = time.monotonic() + 30.0
        if self.deadline is not None:
            self.cutoff = min(self.cutoff, self.deadline)

    def retry(self):
        self.start()
        if self.accepted or self.aborted or time.monotonic() >= self.cutoff:
            raise AdmissionCancelled('response cannot wait for publication')


@dataclass(eq=False, repr=False)
class Frame:
    generation: ReadGeneration
    kind: str
    accepted: int = 0
    terminal: bool = False
    response: ResponseRelease | None = None


@dataclass(frozen=True, eq=False, repr=False)
class CommitBarrier:
    owner: object


class Admission:
    def __init__(self):
        self._mutex = Lock()
        self._epoch = object()
        self._barrier = None
        self._wait_loop = None
        self._reopened = None

    def begin_validation(self):
        with self._mutex:
            if self._barrier is not None:
                raise AdmissionCancelled('publication admission closed')
            return ReadGeneration(self, self._epoch)

    def check_generation(self, generation):
        """Validate a reader snapshot against the existing admission generation."""
        with self._mutex:
            self._valid(generation)

    def _valid(self, generation):
        if (type(generation) is not ReadGeneration or generation.owner is not self
                or generation.epoch is not self._epoch or self._barrier is not None):
            raise AdmissionCancelled('publication admission changed')

    def admit(self, generation, kind, response=None):
        if type(kind) is not str or not kind:
            raise ValueError('frame kind required')
        with self._mutex:
            self._valid(generation)
            if response is not None and (type(response) is not ResponseRelease or response.aborted):
                raise AdmissionCancelled("response ended")
            return Frame(generation, kind, response=response)

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
            if frame.response is not None:
                frame.response.accepted += len(data)
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
            if frame.response is not None:
                frame.response.accepted += count
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
        """Reopen after the owner has settled its watched transactions.

        ``committed`` is the owner's outcome classification (True also covers
        durable-partial), retained as an explicit boolean API requirement. It
        does not select admission behavior: close_for_commit already invalidated
        every old generation, unconditionally, including on rollback. Finishing
        never restores that generation and supplies no authority decision.
        """
        if type(committed) is not bool:
            raise TypeError('transaction outcome required')
        with self._mutex:
            if barrier is not self._barrier or type(barrier) is not CommitBarrier:
                raise ValueError('foreign or completed barrier')
            self._barrier = None
            loop, event = self._wait_loop, self._reopened
        # Constant writer work. Event.set wakes tasks on their loop, outside the
        # mutex and without the writer enumerating or waiting for responses.
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                # Shutdown can close the loop after the check. A dead transport
                # has no waiter to notify; never change a durable commit outcome.
                if not loop.is_closed():
                    raise

    async def wait_open(self, response, cancelled):
        """No reader, writer resource or worker-pool slot is held by this wait."""
        loop = asyncio.get_running_loop()
        while True:
            response.retry()
            if cancelled():
                raise AdmissionCancelled('response disconnected or host stopping')
            with self._mutex:
                if self._barrier is None:
                    return
                if self._wait_loop is not loop:
                    if self._wait_loop is not None and not self._wait_loop.is_closed():
                        raise RuntimeError('admission belongs to another transport loop')
                    self._wait_loop, self._reopened = loop, asyncio.Event()
                event = self._reopened
                event.clear()  # register and check while finish_commit is excluded
            try:
                await asyncio.wait_for(event.wait(), min(.05, response.cutoff - time.monotonic()))
            except TimeoutError:
                pass
