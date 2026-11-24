import asyncio
import dataclasses
from contextlib import asynccontextmanager

import httpx
import pytest

from app.config import settings
from app.main import create_app
from tests.fake_directory import FakeDirectory

SPEC_FIELDS = {"batch_id", "total_hospitals", "processed_hospitals", "failed_hospitals",
               "processing_time_seconds", "batch_activated", "hospitals"}
SPEC_ROW_FIELDS = {"row", "hospital_id", "name", "status"}


def csv_text(n=3):
    return "name,address,phone\n" + "".join(f"Hospital {i},{i} Main St,555-000{i}\n"
                                            for i in range(1, n + 1))


@asynccontextmanager
async def running_app(fake, **overrides):
    defaults = dict(directory_api_url="http://directory.test", retry_backoff_seconds=0,
                    shutdown_grace_seconds=5)
    cfg = dataclasses.replace(settings, **{**defaults, **overrides})
    app = create_app(cfg, transport=httpx.MockTransport(fake._handle))
    # Run the app's startup/shutdown (lifespan) around an in-process client.
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://bulk.test") as client:
            yield app, client


@pytest.fixture
def fake():
    return FakeDirectory()


@pytest.fixture
async def api(fake):
    async with running_app(fake) as (app, client):
        yield client


def upload(client, text, path="/hospitals/bulk"):
    return client.post(path, files={"file": ("hospitals.csv", text.encode(), "text/csv")})


def only_batch(app):
    (record,) = app.state.store._batches.values()
    return record


async def wait_for(predicate, timeout=3.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out waiting"
        await asyncio.sleep(0.01)


# ---- happy path and response format ---------------------------------------

async def test_valid_upload_creates_and_activates(api, fake):
    response = await upload(api, csv_text(3))
    assert response.status_code == 200
    body = response.json()

    assert SPEC_FIELDS <= set(body)
    assert body["status"] == "completed"
    assert body["batch_activated"] is True
    assert (body["total_hospitals"], body["processed_hospitals"], body["failed_hospitals"]) == (3, 3, 0)
    assert all(SPEC_ROW_FIELDS <= set(h) for h in body["hospitals"])
    assert [h["row"] for h in body["hospitals"]] == [1, 2, 3]
    assert all(h["status"] == "created_and_activated" for h in body["hospitals"])
    assert all(isinstance(h["hospital_id"], int) for h in body["hospitals"])
    assert all(h["active"] for h in fake.batch(body["batch_id"]))


async def test_get_batch_returns_same_result(api):
    created = (await upload(api, csv_text(2))).json()
    fetched = (await api.get(f"/hospitals/bulk/{created['batch_id']}")).json()
    assert fetched == created


# ---- invalid uploads --------------------------------------------------------

async def test_invalid_csv_is_rejected_without_calling_directory(api, fake):
    response = await upload(api, "name,phone\nA,555\n")
    assert response.status_code == 422
    assert response.json() == {
        "detail": "CSV validation failed",
        "errors": [{"row": None, "message": "Missing required column(s): address"}],
    }
    assert fake.calls == []


async def test_missing_required_fields_reported_per_row(api, fake):
    response = await upload(api, "name,address\nA,\n,2 St\nC,3 St\n")
    assert response.status_code == 422
    assert response.json()["errors"] == [
        {"row": 1, "message": "'address' is required"},
        {"row": 2, "message": "'name' is required"},
    ]
    assert fake.calls == []


async def test_more_than_20_rows_rejected(api, fake):
    response = await upload(api, csv_text(21))
    assert response.status_code == 422
    assert response.json()["errors"] == [
        {"row": None, "message": "Too many rows: maximum is 20 hospitals"},
    ]
    assert fake.calls == []


async def test_exactly_20_rows_accepted(api):
    response = await upload(api, csv_text(20))
    assert response.json()["status"] == "completed"


async def test_oversized_body_rejected_from_content_length(api, fake):
    big = "name,address\n" + "A," + "x" * (settings.max_upload_bytes + 1) + "\n"
    response = await upload(api, big)
    assert response.status_code == 413
    assert fake.calls == []


async def test_oversized_chunked_body_rejected_while_streaming(api, fake):
    # No Content-Length: the limit must be enforced as bytes arrive.
    boundary = "testboundary"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"h.csv\"\r\nContent-Type: text/csv\r\n\r\nname,address\r\n").encode()
    chunks_sent = 0

    async def body():
        nonlocal chunks_sent
        yield head
        for _ in range(100):  # 100 x 4 KiB, well past the 64 KiB limit
            chunks_sent += 1
            yield b"A,1 St\r\n" * 512

    response = await api.post(
        "/hospitals/bulk", content=body(),
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
    )
    assert response.status_code == 413
    assert chunks_sent < 100  # stopped reading early
    assert fake.calls == []


async def test_missing_file_field(api):
    response = await api.post("/hospitals/bulk", files={"other": ("x.csv", b"name,address\n")})
    assert response.status_code == 422


# ---- validate endpoint -----------------------------------------------------

async def test_validate_valid_csv(api, fake):
    response = await upload(api, csv_text(2), path="/hospitals/bulk/validate")
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["total_hospitals"] == 2
    assert body["hospitals"][0] == {"row": 1, "name": "Hospital 1",
                                    "address": "1 Main St", "phone": "555-0001"}
    assert fake.calls == []


@pytest.mark.parametrize("text", [
    "",
    "name,phone\nA,5\n",
    "name,address\nA,\n",
    "name,address\nA,1\nA,1\n",
    csv_text(21),
])
async def test_validate_and_bulk_report_identical_errors(api, fake, text):
    validated = (await upload(api, text, path="/hospitals/bulk/validate")).json()
    bulk = await upload(api, text)
    assert validated["valid"] is False
    assert bulk.status_code == 422
    assert validated["errors"] == bulk.json()["errors"]
    assert fake.calls == []


# ---- failures and resume ---------------------------------------------------

async def test_partial_failure_then_resume(api, fake):
    fake.create_faults["Hospital 2"] = ["422"]
    body = (await upload(api, csv_text(3))).json()

    assert body["status"] == "creation_failed"
    assert body["batch_activated"] is False
    assert (body["processed_hospitals"], body["failed_hospitals"]) == (2, 1)
    failed = body["hospitals"][1]
    assert failed["status"] == "failed" and failed["hospital_id"] is None
    assert "422" in failed["error"]
    assert not any(h["active"] for h in fake.batch(body["batch_id"]))

    resumed = await api.post(f"/hospitals/bulk/{body['batch_id']}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "completed"
    assert fake.calls.count("POST /hospitals/") == 4

    again = await api.post(f"/hospitals/bulk/{body['batch_id']}/resume")
    assert again.status_code == 409


async def test_activation_failure_reported_distinctly(api, fake):
    fake.activate_faults = ["500", "500", "500"]
    body = (await upload(api, csv_text(3))).json()
    assert body["status"] == "activation_failed"
    assert body["batch_activated"] is False
    assert (body["processed_hospitals"], body["failed_hospitals"]) == (3, 0)
    assert all(h["status"] == "created" for h in body["hospitals"])
    assert body["error"].startswith("Activation failed")


async def test_activation_count_mismatch_then_resume(api, fake):
    def late_duplicate(batch_id):
        fake.before_activate = None
        fake._create({"name": "Hospital 1", "address": "1 Main St", "phone": "555-0001",
                      "creation_batch_id": batch_id})

    fake.before_activate = late_duplicate
    body = (await upload(api, csv_text(2))).json()
    assert body["status"] == "activation_failed"
    assert body["batch_activated"] is False
    assert "activated 3 hospitals but 2 were expected" in body["error"]

    resumed = (await api.post(f"/hospitals/bulk/{body['batch_id']}/resume")).json()
    assert resumed["status"] == "completed"
    assert len(fake.batch(body["batch_id"])) == 2


async def test_timeouts_are_retried_transparently(api, fake):
    fake.create_faults["Hospital 1"] = ["timeout"]
    fake.create_faults["Hospital 2"] = ["lost_response"]
    body = (await upload(api, csv_text(3))).json()
    assert body["status"] == "completed"
    assert len(fake.batch(body["batch_id"])) == 3  # no duplicate from the lost response


async def test_unknown_and_malformed_batch_ids(api):
    unknown = "00000000-0000-4000-8000-000000000000"
    assert (await api.get(f"/hospitals/bulk/{unknown}")).status_code == 404
    assert (await api.post(f"/hospitals/bulk/{unknown}/resume")).status_code == 404
    assert (await api.get("/hospitals/bulk/not-a-uuid")).status_code == 422


# ---- concurrency -----------------------------------------------------------

async def test_concurrent_uploads_share_the_concurrency_limit(fake):
    fake.latency = 0.03
    async with running_app(fake, max_concurrency=4) as (app, client):
        responses = await asyncio.gather(*(upload(client, csv_text(8)) for _ in range(3)))
    bodies = [r.json() for r in responses]
    assert all(b["status"] == "completed" for b in bodies)
    assert len({b["batch_id"] for b in bodies}) == 3
    assert fake.max_in_flight == 4


async def test_progress_can_be_polled_while_processing(fake):
    fake.latency = 0.05
    async with running_app(fake, max_concurrency=2) as (app, client):
        request = asyncio.create_task(upload(client, csv_text(6)))
        await wait_for(lambda: len(app.state.store._batches) == 1)
        batch_id = only_batch(app).batch_id
        await wait_for(lambda: only_batch(app).processed > 0)

        progress = (await client.get(f"/hospitals/bulk/{batch_id}")).json()
        assert progress["status"] == "processing"
        assert 0 < progress["processed_hospitals"] < 6
        assert progress["processing_time_seconds"] > 0
        assert progress["batch_activated"] is False

        assert (await request).json()["status"] == "completed"


async def test_simultaneous_resumes_of_one_batch(fake):
    fake.activate_faults = ["500", "500", "500"]
    async with running_app(fake) as (app, client):
        batch_id = (await upload(client, csv_text(2))).json()["batch_id"]
        fake.latency = 0.05
        first, second = await asyncio.gather(
            client.post(f"/hospitals/bulk/{batch_id}/resume"),
            client.post(f"/hospitals/bulk/{batch_id}/resume"),
        )
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert sum(1 for c in fake.calls if c.startswith("PATCH")) == 4  # 3 failed + 1


# ---- disconnects and shutdown ----------------------------------------------

async def test_client_disconnect_does_not_abort_batch(fake):
    fake.latency = 0.05
    async with running_app(fake) as (app, client):
        request = asyncio.create_task(upload(client, csv_text(5)))
        await wait_for(lambda: fake.in_flight > 0)
        request.cancel()  # client goes away mid-batch; the handler is cancelled
        with pytest.raises(asyncio.CancelledError):
            await request

        assert len(app.state.tasks) == 1  # still strongly referenced
        record = only_batch(app)
        await wait_for(lambda: record.status.value == "completed")
        await wait_for(lambda: len(app.state.tasks) == 0)

        polled = (await client.get(f"/hospitals/bulk/{record.batch_id}")).json()
    assert polled["status"] == "completed"
    assert polled["batch_activated"] is True


async def test_shutdown_waits_for_running_batch_within_grace(fake):
    fake.latency = 0.05
    async with running_app(fake, shutdown_grace_seconds=5) as (app, client):
        request = asyncio.create_task(upload(client, csv_text(3)))
        await wait_for(lambda: fake.in_flight > 0)
        request.cancel()
        record = only_batch(app)
    # Leaving the context ran shutdown, which waited for the batch.
    assert record.status.value == "completed"


async def test_shutdown_cancels_batch_after_grace_and_leaves_it_inactive(fake):
    fake.latency = 0.5
    async with running_app(fake, shutdown_grace_seconds=0.05) as (app, client):
        request = asyncio.create_task(upload(client, csv_text(3)))
        await wait_for(lambda: fake.in_flight > 0)
        request.cancel()
        record = only_batch(app)
    assert record.status.value == "creation_failed"
    assert "interrupted" in record.error
    assert record.activated is False
    assert not any(c.startswith("PATCH") for c in fake.calls)
    assert not record.lock.locked()
