# Hospital Bulk Processor: web UI

React + TypeScript + Vite + Tailwind CSS, with shadcn/ui-style components and
Lucide icons. It talks to the FastAPI backend in the parent directory.

## Run

```bash
cp .env.example .env   # point VITE_API_URL at the backend
npm install
npm run dev            # http://localhost:5173
npm run build          # type-check and build to dist/
```

The backend must allow the UI's origin (`CORS_ORIGINS`, which defaults to the
Vite dev server).

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_URL` | `http://localhost:8000` | Backend base URL |
| `VITE_MAX_ROWS` | `20` | Shown in the guide; keep in sync with `MAX_CSV_ROWS` |
| `VITE_MAX_UPLOAD_BYTES` | `65536` | Checked before upload; keep in sync with `MAX_UPLOAD_BYTES` |

## How it works

- **Upload**: the file is checked locally (extension, size), then sent to
  `POST /hospitals/bulk/validate`. Processing is only possible once the
  backend says the file is valid.
- **Processing**: `POST /hospitals/bulk?wait=false` returns the batch ID at
  once. The batch page follows it over the WebSocket
  `/hospitals/bulk/{id}/ws`, which pushes every change and closes when the run
  ends. If the connection drops, it reconnects every 3 s. A finished batch costs
  one message and no polling.
- **Resume**: shown only for `creation_failed`, `reconciliation_failed` and
  `activation_failed`. It calls `POST /…/resume?wait=false` and reconnects the
  socket. The button is disabled while the request is in flight, and the backend
  returns 409 for duplicates.
- **History**: the backend has no "list batches" endpoint, so this browser
  keeps the IDs it started in `localStorage`. Batches the server has forgotten
  (in-memory store, lost on restart) show as "No longer available". Dashboard
  totals are summed from these batches, never invented.

## Layout

```
src/
  lib/api.ts         every backend call, plus ApiError with readable messages
  lib/types.ts       response types (mirror app/schemas.py)
  lib/history.ts     localStorage list of batches
  lib/status.ts      status labels and colours
  hooks/             useBatch (WebSocket), useHistoryBatches, useHealth
  components/ui/     button, card, badge, table, progress, skeleton, confirm dialog
  components/        dropzone, validation report, rows table, batch list, …
  pages/             Dashboard, Upload, BatchDetail, History
```
