"""Thread-safe ownership and retryable cleanup for a bounded transfer."""

from __future__ import annotations

from collections.abc import Callable
import math
import threading
import time

from bookflow.core.errors import BookflowError


def _io(check: str) -> BookflowError:
    return BookflowError("E_IO", "Transfer resource check failed.", {"check": check})


class TransferLease:
    """A caller's resources, optionally handed to a writer until completion.

    Failed cleanup retains the owner state and capacity for explicit retry.
    Release receives this lease; resource cleanup callbacks receive no arguments.
    Callbacks must be retry-safe if they raise after performing a side effect.
    No callback runs under the state lock. Callers must not hold host admission
    locks while invoking cleanup, which can call back into host admission.
    """

    def __init__(self, principal_id: str, company_id: str,
                 release: Callable[[TransferLease], object], lifetime_seconds: float = 300,
                 clock: Callable[[], float] = time.monotonic):
        if (type(lifetime_seconds) not in (int, float)
                or not 0 < lifetime_seconds <= 300
                or not math.isfinite(lifetime_seconds)):
            raise BookflowError("E_VALIDATION", "Transfer lifetime must be positive and at most 300 seconds.")
        if not callable(release) or not callable(clock):
            raise BookflowError("E_VALIDATION", "Transfer callbacks must be callable.")
        self._principal_id = principal_id
        self._company_id = company_id
        self._release = release
        self._clock = clock
        self._deadline = clock() + lifetime_seconds
        self._condition = threading.Condition()
        self._state = "caller"
        self._cancelled = False
        self._completed = False
        self._cleanup_pending = False
        self._cleaning_thread: int | None = None
        self._callbacks: list[Callable[[], object]] = []

    @property
    def principal_id(self) -> str:
        return self._principal_id

    @property
    def company_id(self) -> str:
        return self._company_id

    @property
    def state(self) -> str:
        with self._condition:
            return self._state

    @property
    def cleanup_pending(self) -> bool:
        with self._condition:
            return self._cleanup_pending

    def add_cleanup(self, callback: Callable[[], object]) -> None:
        with self._condition:
            if self._state != "caller" or self._completed or not callable(callback):
                raise BookflowError("E_VALIDATION", "Transfer cleanup requires caller ownership.")
            self._callbacks.append(callback)

    def handoff(self) -> None:
        with self._condition:
            if self._state != "caller" or self._completed:
                raise BookflowError("E_VALIDATION", "Transfer handoff requires caller ownership.")
            self._state = "writer"

    def _check(self, owner: str) -> None:
        with self._condition:
            if self._state == "closed" or self._completed:
                raise _io("closed")
            if self._state != owner:
                raise BookflowError("E_VALIDATION", "Transfer operation requires its owner.")
            if self._cancelled:
                raise _io("cancelled")
            if self._clock() >= self._deadline:
                raise _io("deadline")

    def check_io(self) -> None:
        self._check("caller")

    def remaining_seconds(self) -> float:
        """Remaining caller I/O lifetime, for transport timeout composition."""
        self.check_io()
        return max(0.0, self._deadline - self._clock())

    def check_start(self) -> None:
        self._check("writer")

    def cancel(self) -> None:
        with self._condition:
            if self._state == "caller" and not self._completed:
                self._cancelled = True

    def close(self) -> None:
        """Close caller resources; leave accepted writer work untouched."""
        self._cleanup("caller")

    def finish(self) -> None:
        """Finish writer cleanup, after the job's database cleanup."""
        self._cleanup("writer")

    def _cleanup(self, owner: str) -> None:
        current = threading.get_ident()
        with self._condition:
            if self._state == "closed" or (owner == "caller" and self._state == "writer"):
                return
            if self._state != owner:
                raise BookflowError("E_VALIDATION", "Transfer cleanup requires its owner.")
            while self._cleaning_thread is not None:
                if self._cleaning_thread == current:
                    raise BookflowError("E_VALIDATION", "Transfer cleanup cannot reenter itself.")
                self._condition.wait()
                if self._state == "closed":
                    return
            self._completed = True
            self._cleaning_thread = current

        try:
            while True:
                with self._condition:
                    callback = self._callbacks[-1] if self._callbacks else None
                if callback is None:
                    self._release(self)
                else:
                    callback()
                with self._condition:
                    if self._callbacks:
                        self._callbacks.pop()
                    else:
                        self._state = "closed"
                        self._cleanup_pending = False
                        break
        except BaseException:
            with self._condition:
                self._cleanup_pending = True
            raise _io("cleanup") from None
        finally:
            with self._condition:
                self._cleaning_thread = None
                self._condition.notify_all()

    def __enter__(self) -> TransferLease:
        self.check_io()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
