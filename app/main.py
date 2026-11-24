"""HTTP API for bulk hospital processing."""

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import List, Optional

import httpx
from fastapi import (APIRouter, FastAPI, File, HTTPException, Query, Request, Response,
                     UploadFile, WebSocket, WebSocketDisconnect)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import Settings, settings
from app.csv_parser import CSVValidationError, HospitalRow, parse_hospital_csv
from app.directory_client import DirectoryClient
from app.limits import BodySizeLimitMiddleware
from app.processor import BatchBusyError, BatchNotRunnableError, BatchProcessor
from app.schemas import (
    BulkResult,
    CSVErrorResponse,
    RowErrorOut,
    ValidatedRow,
    ValidationResult,
)
from app.store import BatchRecord, BatchStatus, BatchStore
from app.tasks import TaskRegistry

# A batch in one of these states is mid-run; any other state is an outcome.
ACTIVE_STATUSES = {BatchStatus.PENDING, BatchStatus.PROCESSING, BatchStatus.ACTIVATING}
WS_SAMPLE_SECONDS = 0.25

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

router = APIRouter()

CSV_FILE = File(..., description="CSV with header name,address,phone (phone optional)")


def _parse_upload(request: Request, file: UploadFile) -> List[HospitalRow]:
    # Same parser for validate and bulk create, so the two cannot disagree.
    # file.file is the spooled upload; the body cap keeps it in memory, so
    # reading it synchronously doesn't block the event loop on disk I/O.
    max_rows = request.app.state.settings.max_rows
    return parse_hospital_csv(file.file, max_rows=max_rows)


async def _run_batch(request: Request, record: BatchRecord, wait: bool = True) -> BulkResult:
    """Run (or resume) a batch and return its result.

    The work runs in a registry-tracked task, awaited through asyncio.shield:
    if the client disconnects and this handler is cancelled, the shield
    absorbs the cancellation and the batch carries on. The client can poll
    GET /hospitals/bulk/{batch_id} for the outcome.

    With wait=False the handler returns as soon as the run has started, and
    the client follows it over the WebSocket (or by polling).
    """
    app = request.app
    task = app.state.tasks.spawn(app.state.processor.run(record))
    try:
        if wait:
            await asyncio.shield(task)
        else:
            # asyncio runs ready callbacks in order, so the new task runs
            # before this handler resumes. By then it has either taken the
            # batch lock (run() doesn't await before that) or failed its
            # busy/runnable checks, which surface here as 409s.
            await asyncio.sleep(0)
            if task.done():
                task.result()
    except BatchBusyError:
        raise HTTPException(409, "Batch is already being processed")
    except BatchNotRunnableError:
        raise HTTPException(409, f"Batch is {record.status.value}; nothing to resume")
    return BulkResult.from_record(record)


def _get_record(request: Request, batch_id: uuid.UUID) -> BatchRecord:
    record = request.app.state.store.get(str(batch_id))
    if record is None:
        raise HTTPException(404, "Unknown batch_id (batches are kept in memory only)")
    return record


WAIT_QUERY = Query(True, description="false: return at once (202) and follow the batch "
                                      "via GET or the WebSocket")


@router.post(
    "/hospitals/bulk",
    response_model=BulkResult,
    responses={202: {"model": BulkResult, "description": "Started (wait=false)"},
               413: {"description": "Upload too large"},
               422: {"model": CSVErrorResponse, "description": "Invalid CSV"}},
)
async def bulk_create(request: Request, response: Response, file: UploadFile = CSV_FILE,
                      wait: bool = WAIT_QUERY) -> BulkResult:
    """Validate the CSV, create every hospital under a new batch ID, then
    activate the batch. Waits for the outcome unless wait=false; check
    `status` and `batch_activated` to see whether it succeeded."""
    rows = _parse_upload(request, file)
    record = request.app.state.processor.create_batch(rows)
    if not wait:
        response.status_code = 202
    return await _run_batch(request, record, wait)


@router.post("/hospitals/bulk/validate", response_model=ValidationResult)
async def validate_csv(request: Request, file: UploadFile = CSV_FILE) -> ValidationResult:
    """Check a CSV with exactly the rules bulk create uses, without creating anything."""
    try:
        rows = _parse_upload(request, file)
    except CSVValidationError as exc:
        return ValidationResult(
            valid=False, total_hospitals=0, hospitals=[],
            errors=[RowErrorOut.from_error(e) for e in exc.errors],
        )
    return ValidationResult(
        valid=True, total_hospitals=len(rows), errors=[],
        hospitals=[ValidatedRow.from_row(r) for r in rows],
    )


@router.get("/hospitals/bulk/{batch_id}", response_model=BulkResult)
async def get_batch(request: Request, batch_id: uuid.UUID) -> BulkResult:
    """Current state of a batch, including progress while it is running."""
    return BulkResult.from_record(_get_record(request, batch_id))


@router.post(
    "/hospitals/bulk/{batch_id}/resume",
    response_model=BulkResult,
    responses={202: {"model": BulkResult, "description": "Started (wait=false)"},
               409: {"description": "Batch is running or already completed"}},
)
async def resume_batch(request: Request, response: Response, batch_id: uuid.UUID,
                       wait: bool = WAIT_QUERY) -> BulkResult:
    """Retry a failed batch: create only the rows that are missing, reconcile,
    and activate. Rows that already exist are never sent again."""
    result = await _run_batch(request, _get_record(request, batch_id), wait)
    if not wait:
        response.status_code = 202
    return result


@router.websocket("/hospitals/bulk/{batch_id}/ws")
async def batch_updates(websocket: WebSocket, batch_id: uuid.UUID) -> None:
    """Push the batch's state (a BulkResult) whenever it changes, and once more
    when the run is over, then close. Unknown batches close with code 4404."""
    await websocket.accept()
    record = websocket.app.state.store.get(str(batch_id))
    if record is None:
        await websocket.close(code=4404, reason="Unknown batch_id")
        return

    # Batch state is plain in-memory data updated by the processor, so we
    # sample it rather than threading change notifications through the
    # workflow. Elapsed time alone doesn't count as a change.
    last = None
    try:
        while True:
            result = BulkResult.from_record(record).model_dump(mode="json")
            done = record.status not in ACTIVE_STATUSES and not record.lock.locked()
            snapshot = {**result, "processing_time_seconds": None}
            if snapshot != last or done:
                await websocket.send_json(result)
                last = snapshot
            if done:
                break
            await asyncio.sleep(WS_SAMPLE_SECONDS)
        await websocket.close()
    except WebSocketDisconnect:
        pass


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/docs")


async def _csv_error_handler(request: Request, exc: CSVValidationError) -> JSONResponse:
    body = CSVErrorResponse(
        detail="CSV validation failed",
        errors=[RowErrorOut.from_error(e) for e in exc.errors],
    )
    return JSONResponse(status_code=422, content=body.model_dump())


def create_app(
    cfg: Settings = settings,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> FastAPI:
    """Build the app. `transport` lets tests swap the directory API for a fake."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # One HTTP client for the app's lifetime: connections are pooled and
        # reused across rows and batches instead of reopened per request.
        http = httpx.AsyncClient(
            base_url=cfg.directory_api_url,
            timeout=httpx.Timeout(cfg.read_timeout, connect=cfg.connect_timeout),
            limits=httpx.Limits(max_connections=cfg.max_concurrency,
                                max_keepalive_connections=cfg.max_concurrency),
            transport=transport,
        )
        store = BatchStore(max_batches=cfg.max_stored_batches)
        client = DirectoryClient(http, max_attempts=cfg.max_attempts,
                                 retry_backoff_seconds=cfg.retry_backoff_seconds,
                                 max_concurrency=cfg.max_concurrency)
        app.state.settings = cfg
        app.state.store = store
        app.state.processor = BatchProcessor(client, store, cfg.cold_start_timeout)
        app.state.tasks = TaskRegistry()
        try:
            yield
        finally:
            # Order matters: running batches still need the HTTP client.
            await app.state.tasks.shutdown(cfg.shutdown_grace_seconds)
            await http.aclose()

    app = FastAPI(
        title="Hospital Bulk Processor",
        description="Bulk-creates hospitals from a CSV via the Hospital Directory API.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=cfg.max_upload_bytes)
    if cfg.cors_origins:
        # Added last so it is outermost: 413s from the size limit get CORS
        # headers too, and the browser can show them instead of a CORS error.
        app.add_middleware(CORSMiddleware, allow_origins=list(cfg.cors_origins),
                           allow_methods=["GET", "POST"], allow_headers=["*"])
    app.add_exception_handler(CSVValidationError, _csv_error_handler)
    app.include_router(router)
    return app


app = create_app()
