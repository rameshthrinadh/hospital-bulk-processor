"""Integration tests against the real Hospital Directory API.

Excluded from the default run. Run with:  pytest -m live
(Override the target with DIRECTORY_API_URL.)

Safety rules these tests follow:
  - Every hospital they create is named "PARIBUS-LIVE-TEST <run id> ..." and
    belongs to a batch whose UUID was generated for this run.
  - Cleanup deletes only by those batch IDs, and first checks that every
    record in the batch carries this run's marker; if any doesn't, it refuses
    to delete and fails loudly instead.
  - Cleanup always runs, in a `finally` block.
"""

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from typing import Iterable, List

import httpx
import pytest

from app.config import settings
from app.csv_parser import HospitalRow
from app.directory_client import DirectoryClient
from app.main import create_app

pytestmark = pytest.mark.live

RUN_ID = uuid.uuid4().hex[:8]
MARKER = f"PARIBUS-LIVE-TEST {RUN_ID}"


def live_http(timeout: float = 60.0) -> httpx.AsyncClient:
    # Generous timeout: the free-tier directory API can take ~1 minute to wake.
    return httpx.AsyncClient(base_url=settings.directory_api_url, timeout=timeout)


def live_csv(n: int) -> str:
    return "name,address,phone\n" + "".join(
        f"{MARKER} Hospital {i},{i} Integration Way,555-{i:04d}\n" for i in range(1, n + 1)
    )


async def cleanup(batch_ids: Iterable[str]) -> None:
    async with live_http() as http:
        for batch_id in batch_ids:
            response = await http.get(f"/hospitals/batch/{batch_id}")
            if response.status_code == 404:
                continue  # nothing was created
            response.raise_for_status()
            foreign = [h["id"] for h in response.json() if not h["name"].startswith(MARKER)]
            if foreign:
                pytest.fail(f"Refusing to delete batch {batch_id}: records {foreign} "
                            f"are not from this test run")
            deleted = await http.delete(f"/hospitals/batch/{batch_id}")
            assert deleted.status_code in (200, 404), deleted.text


@asynccontextmanager
async def live_app():
    """The real app wired to the real directory API, with lifespan running.
    Every batch the app created is cleaned up afterwards, even on failure."""
    app = create_app(settings)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://bulk.test", timeout=300) as client:
                yield app, client
    finally:
        store = getattr(app.state, "store", None)
        await cleanup(list(store._batches) if store else [])


def upload(client: httpx.AsyncClient, text: str, path: str = "/hospitals/bulk"):
    return client.post(path, files={"file": ("live.csv", text.encode(), "text/csv")})


async def batch_on_directory(batch_id: str) -> List[dict]:
    async with live_http() as http:
        response = await http.get(f"/hospitals/batch/{batch_id}")
        response.raise_for_status()
        return response.json()


# ---- API contract -----------------------------------------------------------

async def test_directory_api_contract_matches_our_assumptions():
    """The behaviours the client's error handling depends on."""
    batch_id = str(uuid.uuid4())
    row = HospitalRow(1, f"{MARKER} Contract", "1 Contract St", None)
    try:
        async with live_http() as http:
            client = DirectoryClient(http)
            assert await client.list_batch(batch_id) == []  # 404 -> empty

            created = await client.create_hospital(row, batch_id)
            assert created["creation_batch_id"] == batch_id
            assert created["active"] is False
            assert created["phone"] is None  # identity matching relies on this

            assert await client.activate_batch_safely(batch_id, expected_count=1) == 1
            # A second activation is a 400 "already active"; the safe path
            # must read back and report success, not fail.
            again = await http.patch(f"/hospitals/batch/{batch_id}/activate")
            assert again.status_code == 400
            assert await client.activate_batch_safely(batch_id, expected_count=1) == 1
    finally:
        await cleanup([batch_id])


# ---- end-to-end through our API --------------------------------------------

async def test_bulk_upload_end_to_end():
    async with live_app() as (app, client):
        response = await upload(client, live_csv(3))
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["status"] == "completed", body
        assert body["batch_activated"] is True
        assert (body["processed_hospitals"], body["failed_hospitals"]) == (3, 0)

        on_directory = await batch_on_directory(body["batch_id"])
        assert {h["id"] for h in on_directory} == {h["hospital_id"] for h in body["hospitals"]}
        assert all(h["active"] for h in on_directory)

        polled = (await client.get(f"/hospitals/bulk/{body['batch_id']}")).json()
        assert polled["status"] == "completed"


async def test_twenty_rows_processed_concurrently():
    async with live_app() as (app, client):
        # Wake the directory API first so a cold start doesn't skew timing.
        async with live_http() as http:
            await http.get("/")
        started = time.monotonic()
        body = (await upload(client, live_csv(20))).json()
        elapsed = time.monotonic() - started

        assert body["status"] == "completed", body
        assert len(await batch_on_directory(body["batch_id"])) == 20
        # Each create takes ~5s; sequentially 20 rows would take ~100s.
        print(f"\n20 rows processed in {elapsed:.1f}s")
        assert elapsed < 60


async def test_invalid_csv_creates_nothing():
    async with live_app() as (app, client):
        response = await upload(client, f"name,address\n{MARKER} A,\n")
        assert response.status_code == 422
        assert app.state.store._batches == {}


async def test_concurrent_uploads():
    async with live_app() as (app, client):
        responses = await asyncio.gather(*(upload(client, live_csv(4)) for _ in range(2)))
        bodies = [r.json() for r in responses]
        assert all(b["status"] == "completed" for b in bodies), bodies
        assert bodies[0]["batch_id"] != bodies[1]["batch_id"]
