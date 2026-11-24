import asyncio

from app.store import BatchRecord, BatchStore


def record(batch_id):
    return BatchRecord(batch_id=batch_id, rows=[])


def test_evicts_oldest_when_full():
    store = BatchStore(max_batches=2)
    for batch_id in "abc":
        store.add(record(batch_id))
    assert store.get("a") is None
    assert store.get("b") and store.get("c")


async def test_never_evicts_a_running_batch():
    store = BatchStore(max_batches=1)
    running = record("a")
    await running.lock.acquire()
    store.add(running)
    store.add(record("b"))
    assert store.get("a") is running
    assert store.get("b") is not None  # the new batch is kept too
