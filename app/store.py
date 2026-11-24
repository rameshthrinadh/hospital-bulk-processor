"""In-memory batch state.

State lives in this process only: it is lost on restart and is not shared
between workers, so the app must run as a single process. The hospitals
themselves live on the directory API, so a lost record here never loses
data there - it only loses our view of the batch.
"""

import asyncio
import enum
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from app.csv_parser import HospitalRow


class RowStatus(str, enum.Enum):
    PENDING = "pending"
    CREATED = "created"  # exists on the directory API, still inactive
    CREATED_AND_ACTIVATED = "created_and_activated"
    FAILED = "failed"


class BatchStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    ACTIVATING = "activating"
    COMPLETED = "completed"
    # The three failure states stay distinct so a client can tell *what* went
    # wrong. In all three the batch is inactive and can be resumed.
    CREATION_FAILED = "creation_failed"          # one or more rows not created
    RECONCILIATION_FAILED = "reconciliation_failed"  # couldn't verify batch contents
    ACTIVATION_FAILED = "activation_failed"      # all created, activation failed


RUNNABLE_STATUSES = {
    BatchStatus.PENDING,
    BatchStatus.CREATION_FAILED,
    BatchStatus.RECONCILIATION_FAILED,
    BatchStatus.ACTIVATION_FAILED,
}


@dataclass
class RowResult:
    row: int
    name: str
    address: str
    phone: Optional[str]
    status: RowStatus = RowStatus.PENDING
    hospital_id: Optional[int] = None
    error: Optional[str] = None

    @classmethod
    def from_row(cls, row: HospitalRow) -> "RowResult":
        return cls(row=row.row, name=row.name, address=row.address, phone=row.phone)

    def as_row(self) -> HospitalRow:
        return HospitalRow(self.row, self.name, self.address, self.phone)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class BatchRecord:
    batch_id: str
    rows: List[RowResult]
    status: BatchStatus = BatchStatus.PENDING
    error: Optional[str] = None
    activated: bool = False
    runs: int = 0
    processing_time_seconds: float = 0.0  # duration of the most recent run
    run_started: Optional[float] = None  # time.monotonic() while a run is in progress
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    # Held for the whole of a processing run; see BatchProcessor.run.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def processed(self) -> int:
        return sum(1 for r in self.rows if r.hospital_id is not None)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.rows if r.status is RowStatus.FAILED)


class BatchStore:
    def __init__(self, max_batches: int = 1000):
        self._batches: "OrderedDict[str, BatchRecord]" = OrderedDict()
        self._max_batches = max_batches

    def add(self, record: BatchRecord) -> None:
        self._batches[record.batch_id] = record
        self._evict(keep=record.batch_id)

    def get(self, batch_id: str) -> Optional[BatchRecord]:
        return self._batches.get(batch_id)

    def _evict(self, keep: str) -> None:
        # Bound memory by forgetting the oldest batches that aren't running.
        for batch_id in list(self._batches):
            if len(self._batches) <= self._max_batches:
                return
            if batch_id != keep and not self._batches[batch_id].lock.locked():
                del self._batches[batch_id]
