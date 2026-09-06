"""Bounded host-generation intent ownership, independent of business execution.

The HTTP owner supplies already authorized frozen state and calls start() inside
the actual execution worker. Observing an intent never schedules work. Callers
must authenticate each request and check its publication permit before delivery.
"""

from collections import Counter, OrderedDict
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, fields, is_dataclass
import secrets
import sys
import threading
import time

from pydantic import BaseModel

from bookflow.core.errors import BookflowError

MIB = 1024 * 1024


def retained_size(value):
    """Charge the complete transport-owned value graph, including shared objects once.

Host handles/functions/registry objects must not enter retained snapshots. A
permit's transport snapshot is an explicit value graph, not the live host graph.
"""
    seen, pending, total = set(), [value], 0
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        total += sys.getsizeof(item)
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple, set, frozenset)):
            pending.extend(item)
        elif isinstance(item, BaseModel):
            pending.append(item.__dict__)
            pending.append(item.__pydantic_fields_set__)
            pending.append(item.__pydantic_extra__)
            pending.append(item.__pydantic_private__)
        elif is_dataclass(item) and not isinstance(item, type):
            if hasattr(item, "__dict__"):
                pending.append(item.__dict__)
            else:
                pending.extend(getattr(item, f.name) for f in fields(item))
        elif not isinstance(item, (str, bytes, bytearray, int, float, bool, type(None))):
            raise TypeError("Retained transport state must be an explicit value graph")
    return total


def busy():
    return BookflowError("E_DB_BUSY", details={"stage": "admission", "outcome": "not_submitted"})


@dataclass(repr=False)
class Intent:
    reference: str
    owner: tuple[str, str, str]
    admitted: float
    progress: float
    state: str = "preparing"
    frozen: object = None
    prepared_bytes: int = 0
    cleanup: object = None
    abandoned: bool = False
    receipt: bytes | None = None
    publication: object = None
    completed: float | None = None
    retained_bytes: int = 0
    reason: str | None = None
    workers: int = 0
    header: dict = field(default_factory=dict)


class Intents:
    """One registry per host; lifecycle owner invokes sweep and close explicitly."""

    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.generation = secrets.token_urlsafe(18)
        self.lock = threading.RLock()
        self.active = {}
        self.completed = OrderedDict()
        self.reservations = {}
        self.closed = False

    def _expired(self, intent, now):
        return now - intent.admitted >= 300 or now - intent.progress >= 30

    def _find(self, reference, owner):
        intent = self.active.get(reference) or self.completed.get(reference)
        return intent if intent is not None and intent.owner == owner else None

    def admit(self, owner, *, cleanup=None, header=None):
        self.sweep()
        with self.lock:
            if self.closed or len(self.active) >= 8 or sum(i.owner[2] == owner[2] for i in self.active.values()) >= 2:
                raise busy()
            now = self.clock()
            intent = Intent(self.generation + "." + secrets.token_urlsafe(24), owner,
                            now, now, cleanup=cleanup, header=deepcopy(header or {}))
            intent.prepared_bytes = retained_size(intent.header)
            total = sum(i.prepared_bytes for i in self.active.values())
            own = sum(i.prepared_bytes for i in self.active.values() if i.owner[2] == owner[2])
            if total + intent.prepared_bytes > 64 * MIB or own + intent.prepared_bytes > 8 * MIB:
                raise busy()
            self.active[intent.reference] = intent
            return intent

    def progress(self, intent):
        with self.lock:
            self._preexecution(intent)
            intent.progress = self.clock()

    @contextmanager
    def preparation_worker(self, intent):
        """Pin preparing/receiving resources while their actual worker uses them."""
        with self.lock:
            self._preexecution(intent)
            if intent.state not in {"preparing", "receiving"}:
                raise RuntimeError("Preparation is already sealed")
            intent.workers += 1
        try:
            yield
        finally:
            with self.lock:
                intent.workers -= 1
                cleanup = intent.abandoned and not intent.workers
            if cleanup:
                self.finish(intent, reason=intent.reason or "expired_before_submission")

    def _preexecution(self, intent):
        if (self.active.get(intent.reference) is not intent or intent.abandoned
                or intent.state not in {"preparing", "receiving", "ready", "queued"}
                or self._expired(intent, self.clock())):
            before = intent.state in {"preparing", "receiving", "ready", "queued"}
            raise BookflowError("E_IO", details={"reason": "intent_not_executable", "outcome": "not_submitted" if before else "unknown"})

    def ready(self, intent, frozen, *, retain=True):
        frozen = deepcopy(frozen) if retain else None
        amount = (retained_size((intent.reference, intent.owner, intent.header, frozen))
                  + sys.getsizeof(intent) + sys.getsizeof(intent.__dict__)) if retain else intent.prepared_bytes
        rejected = False
        with self.lock:
            self._preexecution(intent)
            if intent.state not in {"preparing", "receiving"}:
                raise BookflowError("E_USAGE", details={"reason": "intent_already_sealed"})
            if retain:
                total = sum(i.prepared_bytes for i in self.active.values() if i is not intent)
                own = sum(i.prepared_bytes for i in self.active.values() if i is not intent and i.owner[2] == intent.owner[2])
                if total + amount > 64 * MIB or own + amount > 8 * MIB:
                    intent.abandoned = True
                    intent.reason = "rejected_before_submission"
                    rejected = True
            # Direct execution remains worker-owned, not parked in ready storage.
            if not rejected:
                intent.frozen = frozen if retain else None
                intent.prepared_bytes = amount
                intent.state = "ready"
            pinned = bool(intent.workers)
        if rejected:
            if not pinned:
                self.finish(intent, reason="rejected_before_submission")
            raise busy()

    def queue(self, intent):
        """Only the first execute request wins admission to the execution worker."""
        with self.lock:
            if intent.state in {"queued", "started", "delivering", "completed"}:
                return False
            self._preexecution(intent)
            if intent.state != "ready":
                raise BookflowError("E_USAGE", details={"reason": "intent_not_sealed"})
            intent.state = "queued"
            return True

    def start(self, intent):
        """Called in the actual reader/writer callback, before core dispatch."""
        with self.lock:
            self._preexecution(intent)
            if intent.state != "queued":
                raise BookflowError("E_USAGE", details={"reason": "intent_not_queued"})
            intent.state = "started"
            frozen = intent.frozen
            intent.frozen, intent.prepared_bytes = None, 0
            return frozen

    def reject_delivery(self, intent):
        """A typed input rejection may deliver once, without scheduling execution."""
        with self.lock:
            self._preexecution(intent)
            if intent.state not in {'preparing', 'ready', 'receiving'} or intent.workers:
                raise RuntimeError('Input rejection lost its preparation owner')
            intent.state = 'delivering'
            intent.reason = 'rejected_before_submission'
            intent.progress = self.clock()
            intent.execution_returned = True
            intent.frozen, intent.prepared_bytes = None, 0

    def delivery(self, intent):
        with self.lock:
            if self.active.get(intent.reference) is not intent or intent.state != "started":
                raise RuntimeError("Only the execution owner can begin delivery")
            intent.state = "delivering"
            intent.progress = self.clock()

    def resume_delivery(self, intent):
        """Move one retained identity into a delivery slot, never an execution queue."""
        self.sweep()
        with self.lock:
            if intent.state != "completed" or self.completed.get(intent.reference) is not intent:
                return False
            if intent.receipt is None or intent.publication is None or intent.abandoned:
                return False
            if self.closed or len(self.active) >= 8 or sum(i.owner[2] == intent.owner[2] for i in self.active.values()) >= 2:
                raise BookflowError("E_DB_BUSY", details={"stage": "recovery_admission", "outcome": "unknown"})
            del self.completed[intent.reference]
            self.active[intent.reference] = intent
            intent.state = "delivering"
            intent.progress = self.clock()
            intent.execution_returned = True
            return True

    def reserve_receipt(self, intent, receipt, publication):
        """Reserve finite recovery memory before advertising it in a terminal.

        The active delivery slot remains held until finish() after socket cleanup.
        Reservations cannot be evicted by another delivery or completed-cache trim.
        """
        from datetime import datetime, timedelta, timezone
        unavailable = {"mode": "unavailable", "retained_until": None,
                       "receipt_available": False, "inspection_available": False}
        receipt = receipt if isinstance(receipt, bytes) and len(receipt) <= MIB else None
        values = {"receipt": receipt, "publication": publication}
        amount = retained_size((intent.header, intent.owner, intent.reference, values)) + 4096
        if amount > 4 * MIB:
            return unavailable
        with self.lock:
            if self.active.get(intent.reference) is not intent or intent.abandoned or intent.state != "delivering":
                return unavailable
            if intent.reference in self.reservations:
                raise RuntimeError("Delivery recovery is already reserved")
            self.reservations[intent.reference] = (intent.owner[2], amount, values)
            self._trim()
            own = [row for row in self.reservations.values() if row[0] == intent.owner[2]]
            if (len(self.reservations) > 128 or len(own) > 16
                    or sum(row[1] for row in self.reservations.values()) > 32 * MIB
                    or sum(row[1] for row in own) > 4 * MIB):
                del self.reservations[intent.reference]
                return unavailable
            seconds = min(60, 300 - (self.clock() - intent.completed)) if intent.completed is not None else 60
            return {"mode": "retained", "retained_until": (datetime.now(timezone.utc) + timedelta(seconds=max(0, seconds))).isoformat().replace('+00:00', 'Z'),
                    "receipt_available": receipt is not None, "inspection_available": publication is not None}

    def finish(self, intent, *, receipt=None, publication=None, reason=None):
        """Actual owner calls after worker/stream cleanup; never on caller timeout."""
        with self.lock:
            if self.active.get(intent.reference) is not intent:
                return
            if intent.state == "cleaning":
                return
            if intent.workers:
                raise RuntimeError("Preparation worker still owns resources")
            intent.state = "cleaning"
            intent.reason = reason
            cleanup, intent.cleanup = intent.cleanup, None
        try:
            if cleanup is not None:
                cleanup()
        except BaseException:
            # Keep capacity reserved until cleanup can actually be retried.
            with self.lock:
                intent.cleanup = cleanup
                intent.state = "cleanup_failed"
            raise
        with self.lock:
            del self.active[intent.reference]
            reservation = self.reservations.pop(intent.reference, None)
            if reservation is not None:
                receipt, publication = reservation[2]["receipt"], reservation[2]["publication"]
            intent.frozen, intent.prepared_bytes = None, 0
            intent.state, intent.reason = "completed", reason or intent.reason
            intent.completed = intent.completed if intent.completed is not None else self.clock()
            intent.progress = self.clock()
            intent.receipt = receipt if isinstance(receipt, bytes) and len(receipt) <= MIB and not intent.abandoned else None
            intent.publication = publication
            # Include record fields/strings/metadata, not merely serialized JSON.
            try:
                intent.retained_bytes = retained_size(intent)
            except TypeError:
                intent.receipt = intent.publication = None
                raise
            if intent.retained_bytes > 4 * MIB:
                intent.receipt = intent.publication = None
                return
            self.completed[intent.reference] = intent
            self._trim()

    def _trim(self):
        while self.completed:
            entries = list(self.completed.values())
            counts = Counter(i.owner[2] for i in entries)
            sizes = Counter()
            for intent in entries:
                sizes[intent.owner[2]] += intent.retained_bytes
            for principal, amount, _values in self.reservations.values():
                counts[principal] += 1
                sizes[principal] += amount
            over = {key for key in counts if counts[key] > 16 or sizes[key] > 4 * MIB}
            if len(entries) + len(self.reservations) <= 128 and sum(sizes.values()) <= 32 * MIB and not over:
                break
            victim = next((i for i in entries if not over or i.owner[2] in over), None)
            if victim is None:
                break  # Active reservations are not evictable.
            self._discard(victim)

    def _discard(self, intent):
        self.completed.pop(intent.reference, None)
        intent.receipt = intent.publication = None

    def observe(self, reference, owner):
        self.sweep()
        with self.lock:
            intent = self._find(reference, owner)
            if intent is not None and intent.state == "completed":
                intent.progress = self.clock()
            return intent

    def release(self, reference, owner):
        with self.lock:
            intent = self._find(reference, owner)
            if intent is None:
                return None
            intent.abandoned = True
            if intent.completed is not None:
                self._discard(intent)
            elif intent.state in {"preparing", "receiving", "ready"}:
                if intent.workers:
                    return intent
            else:
                # Queued callback must observe abandonment; started writer owns cleanup.
                return intent
        if intent.completed is None:
            self.finish(intent, reason="released_before_submission")
        return intent

    def sweep(self):
        with self.lock:
            now = self.clock()
            for intent in list(self.completed.values()):
                if now - intent.completed >= 300 or now - intent.progress >= 60:
                    self._discard(intent)
            expired = [i for i in self.active.values() if i.state in {"preparing", "receiving", "ready"} and self._expired(i, now)]
            orphaned = [i for i in self.active.values() if i.state in {"queued", "started", "delivering"}
                        and getattr(i, "execution_returned", False) and not i.workers and now - i.progress >= 30]
            retry = [i for i in self.active.values() if i.state == "cleanup_failed"]
            for intent in expired + orphaned:
                intent.abandoned = True
            for intent in orphaned:
                intent.reason = "execution_result_unavailable"
        failures = 0
        for intent in expired + orphaned + retry:
            with self.lock:
                pinned = bool(intent.workers)
            if not pinned:
                try:
                    self.finish(intent, reason=intent.reason if intent in retry or intent in orphaned else "expired_before_submission")
                except Exception:
                    # Retry on the next lifecycle tick; failed cleanup retains its slot.
                    failures += 1
        return failures

    def close(self):
        with self.lock:
            self.closed = True
            entries = list(self.active.values())
            for intent in list(self.completed.values()):
                self._discard(intent)
        for intent in entries:
            if intent.state == "cleanup_failed" or (getattr(intent, "execution_returned", False) and not intent.workers):
                self.finish(intent, reason=intent.reason)
            else:
                self.release(intent.reference, intent.owner)


class OwnedIntents(Intents):
    """Autonomous reaping, tied to the actual host shutdown rather than client polling."""

    def __init__(self, host, *, clock=time.monotonic):
        super().__init__(clock=clock)
        self._stop = threading.Event()
        self._reaper = threading.Thread(target=self._reap, name="bookflow-mcp-intents", daemon=True)
        host.own_resource(self)
        self._reaper.start()

    def _reap(self):
        while not self._stop.wait(0.25):
            self.sweep()

    def close(self):
        self._stop.set()
        super().close()
        if self._reaper.is_alive():
            self._reaper.join(timeout=1)
            if self._reaper.is_alive():
                raise BookflowError("E_DB_BUSY", message="MCP resource cleanup is still running.")
