"""The bulk-create workflow: create rows -> reconcile -> activate.

The rule everything here serves: we only *ask* the directory API to activate
a batch after verifying that every CSV row maps to exactly one hospital in
the batch and that the batch holds nothing else, and we only report
`completed` when the activated count matches. Any other outcome leaves a
status saying which stage failed, so the batch can be resumed.

What this cannot guarantee: the directory API has no idempotency keys, so
processing is at-least-once, not exactly-once. A create whose response we
lost may still land on the server *after* we reconcile. If it lands before
activation, the activated count gives it away (-> activation_failed). If it
lands after activation, it stays an inactive extra in the batch that we never
see. See README "Consistency guarantees".
"""

import asyncio
import logging
import time
import uuid
from typing import Dict, List, Set

from app.csv_parser import HospitalRow
from app.directory_client import DirectoryClient, DirectoryError, matches
from app.store import (
    RUNNABLE_STATUSES,
    BatchRecord,
    BatchStatus,
    BatchStore,
    RowResult,
    RowStatus,
    utc_now,
)

logger = logging.getLogger(__name__)


class BatchBusyError(Exception):
    """Another run of this batch is in progress."""


class BatchNotRunnableError(Exception):
    """The batch is already completed (or otherwise not in a runnable state)."""


class ReconciliationError(Exception):
    pass


class BatchProcessor:
    def __init__(self, client: DirectoryClient, store: BatchStore, cold_start_timeout: float = 60.0):
        self._client = client
        self._store = store
        self._cold_start_timeout = cold_start_timeout

    def create_batch(self, rows: List[HospitalRow]) -> BatchRecord:
        record = BatchRecord(
            batch_id=str(uuid.uuid4()),
            rows=[RowResult.from_row(r) for r in rows],
        )
        self._store.add(record)
        return record

    async def run(self, record: BatchRecord) -> BatchRecord:
        """Process a new batch, or resume a failed one.

        Both are the same operation: create whatever rows lack a hospital,
        reconcile, activate. Rows already created are never sent again.
        """
        # Check-then-acquire is safe here without a race: asyncio runs one
        # coroutine at a time and there is no `await` between the check and
        # taking the lock (acquiring a free Lock doesn't yield).
        if record.lock.locked():
            raise BatchBusyError(record.batch_id)
        async with record.lock:
            if record.status not in RUNNABLE_STATUSES:
                raise BatchNotRunnableError(record.batch_id)
            record.runs += 1
            record.error = None
            started = record.run_started = time.monotonic()
            try:
                await self._run_locked(record)
            except BaseException as exc:
                # Includes CancelledError: never leave a batch stuck in
                # "processing" with its lock released.
                if record.status in (BatchStatus.PROCESSING, BatchStatus.ACTIVATING):
                    record.status = (BatchStatus.ACTIVATION_FAILED
                                     if record.status is BatchStatus.ACTIVATING
                                     else BatchStatus.CREATION_FAILED)
                    record.error = f"Processing interrupted: {type(exc).__name__}"
                if not isinstance(exc, Exception):
                    raise
                logger.exception("batch %s: unexpected error", record.batch_id)
            finally:
                record.processing_time_seconds = round(time.monotonic() - started, 2)
                record.run_started = None
                record.updated_at = utc_now()
        return record

    async def _run_locked(self, record: BatchRecord) -> None:
        record.status = BatchStatus.PROCESSING
        pending = [r for r in record.rows if r.hospital_id is None]
        for r in pending:
            r.status, r.error = RowStatus.PENDING, None

        if pending:
            await self._warm_up()
            await asyncio.gather(*(self._create_row(record.batch_id, r) for r in pending))

        # Reconcile even if some rows failed: a row whose last attempt timed
        # out may in fact exist, and duplicates should be cleaned up either way.
        try:
            await self._reconcile(record)
        except (DirectoryError, ReconciliationError) as exc:
            record.status = BatchStatus.RECONCILIATION_FAILED
            record.error = f"Could not verify batch contents: {exc}"
            return

        if record.failed:
            record.status = BatchStatus.CREATION_FAILED
            record.error = f"{record.failed} of {record.total} hospitals could not be created"
            return

        record.status = BatchStatus.ACTIVATING
        try:
            activated = await self._client.activate_batch_safely(record.batch_id, record.total)
        except DirectoryError as exc:
            record.status = BatchStatus.ACTIVATION_FAILED
            record.error = f"Activation failed: {exc.message}"
            return

        if activated != record.total:
            # Most likely a create we had given up on landed between reconciling
            # and activating, so it was activated too. We don't report
            # success. A resume reconciles first (deleting an exact duplicate
            # by ID), and the activation that follows confirms by read-back
            # that the batch holds exactly our rows.
            logger.warning("batch %s: activated %s hospitals, expected %s",
                           record.batch_id, activated, record.total)
            record.status = BatchStatus.ACTIVATION_FAILED
            record.error = (
                f"Directory API activated {activated} hospitals but {record.total} were "
                "expected. Hospitals in this batch may now be active with unexpected "
                "contents; resume to re-verify the batch."
            )
            return

        for r in record.rows:
            r.status = RowStatus.CREATED_AND_ACTIVATED
        record.activated = True
        record.status = BatchStatus.COMPLETED

    async def _warm_up(self) -> None:
        # The directory API runs on a host that sleeps when idle; the first
        # request can take ~50s. Wake it with one request rather than letting
        # the whole fan-out time out together.
        try:
            await self._client.ping(timeout=self._cold_start_timeout)
        except DirectoryError as exc:
            logger.warning("directory API warm-up failed: %s", exc.message)

    async def _create_row(self, batch_id: str, r: RowResult) -> None:
        try:
            hospital = await self._client.create_hospital_safely(r.as_row(), batch_id)
        except DirectoryError as exc:
            r.status, r.error = RowStatus.FAILED, exc.message
            return
        except Exception as exc:  # one bad row must not abort its siblings
            logger.exception("row %s: unexpected error", r.row)
            r.status, r.error = RowStatus.FAILED, f"Unexpected error: {type(exc).__name__}"
            return
        r.status, r.hospital_id, r.error = RowStatus.CREATED, hospital["id"], None

    async def _reconcile(self, record: BatchRecord) -> None:
        """Make our rows and the API's view of the batch agree, or raise.

        1. Every row with a hospital_id must find that record in the batch
           with matching fields; otherwise the row is marked failed.
        2. Rows without an ID adopt an unclaimed record with identical
           fields (its create landed but the response was lost).
        3. Leftover records are deleted only if they exactly duplicate one of
           our rows. They are in a batch whose UUID only we know and are not
           claimed by any row, so they can only be extras from our retries.
           Anything else is left alone and blocks activation.
        """
        listed = await self._client.list_batch(record.batch_id)
        by_id: Dict[int, dict] = {h["id"]: h for h in listed}
        claimed: Set[int] = set()

        for r in record.rows:
            if r.hospital_id is None:
                continue
            hospital = by_id.get(r.hospital_id)
            if hospital is None or not matches(hospital, r.as_row()):
                r.status, r.hospital_id = RowStatus.FAILED, None
                r.error = "Created hospital is missing from the batch or was modified"
            else:
                claimed.add(hospital["id"])

        for r in record.rows:
            if r.hospital_id is not None:
                continue
            candidates = sorted(
                (h for h in listed if h["id"] not in claimed and matches(h, r.as_row())),
                key=lambda h: h["id"],
            )
            if candidates:
                adopted = candidates[0]
                claimed.add(adopted["id"])
                r.status, r.hospital_id = RowStatus.CREATED, adopted["id"]
                r.error = None
                logger.info("row %s: adopted hospital %s found during reconciliation",
                            r.row, adopted["id"])

        unexpected = []
        for hospital in listed:
            if hospital["id"] in claimed:
                continue
            is_duplicate = any(matches(hospital, r.as_row()) for r in record.rows)
            if not is_duplicate:
                unexpected.append(hospital["id"])
                continue
            logger.warning("batch %s: deleting duplicate hospital %s",
                           record.batch_id, hospital["id"])
            try:
                await self._client.delete_hospital(hospital["id"])
            except DirectoryError as exc:
                if exc.status_code != 404:  # already gone is fine
                    raise

        if unexpected:
            raise ReconciliationError(
                f"batch contains hospital(s) not created from this CSV: {unexpected}"
            )
