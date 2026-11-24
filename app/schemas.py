"""HTTP response models.

BulkResult keeps every field of the assignment's expected response, with the
same names and meaning, and adds `status` and `error` (batch level) and
`error` (row level) so failures can be told apart.
"""

import time
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

from app.csv_parser import HospitalRow, RowError
from app.store import BatchRecord, BatchStatus, RowStatus


class HospitalResult(BaseModel):
    row: int
    hospital_id: Optional[int]
    name: str
    status: RowStatus
    error: Optional[str] = None


class BulkResult(BaseModel):
    batch_id: str
    total_hospitals: int
    processed_hospitals: int  # rows that exist on the directory API
    failed_hospitals: int
    processing_time_seconds: float
    batch_activated: bool
    hospitals: List[HospitalResult]
    # Additions to the assignment's format:
    status: BatchStatus
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime  # end of the most recent run

    @classmethod
    def from_record(cls, record: BatchRecord) -> "BulkResult":
        elapsed = record.processing_time_seconds
        if record.run_started is not None:  # still running: report time so far
            elapsed = round(time.monotonic() - record.run_started, 2)
        return cls(
            batch_id=record.batch_id,
            total_hospitals=record.total,
            processed_hospitals=record.processed,
            failed_hospitals=record.failed,
            processing_time_seconds=elapsed,
            batch_activated=record.activated,
            hospitals=[
                HospitalResult(row=r.row, hospital_id=r.hospital_id, name=r.name,
                               status=r.status, error=r.error)
                for r in record.rows
            ],
            status=record.status,
            error=record.error,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class RowErrorOut(BaseModel):
    row: Optional[int]  # null for file-level problems (header, size, encoding)
    message: str

    @classmethod
    def from_error(cls, error: RowError) -> "RowErrorOut":
        return cls(row=error.row, message=error.message)


class ValidatedRow(BaseModel):
    row: int
    name: str
    address: str
    phone: Optional[str]

    @classmethod
    def from_row(cls, row: HospitalRow) -> "ValidatedRow":
        return cls(row=row.row, name=row.name, address=row.address, phone=row.phone)


class ValidationResult(BaseModel):
    valid: bool
    total_hospitals: int
    errors: List[RowErrorOut]
    hospitals: List[ValidatedRow]


class CSVErrorResponse(BaseModel):
    detail: str
    errors: List[RowErrorOut]
