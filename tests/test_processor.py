import asyncio
import time

import pytest

from app.csv_parser import HospitalRow
from app.directory_client import DirectoryClient
from app.processor import BatchBusyError, BatchNotRunnableError, BatchProcessor
from app.store import BatchStatus, BatchStore, RowStatus
from tests.fake_directory import FakeDirectory


def rows(n=3):
    return [HospitalRow(i, f"Hospital {i}", f"{i} Main St", None) for i in range(1, n + 1)]


@pytest.fixture
def fake():
    return FakeDirectory()


@pytest.fixture
async def processor(fake):
    async with fake.client() as http:
        client = DirectoryClient(http, max_attempts=3, retry_backoff_seconds=0, max_concurrency=5)
        yield BatchProcessor(client, BatchStore())


def posts(fake):
    return fake.calls.count("POST /hospitals/")


def patches(fake):
    return sum(1 for c in fake.calls if c.startswith("PATCH"))


def assert_inactive(fake, batch_id):
    assert fake.batch(batch_id), "expected hospitals in batch"
    assert not any(h["active"] for h in fake.batch(batch_id))


# ---- happy path -------------------------------------------------------------

async def test_all_rows_created_then_batch_activated(processor, fake):
    record = processor.create_batch(rows(3))
    await processor.run(record)

    assert record.status is BatchStatus.COMPLETED
    assert record.activated is True
    assert (record.total, record.processed, record.failed) == (3, 3, 0)
    assert all(r.status is RowStatus.CREATED_AND_ACTIVATED for r in record.rows)
    assert sorted(r.hospital_id for r in record.rows) == sorted(h["id"] for h in fake.batch(record.batch_id))
    assert all(h["active"] for h in fake.batch(record.batch_id))
    assert patches(fake) == 1


# ---- partial creation failure ----------------------------------------------

async def test_partial_failure_reports_rows_and_never_activates(processor, fake):
    fake.create_faults["Hospital 2"] = ["422"]
    record = processor.create_batch(rows(3))
    await processor.run(record)

    assert record.status is BatchStatus.CREATION_FAILED
    assert record.activated is False
    assert (record.processed, record.failed) == (2, 1)
    failed = record.rows[1]
    assert failed.status is RowStatus.FAILED and failed.hospital_id is None
    assert "422" in failed.error
    assert [r.status for r in (record.rows[0], record.rows[2])] == [RowStatus.CREATED] * 2
    assert patches(fake) == 0
    assert_inactive(fake, record.batch_id)


async def test_resume_retries_only_failed_rows_then_activates(processor, fake):
    fake.create_faults["Hospital 2"] = ["422"]  # fails once, then succeeds
    record = processor.create_batch(rows(3))
    await processor.run(record)
    assert posts(fake) == 3

    await processor.run(record)

    assert posts(fake) == 4  # only the failed row was re-sent
    assert record.status is BatchStatus.COMPLETED
    assert record.runs == 2
    assert len(fake.batch(record.batch_id)) == 3
    assert all(h["active"] for h in fake.batch(record.batch_id))


async def test_resume_with_still_failing_row_stays_inactive(processor, fake):
    fake.create_faults["Hospital 2"] = ["422", "422"]
    record = processor.create_batch(rows(3))
    await processor.run(record)
    await processor.run(record)
    assert record.status is BatchStatus.CREATION_FAILED
    assert patches(fake) == 0
    assert_inactive(fake, record.batch_id)


async def test_all_rows_failing(processor, fake):
    for r in rows(2):
        fake.create_faults[r.name] = ["422"]
    record = processor.create_batch(rows(2))
    await processor.run(record)
    assert record.status is BatchStatus.CREATION_FAILED
    assert (record.processed, record.failed) == (0, 2)
    assert patches(fake) == 0


# ---- activation failure ----------------------------------------------------

async def test_activation_failure_is_distinct_from_creation_failure(processor, fake):
    fake.activate_faults = ["500", "500", "500"]
    record = processor.create_batch(rows(3))
    await processor.run(record)

    assert record.status is BatchStatus.ACTIVATION_FAILED
    assert record.error.startswith("Activation failed")
    assert (record.processed, record.failed) == (3, 0)  # every row was created
    assert all(r.status is RowStatus.CREATED for r in record.rows)
    assert_inactive(fake, record.batch_id)


async def test_resume_after_activation_failure_does_not_recreate(processor, fake):
    fake.activate_faults = ["500", "500", "500"]
    record = processor.create_batch(rows(3))
    await processor.run(record)

    await processor.run(record)

    assert posts(fake) == 3
    assert record.status is BatchStatus.COMPLETED
    assert all(h["active"] for h in fake.batch(record.batch_id))


async def test_activation_lost_response_still_completes(processor, fake):
    fake.activate_faults = ["lost_response"]
    record = processor.create_batch(rows(3))
    await processor.run(record)
    assert record.status is BatchStatus.COMPLETED


# ---- reconciliation and duplicates -----------------------------------------

async def test_duplicate_from_blind_retry_is_deleted_by_id_before_activation(processor, fake):
    # First POST lands but its response is lost; the recovery lookup fails,
    # so the client retries and creates a second copy.
    fake.create_faults["Hospital 1"] = ["lost_response"]
    fake.list_faults = ["500"]
    record = processor.create_batch(rows(2))
    await processor.run(record)

    batch = fake.batch(record.batch_id)
    assert record.status is BatchStatus.COMPLETED
    assert len(batch) == 2
    assert {h["id"] for h in batch} == {r.hospital_id for r in record.rows}
    assert posts(fake) == 3  # Hospital 1 was sent twice
    assert sum(1 for c in fake.calls if c.startswith("DELETE /hospitals/")) == 1


async def test_row_given_up_on_is_adopted_if_it_actually_exists(processor, fake):
    # Attempt 1 lands (response lost), lookups fail, attempts 2-3 time out
    # without landing. The client reports failure, but reconciliation finds
    # the record and the batch can still complete.
    fake.create_faults["Hospital 1"] = ["lost_response", "timeout", "timeout"]
    fake.list_faults = ["500", "500"]
    record = processor.create_batch(rows(2))
    await processor.run(record)

    assert record.status is BatchStatus.COMPLETED
    assert posts(fake) == 4  # 3 attempts for Hospital 1, only the first landed
    assert record.rows[0].hospital_id == fake.batch(record.batch_id)[0]["id"]
    assert len(fake.batch(record.batch_id)) == 2


async def test_late_create_activated_with_batch_is_activation_failed_then_resumable(processor, fake):
    # A create we gave up on lands between reconciliation and activation, so
    # the API activates 3 hospitals where we expected 2.
    record = processor.create_batch(rows(2))

    def late_duplicate(batch_id):
        fake.before_activate = None
        fake._create({"name": "Hospital 1", "address": "1 Main St",
                      "creation_batch_id": batch_id})

    fake.before_activate = late_duplicate
    await processor.run(record)

    assert record.status is BatchStatus.ACTIVATION_FAILED
    assert record.activated is False
    assert "activated 3 hospitals but 2 were expected" in record.error
    assert all(r.status is RowStatus.CREATED for r in record.rows)

    # Resume: reconciliation deletes the extra by ID; re-activation gets 400
    # "already active", and read-back confirms exactly our two rows.
    await processor.run(record)
    batch = fake.batch(record.batch_id)
    assert record.status is BatchStatus.COMPLETED
    assert {h["id"] for h in batch} == {r.hospital_id for r in record.rows}
    assert all(h["active"] for h in batch)


async def test_activated_count_lower_than_expected_is_activation_failed(processor, fake):
    record = processor.create_batch(rows(3))

    def lose_one(batch_id):
        fake.before_activate = None
        del fake.hospitals[fake.batch(batch_id)[0]["id"]]

    fake.before_activate = lose_one
    await processor.run(record)
    assert record.status is BatchStatus.ACTIVATION_FAILED
    assert record.activated is False
    assert "activated 2 hospitals but 3 were expected" in record.error


async def test_unknown_record_in_batch_blocks_activation_and_is_not_deleted(processor, fake):
    record = processor.create_batch(rows(2))
    # Same *name* as one of our rows, different address: not a duplicate.
    fake._create({"name": "Hospital 1", "address": "Elsewhere",
                  "creation_batch_id": record.batch_id})
    await processor.run(record)

    assert record.status is BatchStatus.RECONCILIATION_FAILED
    assert "not created from this CSV" in record.error
    assert len(fake.batch(record.batch_id)) == 3  # nothing deleted
    assert not any(c.startswith("DELETE") for c in fake.calls)
    assert patches(fake) == 0
    assert_inactive(fake, record.batch_id)


async def test_reconciliation_list_failure_blocks_activation_then_resume_succeeds(processor, fake):
    fake.list_faults = ["500"]  # the reconciliation read fails
    record = processor.create_batch(rows(2))
    await processor.run(record)
    assert record.status is BatchStatus.RECONCILIATION_FAILED
    assert patches(fake) == 0
    assert_inactive(fake, record.batch_id)

    await processor.run(record)
    assert record.status is BatchStatus.COMPLETED
    assert posts(fake) == 2


async def test_externally_deleted_hospital_is_recreated_on_resume(processor, fake):
    fake.activate_faults = ["500", "500", "500"]
    record = processor.create_batch(rows(2))
    await processor.run(record)
    del fake.hospitals[record.rows[0].hospital_id]

    await processor.run(record)  # reconciliation notices the missing record
    assert record.status is BatchStatus.CREATION_FAILED
    assert record.rows[0].status is RowStatus.FAILED

    await processor.run(record)  # and the next resume recreates it
    assert record.status is BatchStatus.COMPLETED
    assert len(fake.batch(record.batch_id)) == 2


# ---- concurrency -----------------------------------------------------------

async def test_creates_run_concurrently_but_bounded(fake):
    fake.latency = 0.05
    async with fake.client() as http:
        client = DirectoryClient(http, retry_backoff_seconds=0, max_concurrency=5)
        processor = BatchProcessor(client, BatchStore())
        record = processor.create_batch(rows(20))
        started = time.monotonic()
        await processor.run(record)
        elapsed = time.monotonic() - started

    assert record.status is BatchStatus.COMPLETED
    assert fake.max_in_flight == 5
    assert elapsed < 20 * 0.05 / 2  # far faster than sequential


async def test_concurrency_limit_is_shared_across_batches(fake):
    fake.latency = 0.02
    async with fake.client() as http:
        client = DirectoryClient(http, retry_backoff_seconds=0, max_concurrency=4)
        processor = BatchProcessor(client, BatchStore())
        a, b = processor.create_batch(rows(10)), processor.create_batch(rows(10))
        await asyncio.gather(processor.run(a), processor.run(b))

    assert a.status is b.status is BatchStatus.COMPLETED
    assert fake.max_in_flight == 4


async def test_concurrent_run_of_same_batch_is_rejected(fake):
    fake.latency = 0.05
    async with fake.client() as http:
        processor = BatchProcessor(DirectoryClient(http, retry_backoff_seconds=0), BatchStore())
        record = processor.create_batch(rows(3))
        first = asyncio.create_task(processor.run(record))
        await asyncio.sleep(0.01)  # let the first run take the lock
        with pytest.raises(BatchBusyError):
            await processor.run(record)
        await first
    assert record.status is BatchStatus.COMPLETED
    assert posts(fake) == 3


async def test_completed_batch_cannot_be_run_again(processor, fake):
    record = processor.create_batch(rows(2))
    await processor.run(record)
    with pytest.raises(BatchNotRunnableError):
        await processor.run(record)
    assert patches(fake) == 1


async def test_cancellation_leaves_batch_inactive_and_resumable(fake):
    fake.latency = 0.05
    async with fake.client() as http:
        processor = BatchProcessor(DirectoryClient(http, retry_backoff_seconds=0), BatchStore())
        record = processor.create_batch(rows(3))
        task = asyncio.create_task(processor.run(record))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert record.status is BatchStatus.CREATION_FAILED
        assert "interrupted" in record.error
        assert not record.lock.locked()
        assert patches(fake) == 0

        await processor.run(record)  # resume works after an interruption
    assert record.status is BatchStatus.COMPLETED
    assert len(fake.batch(record.batch_id)) == 3


async def test_progress_is_visible_while_running(fake):
    fake.latency = 0.03
    async with fake.client() as http:
        client = DirectoryClient(http, retry_backoff_seconds=0, max_concurrency=2)
        processor = BatchProcessor(client, BatchStore())
        record = processor.create_batch(rows(6))
        task = asyncio.create_task(processor.run(record))
        await asyncio.sleep(0.1)
        mid = (record.status, record.processed)
        await task
    assert mid[0] is BatchStatus.PROCESSING
    assert 0 < mid[1] < 6
    assert record.processed == 6
