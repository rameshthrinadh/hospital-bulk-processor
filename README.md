# Hospital Bulk Processor

A FastAPI service that bulk-creates hospitals from a CSV upload through the
[Hospital Directory API](https://hospital-directory.onrender.com/docs). It
validates the CSV, creates every row under a fresh batch ID with bounded
concurrency, verifies the batch against the directory, and activates it only
when every row was created.

**Live URL:** _to be added once deployed_ (see [Deploying to Render](#deploying-to-render))

- [Quick start](#quick-start)
- [API](#api)
- [How it works](#how-it-works)
- [Error handling, retries and resume](#error-handling-retries-and-resume)
- [Consistency guarantees](#consistency-guarantees)
- [Configuration](#configuration)
- [Testing](#testing)
- [Docker](#docker)
- [Deploying to Render](#deploying-to-render)
- [Design decisions and trade-offs](#design-decisions-and-trade-offs)

## Quick start

```bash
# Docker: web UI and API together, no local Python or Node needed
docker compose up --build
# -> http://localhost:8000       web UI
# -> http://localhost:8000/docs  interactive API docs

# or the API locally, with Python 3.12 (/ then redirects to /docs)
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --port 8000
```

```bash
cat > hospitals.csv <<'EOF'
name,address,phone
General Hospital,123 Main St,555-1234
City Clinic,"45 Side St, Suite 2",
EOF

curl -F file=@hospitals.csv http://localhost:8000/hospitals/bulk
```

**Web UI in development** (in a second terminal, with Node 20+; hot reload):

```bash
cd frontend
cp .env.example .env    # VITE_API_URL=http://localhost:8000
npm install
npm run dev             # -> http://localhost:5173
```

See [frontend/README.md](frontend/README.md).

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/hospitals/bulk` | Upload a CSV (form field `file`). Creates, verifies and activates the batch, waits for the outcome and returns it. With `?wait=false`, returns **202** as soon as the batch has started. |
| `POST` | `/hospitals/bulk/validate` | Check a CSV with exactly the same rules. Creates nothing. |
| `GET` | `/hospitals/bulk/{batch_id}` | Current state of a batch. Poll it for progress while a batch runs. |
| `POST` | `/hospitals/bulk/{batch_id}/resume` | Retry a failed batch: create only the missing rows, then verify and activate. Also takes `?wait=false`. |
| `WS` | `/hospitals/bulk/{batch_id}/ws` | Live progress: pushes the batch (same shape as `GET`) on connect and on every change, then closes (code 1000) when the run is over. Unknown batch: closes with 4404. |
| `GET` | `/health` | Liveness check, used by Docker and Render. |
| `GET` | `/docs` | Swagger UI. `/` serves the web UI when it is built in (Docker), otherwise redirects here. |

### CSV format

- Header row required: `name,address,phone`. Columns can be in any order and
  are case-insensitive. `phone` is optional, both as a column and per row.
- At most **20 rows**, UTF-8 (an Excel BOM is fine), request body at most 64 KiB.
- Values are trimmed. Blank lines are ignored. Quoted fields may contain commas.
- **All-or-nothing:** if any row is invalid (missing name or address, wrong
  field count, unknown column, two identical rows), the whole file is rejected
  with every problem listed, and nothing is created.

### `POST /hospitals/bulk`: success

The response keeps every field of the assignment's format and adds `status`
and `error` at batch and row level, plus `created_at` and `updated_at` (end of
the latest run) on the batch.

```json
{
  "batch_id": "941cc42b-9d5d-4e6a-a378-05d157ed36cf",
  "total_hospitals": 2,
  "processed_hospitals": 2,
  "failed_hospitals": 0,
  "processing_time_seconds": 6.75,
  "batch_activated": true,
  "hospitals": [
    {"row": 1, "hospital_id": 1741, "name": "General Hospital", "status": "created_and_activated", "error": null},
    {"row": 2, "hospital_id": 1742, "name": "City Clinic", "status": "created_and_activated", "error": null}
  ],
  "status": "completed",
  "error": null,
  "created_at": "2026-11-13T16:22:37.859394Z",
  "updated_at": "2026-11-13T16:22:44.611020Z"
}
```

- `processed_hospitals` counts rows that exist on the directory API.
- `failed_hospitals` counts rows that could not be created.
- `processing_time_seconds` is the duration of the latest run. While a batch is
  running, it is the time elapsed so far.

### Partial failure (illustrative)

```json
{
  "batch_id": "…",
  "total_hospitals": 3,
  "processed_hospitals": 2,
  "failed_hospitals": 1,
  "processing_time_seconds": 5.91,
  "batch_activated": false,
  "hospitals": [
    {"row": 1, "hospital_id": 1801, "name": "A", "status": "created", "error": null},
    {"row": 2, "hospital_id": null, "name": "B", "status": "failed", "error": "HTTP 500: Internal Server Error"},
    {"row": 3, "hospital_id": 1802, "name": "C", "status": "created", "error": null}
  ],
  "status": "creation_failed",
  "error": "1 of 3 hospitals could not be created"
}
```

The batch stays **inactive**. `POST /hospitals/bulk/{batch_id}/resume`
retries only row 2, then verifies and activates.

### Invalid CSV: HTTP 422

```json
{
  "detail": "CSV validation failed",
  "errors": [
    {"row": 1, "message": "'address' is required"},
    {"row": 3, "message": "Duplicate of row 2"}
  ]
}
```

`row` is `null` for file-level problems (header, encoding, too many rows).
`/hospitals/bulk/validate` returns the same `errors` list with HTTP 200 and
`"valid": false`.

### Status values

| Batch `status` | Meaning | Batch active? |
|---|---|---|
| `pending` | Created, not started yet | No |
| `processing` | Creating hospitals and verifying the batch | No |
| `activating` | Activation in progress | No |
| `completed` | Every row created, batch verified, activated count matched | **Yes** |
| `creation_failed` | One or more rows could not be created (see row `error`) | No |
| `reconciliation_failed` | Couldn't verify the batch, or it holds records we didn't create | No |
| `activation_failed` | Every row created, but activation failed or activated the wrong count | No\* |

\* Except when the activated count was wrong: the directory API then did
activate the batch. See [Consistency guarantees](#consistency-guarantees).

Row `status`: `pending`, `created` (exists on the directory, inactive),
`created_and_activated`, `failed`.

### HTTP status codes

| Code | When |
|---|---|
| 200 | The batch was processed, **whatever the outcome**. Check `status` and `batch_activated`. |
| 202 | `wait=false`: the batch has started; follow it via the WebSocket or `GET` |
| 404 | Unknown `batch_id` (batches are kept in memory only) |
| 409 | Resume of a batch that is running right now, or already completed |
| 413 | Request body over 64 KiB |
| 422 | Invalid CSV, missing `file` field, or malformed `batch_id` |

## How it works

```
               ┌──────────────────────── Bulk Processor (this service, 1 process) ───────────────────────┐
 CSV upload ──▶│ BodySizeLimitMiddleware ─▶ csv_parser ─▶ BatchProcessor ─────────▶ DirectoryClient ─────│──▶ Hospital
               │  (413 while streaming)     (stops at     create ▸ reconcile ▸      (shared semaphore,   │    Directory
               │                             row 21)      activate                 timeouts, retries,   │    API
               │                                             │                     error classification)│
               │                                        BatchStore (in memory) ◀── GET /hospitals/bulk/{id}
               │                                        TaskRegistry (keeps runs alive past disconnects)  │
               └──────────────────────────────────────────────────────────────────────────────────────────┘
```

| Module | Responsibility |
|---|---|
| [app/main.py](app/main.py) | Routes, app lifespan (shared HTTP client, shutdown), wiring |
| [app/csv_parser.py](app/csv_parser.py) | Streaming CSV parsing and validation, shared by validate and bulk |
| [app/limits.py](app/limits.py) | Request size limit, enforced while the body streams in |
| [app/processor.py](app/processor.py) | The workflow: create → reconcile → activate. Resume is the same run. |
| [app/directory_client.py](app/directory_client.py) | Directory API calls, error classification, safe create and activate |
| [app/store.py](app/store.py) | In-memory batch and row state, per-batch lock |
| [app/tasks.py](app/tasks.py) | Strong references to running batches, drained on shutdown |
| [app/schemas.py](app/schemas.py) | Response models |

### Workflow of one batch run

1. **Validate** the whole CSV (all-or-nothing) and generate a batch UUID.
2. **Warm up**: one `GET /` to the directory API with a 60 s timeout. Its
   free-tier host sleeps when idle, and this stops 20 parallel creates from
   all timing out on a cold start.
3. **Create** every row that has no hospital yet via `POST /hospitals/` with
   the batch ID, in parallel, at most `DIRECTORY_MAX_CONCURRENCY` (10) requests
   at once. Each row's result is recorded as it arrives, which is what the
   progress endpoint shows.
4. **Reconcile.** List the batch on the directory and make it agree with our rows:
   - each row's hospital must be present with matching fields;
   - a row whose create "failed" ambiguously adopts the matching record if it actually landed;
   - exact duplicates left over from retries are deleted by ID;
   - anything else blocks activation.
5. If any row failed → `creation_failed`. Stop, inactive.
6. **Activate** via `PATCH /hospitals/batch/{id}/activate`. Report `completed`
   only if the activated count equals the number of rows.

Resume runs the same steps. Rows that already have a hospital skip step 3.

### Performance

The directory API takes about 5.3 s per create, but handles parallel requests
well. Ten concurrent creates finished in about 6 s.

| | 20 rows |
|---|---|
| Sequential (estimated: 20 × 5.3 s) | ~106 s |
| This service, concurrency 10 (**measured**, live test) | **12.1 s** |

The concurrency limit is one `asyncio.Semaphore` inside the shared client. It
caps outbound requests across **all** batches, so many simultaneous uploads
can't overload the directory API. The HTTP connection pool is sized to match,
and connections are reused across rows and batches.

## Error handling, retries and resume

Every directory API failure is classified, because the safe response differs
in each case:

| Kind | Examples | What we do |
|---|---|---|
| **Permanent** | 4xx such as 422 validation | Fail the row immediately; no retry |
| **Retryable** | Connection refused or connect timeout (request never sent), 429 | Retry with exponential backoff (1 s, 2 s) |
| **Ambiguous** | Read timeout, 5xx, dropped connection: the create *may have happened* | Before retrying, list the batch and adopt a matching record if one exists. Retry only if none does. |

A timeout is never treated as "the create failed". It means "we don't know".
Up to 3 attempts per call (`DIRECTORY_MAX_ATTEMPTS`). Timeouts: 5 s connect,
20 s read.

**Activation** is checked the same way. If activation times out, or a retry
gets a 400 "already active", we read the batch back. All hospitals active with
the expected count means success, even though we never saw the 200.

**Resume** (`POST /hospitals/bulk/{batch_id}/resume`) is allowed from
`creation_failed`, `reconciliation_failed` and `activation_failed`:

- It never re-sends rows that already exist.
- A batch that is running returns **409** immediately; resumes don't queue.
  Each batch has an `asyncio.Lock`, and the "is it locked?" check and taking
  the lock happen with no `await` in between, so two requests can't both get in.
- A completed batch returns 409.

**Client disconnects:** the batch runs in a tracked task awaited through
`asyncio.shield`. If the client goes away, the request handler is cancelled
but the batch finishes. Poll `GET /hospitals/bulk/{batch_id}` for the result.

**Shutdown** (SIGTERM):
1. Uvicorn stops accepting connections and gives open requests up to 20 s to
   finish. A batch in progress normally completes and returns its result.
2. The app then gives any batch still running `SHUTDOWN_GRACE_SECONDS` (5 s).
3. After that, the batch is cancelled and recorded as failed. It is never left
   half-activated.

The total stays under Render's 30 s SIGKILL deadline. This was verified in
Docker: SIGTERM 2 s into a 20-row batch, client still received `completed`,
container exited after 10 s.

## Consistency guarantees

The directory API has **no idempotency keys** and does not deduplicate creates.
Without that, no client can guarantee exactly-once creation. This service is
**at-least-once with best-effort duplicate cleanup**.

**What is guaranteed**

- Activation is requested only after reconciliation confirms that every CSV
  row maps to exactly one hospital in the batch and the batch holds nothing
  else. A batch with any failed row is never activated by this service.
- `completed` is reported only when the activated count equals the number of
  CSV rows: either the API's `activated_count`, or a read-back of the batch
  showing exactly that many hospitals, all active.
- Rows already created are never re-sent on resume.
- Records are deleted **only by ID**, and only when all of these hold:
  - they are in our batch, whose UUID only this service knows;
  - they exactly match a CSV row on name, address *and* phone;
  - they are not the record that row already claimed.

  A record that matches by name alone is never deleted. Any unexpected record
  blocks activation (`reconciliation_failed`) and is left untouched.

**The race we cannot close**

A create that timed out on our side can still be in flight on the server when
we reconcile, and land afterwards:

- **Lands before activation.** Activation includes it, so `activated_count`
  exceeds the row count. We report `activation_failed`. The directory API has
  at that point activated the batch, including the extra record. Resuming
  deletes the exact duplicate by ID and re-verifies by read-back, reaching
  `completed`.
- **Lands after activation.** It stays an inactive extra in the batch. We never
  see it, and the batch still reports `completed`.

Both need a lost response *and* a server that finishes the request after our
retry (20 s read timeout vs ~5 s typical latency), so they should be rare, but
they are possible. Closing the gap needs support on the API side, such as an
idempotency key or a unique constraint per batch.

**In-memory state**

- Batch state lives in process memory. A restart, redeploy or free-tier
  spin-down loses it: `GET` and `resume` for older batches return 404.
- The hospitals themselves remain on the directory API. A batch that was
  created but not activated stays **inactive** there. Activate or delete it
  directly with `PATCH` or `DELETE /hospitals/batch/{batch_id}`.
- Because state isn't shared, the service must run as **exactly one process**.
  Multiple workers or instances would each have their own store and locks: a
  poll or resume could land on a process that has never heard of the batch,
  and two resumes could run at once. The Dockerfile pins `--workers 1` and
  `render.yaml` pins `numInstances: 1`.
- The store keeps the most recent 1,000 batches (`MAX_STORED_BATCHES`) and
  never drops one that is running.

Scaling beyond one process would mean moving batch state and the per-batch
lock into a shared store (e.g. Redis or Postgres, with a row lock or lease).
That is deliberately out of scope here.

## Configuration

Everything is configured through environment variables, read at startup.
Invalid values (e.g. `DIRECTORY_MAX_CONCURRENCY=0`) stop the app from starting.

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8000` (Docker) | Port to bind. Render injects its own. |
| `DIRECTORY_API_URL` | `https://hospital-directory.onrender.com` | Directory API base URL |
| `DIRECTORY_MAX_CONCURRENCY` | `10` | Max concurrent requests to the directory API, across all batches |
| `DIRECTORY_CONNECT_TIMEOUT` | `5` | Seconds to establish a connection |
| `DIRECTORY_READ_TIMEOUT` | `20` | Seconds to wait for a response (creates take ~5 s) |
| `DIRECTORY_MAX_ATTEMPTS` | `3` | Attempts per create or activate call |
| `DIRECTORY_RETRY_BACKOFF` | `1` | Base backoff in seconds (doubles each retry) |
| `DIRECTORY_COLD_START_TIMEOUT` | `60` | Timeout for the warm-up request |
| `MAX_CSV_ROWS` | `20` | Row limit (from the assignment) |
| `MAX_UPLOAD_BYTES` | `65536` | Request body limit |
| `SHUTDOWN_GRACE_SECONDS` | `5` | Extra wait for running batches at shutdown, after uvicorn's 20 s |
| `MAX_STORED_BATCHES` | `1000` | Batches kept in memory |
| `FRONTEND_DIR` | unset (`/app/frontend` in Docker) | Built web UI to serve at `/`. Unset: `/` redirects to `/docs`. |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated browser origins allowed to call the API (the frontend). Empty disables CORS. |
| `LOG_LEVEL` | `INFO` | Log level |

## Testing

```bash
.venv/bin/pip install -r requirements-dev.txt

.venv/bin/pytest            # 99 unit + API tests: no network, ~3 s
.venv/bin/pytest -m live    # 5 integration tests against the real directory API, ~40 s
```

**Default suite.** It runs against an in-memory fake of the directory API
([tests/fake_directory.py](tests/fake_directory.py)), plugged into the real
HTTP client through `httpx.MockTransport`. The fake mirrors the live API's
observed behaviour (no deduplication, 400 on re-activation, 404 for an empty
batch) and can inject faults per hospital: timeouts, 5xx, connection errors,
and *lost responses* (the create succeeds but the response never arrives).

It covers:

| Area | Cases |
|---|---|
| CSV input | Valid CSVs, invalid CSVs, missing fields, row and byte limits (including chunked uploads and lazy reading) |
| Upstream failures | Timeouts, partial failures, activation failures, activated-count mismatches |
| Duplicates | Recovery after lost responses; deletion by ID with no name-only matches |
| Concurrency | Concurrent uploads sharing one limit, simultaneous resumes (one 200, one 409) |
| Lifecycle | Client disconnects, shutdown within and beyond the grace period, progress polling |

**Live suite** ([tests/test_live.py](tests/test_live.py)). It is excluded by
default and runs only with `-m live`. Set `DIRECTORY_API_URL` to target
another deployment.

What it does:
- checks the API contract the client relies on;
- runs end-to-end uploads, including 20 rows (asserting it finishes in under 60 s);
- runs concurrent uploads;
- confirms an invalid CSV creates nothing.

How it stays safe:
- Every test hospital is named `PARIBUS-LIVE-TEST <run-id> …`, inside a batch
  UUID generated for that run.
- Cleanup runs in a `finally` block and deletes only those batch IDs.
- Before deleting, cleanup checks that every record in the batch carries the
  run's marker. If any doesn't, it refuses and fails the test.
- Unrelated records are never touched.

## Docker

```bash
docker compose up --build          # build and run on http://localhost:8000
HOST_PORT=9000 docker compose up   # different host port
docker compose down

# or plain Docker, the way Render runs it
docker build -t hospital-bulk-processor .
docker run --rm -p 10000:10000 -e PORT=10000 hospital-bulk-processor
```

What the image does, and why:

- **Two stages.** A `node:22-slim` stage builds the web UI. The final
  `python:3.12-slim` image gets only the built files (no Node), and the app
  serves them at `/`. One process and one port serve both the UI and the API,
  so the UI needs no API URL and no CORS.
- **Dependencies are installed before the code is copied.** The dependency
  layer is cached and rebuilt only when `requirements.txt` changes.
- **Runs as a non-root user** (`appuser`, uid 10001).
- **`.dockerignore`** keeps tests, the virtualenv, git metadata and docs out of
  the image.
- **`exec uvicorn …` makes uvicorn PID 1.** It receives SIGTERM directly and
  runs the graceful shutdown described above, instead of being killed by the
  shell.
- **`--workers 1` is explicit.** Uvicorn otherwise takes its worker count from
  `$WEB_CONCURRENCY` if the platform sets it.
- **A `HEALTHCHECK`** polls `/health`. Compose sets `stop_grace_period: 30s` to
  mirror Render.

## Deploying to Render

[render.yaml](render.yaml) is a Render Blueprint for one free Docker web
service that serves both the web UI (at `/`) and the API:

- `healthCheckPath: /health`;
- `numInstances: 1` and `WEB_CONCURRENCY=1`;
- `maxShutdownDelaySeconds: 30`;
- auto-deploy on every commit.

Render injects `PORT` (default 10000), and the container binds `0.0.0.0:$PORT`.

1. Push this repository to GitHub, GitLab or Bitbucket.
2. In Render: **New → Blueprint**, select the repository, and apply. Render
   reads `render.yaml`, builds the Dockerfile and starts the service.
3. Open `https://<service-name>.onrender.com/` for the web UI (its dashboard
   should show "Processing service · Online"), and `/docs` for the API.

Free-tier behaviour to expect:

- **Spin-down.** The service sleeps after 15 minutes without traffic. The next
  request takes about a minute while it starts again, and in-memory batch
  state is gone.
- **Restarts.** Render may restart free instances at any time, with the same
  effect. Hospitals already created stay on the directory API (see
  [In-memory state](#consistency-guarantees)).
- **Redeploys.** These send SIGTERM to the old instance, so graceful shutdown
  applies.

## Design decisions and trade-offs

- **FastAPI over Flask.** The work is almost entirely waiting on a slow
  external API. Native `async` lets one process run 10+ requests concurrently
  without threads, and FastAPI provides validation, upload handling and
  `/docs` out of the box.
- **The upload request waits for the result** (about 7–12 s for 20 rows),
  rather than returning 202 and asking the client to poll. That matches the
  assignment's expected response. Progress is still pollable, and the batch
  survives a disconnect.
- **All-or-nothing CSV validation.** A batch can only be activated if every
  row is created, so accepting the valid half of a bad file would only leave
  an inactive, half-filled batch behind.
- **Identical rows are rejected.** Recovering from a lost response means
  finding "our" record by its fields. Two identical rows would make that
  ambiguous.
- **Failed batches are kept for resume, not rolled back.** They stay inactive,
  which is what protects consumers, and resume finishes the job without
  re-sending rows that already exist. Rollback would also be another remote
  call that can fail.
- **Reconciliation against the directory**, rather than trusting our own
  record of what was created. The directory is the source of truth, and this
  is what catches lost responses and duplicates.
- **In-memory state, one process**, as the assignment allows. No Redis,
  Postgres or Celery: for 20-row batches they would add operational cost
  without changing the outcome. The README states the consequences instead.
- **200 for every processed batch, failures included.** The upload was
  accepted and processed; the outcome is in `status`. Clients must check
  `status`, not just the HTTP code.

## Project layout

```
app/                     application package (see "How it works")
tests/
  fake_directory.py      in-memory directory API with fault injection
  test_csv_parser.py     CSV parsing and validation
  test_directory_client.py  error classification, retries, recovery
  test_processor.py      workflow, reconciliation, concurrency, resume
  test_api.py            HTTP endpoints, limits, disconnects, shutdown
  test_async_api.py      wait=false, WebSocket progress, CORS, timestamps
  test_store.py, test_config.py
  test_live.py           real-API integration tests (pytest -m live)
Dockerfile, .dockerignore, docker-compose.yml, render.yaml
requirements.txt         runtime dependencies (pinned)
requirements-dev.txt     + test dependencies
frontend/                React web UI (separate Vite project)
```
