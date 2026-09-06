import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from bookflow.adapters.mcp.intents import Intents, MIB, retained_size
from bookflow.core.errors import BookflowError


class Clock:
    now = 0

    def __call__(self):
        return self.now


OWNER = ("token", "actor", "principal")


def ready(store, owner=OWNER):
    intent = store.admit(owner)
    store.ready(intent, {"command": "company update", "input": {"fax": "new"}})
    return intent


def test_reference_alias_race_has_one_queue_and_one_execution_identity():
    store = Intents()
    intent = ready(store)
    barrier = threading.Barrier(2)

    def execute(alias):
        # Both envelope spellings resolve the same opaque reference, never its body.
        observed = store.observe(alias, OWNER)
        barrier.wait()
        return store.queue(observed)

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(execute, [intent.reference, intent.reference]))
    assert sorted(outcomes) == [False, True]
    assert store.start(intent)["input"] == {"fax": "new"}
    store.delivery(intent)
    store.finish(intent, receipt=b'{"ok":true}', publication={"actor": "actor"})
    assert store.observe(intent.reference, OWNER) is intent
    assert not store.queue(intent)
    other = ready(store)
    assert other.reference != intent.reference
    assert store.queue(other)


def test_slots_cover_principal_across_tokens_and_companies():
    store = Intents()
    store.admit(OWNER)
    store.admit(("other-token", "other-actor", "principal"))
    with pytest.raises(BookflowError) as caught:
        store.admit(("third-token", "third-actor", "principal"))
    assert caught.value.details["outcome"] == "not_submitted"
    for number in range(6):
        store.admit((str(number), str(number), str(number)))
    with pytest.raises(BookflowError):
        store.admit(("last", "last", "last"))
    assert len(store.active) == 8


def test_sealed_input_is_not_an_alias_of_the_callers_mutable_object():
    store = Intents()
    source = {"input": {"fax": "original"}}
    intent = store.admit(OWNER)
    store.ready(intent, source)
    source["input"]["fax"] = "changed"
    store.queue(intent)
    assert store.start(intent) == {"input": {"fax": "original"}}


def test_receiving_progress_cannot_extend_absolute_lifetime():
    clock = Clock()
    store = Intents(clock=clock)
    intent = store.admit(OWNER)
    for second in range(0, 300, 20):
        clock.now = second
        store.progress(intent)
    clock.now = 300
    with pytest.raises(BookflowError):
        store.progress(intent)
    store.sweep()
    assert not store.active
    assert store.observe(intent.reference, OWNER).reason == "expired_before_submission"


def test_aggregate_cache_budget_applies_across_principals():
    store = Intents()
    for number in range(140):
        owner = (str(number), str(number), str(number))
        intent = ready(store, owner)
        store.queue(intent)
        store.start(intent)
        store.finish(intent, receipt=b"x" * (300 * 1024), publication={"actor": number})
        assert len(store.completed) <= 128
        assert sum(i.retained_bytes for i in store.completed.values()) <= 32 * MIB


def test_polling_does_not_renew_ready_deadline_and_loss_is_not_tombstone():
    clock, closed = Clock(), []
    store = Intents(clock=clock)
    intent = store.admit(OWNER, cleanup=lambda: closed.append(True))
    store.ready(intent, {})
    for second in range(30):
        clock.now = second
        assert store.observe(intent.reference, OWNER).state == "ready"
    clock.now = 30
    tombstone = store.observe(intent.reference, OWNER)
    assert tombstone.reason == "expired_before_submission"
    assert closed == [True] and not store.active
    assert store.observe(intent.reference, ("foreign", "actor", "principal")) is None
    clock.now = 90
    assert store.observe(intent.reference, OWNER) is None
    assert Intents().observe(intent.reference, OWNER) is None


def test_queued_expiry_checked_in_worker_and_started_release_holds_capacity():
    clock = Clock()
    store = Intents(clock=clock)
    intent = ready(store)
    store.queue(intent)
    clock.now = 30
    with pytest.raises(BookflowError) as caught:
        store.start(intent)
    assert caught.value.details["outcome"] == "not_submitted"
    assert intent.reference in store.active
    store.finish(intent, reason="expired_before_submission")
    running = ready(store)
    store.queue(running)
    store.start(running)
    store.release(running.reference, OWNER)
    assert running.reference in store.active
    clock.now = 1000
    store.sweep()
    assert running.reference in store.active
    store.finish(running, receipt=b'{"committed":true}')
    assert running.reference not in store.active
    assert running.receipt is None


def test_abandoned_preparation_cleans_only_after_actual_owner_exits():
    clock, closed = Clock(), []
    store = Intents(clock=clock)
    intent = store.admit(OWNER, cleanup=lambda: closed.append(True))
    with store.preparation_worker(intent):
        clock.now = 31
        store.sweep()
        assert intent.abandoned and not closed
        assert intent.reference in store.active
    assert closed == [True] and intent.reference not in store.active


def test_prepared_memory_and_cache_bytes_include_metadata_graphs():
    store = Intents()
    intent = store.admit(OWNER)
    huge = {"memo": "x" * (8 * MIB)}
    assert retained_size(huge) > 8 * MIB
    with pytest.raises(BookflowError):
        store.ready(intent, huge)
    # The valid direct worker does not park its working model in retained storage.
    store.ready(intent, huge, retain=False)
    assert intent.prepared_bytes == 0 and intent.frozen is None
    store.queue(intent)
    store.start(intent)
    store.finish(intent, receipt=b"x" * (MIB + 1), publication={"guard": "y" * (4 * MIB)})
    assert store.observe(intent.reference, OWNER) is None
    assert not store.active and not store.completed


def test_receipt_bounds_and_absolute_expiry_despite_continuous_reads():
    clock = Clock()
    store = Intents(clock=clock)
    references = []
    for number in range(30):
        intent = ready(store)
        store.queue(intent)
        store.start(intent)
        store.finish(intent, receipt=b"x" * (300 * 1024), publication={"index": number})
        references.append(intent.reference)
    assert len(store.completed) <= 16
    assert sum(i.retained_bytes for i in store.completed.values()) <= 4 * MIB
    assert store.observe(references[0], OWNER) is None
    latest = references[-1]
    for second in range(0, 300, 30):
        clock.now = second
        assert store.observe(latest, OWNER) is not None
    clock.now = 300
    assert store.observe(latest, OWNER) is None


def test_cleanup_failure_keeps_slot_until_real_cleanup_succeeds():
    calls = []

    def cleanup():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("held owner")

    store = Intents()
    intent = store.admit(OWNER, cleanup=cleanup)
    with pytest.raises(OSError):
        store.release(intent.reference, OWNER)
    assert intent.reference in store.active
    assert store.sweep() == 0
    assert intent.reference not in store.active and len(calls) == 2
    assert store.observe(intent.reference, OWNER).reason == "released_before_submission"
