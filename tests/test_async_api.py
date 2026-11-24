"""wait=false starts, the WebSocket progress stream, CORS and timestamps."""

import asyncio
import dataclasses

import httpx
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import settings
from app.main import create_app
from tests.fake_directory import FakeDirectory
from tests.test_api import api, csv_text, fake, upload  # noqa: F401


def make_client(fake, **overrides):
    defaults = dict(directory_api_url="http://directory.test", retry_backoff_seconds=0)
    cfg = dataclasses.replace(settings, **{**defaults, **overrides})
    return TestClient(create_app(cfg, transport=httpx.MockTransport(fake._handle)))


def post_csv(client, text, path="/hospitals/bulk?wait=false"):
    return client.post(path, files={"file": ("hospitals.csv", text.encode(), "text/csv")})


def stream(client, batch_id):
    """Every message the WebSocket sends until the server closes it."""
    messages = []
    with client.websocket_connect(f"/hospitals/bulk/{batch_id}/ws") as ws:
        try:
            while True:
                messages.append(ws.receive_json())
        except WebSocketDisconnect as exc:
            assert exc.code == 1000
    return messages


async def test_wait_false_returns_at_once_and_batch_finishes(api, fake):
    fake.latency = 0.05
    response = await upload(api, csv_text(3), path="/hospitals/bulk?wait=false")
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "processing"
    assert body["processed_hospitals"] == 0

    batch_url = f"/hospitals/bulk/{body['batch_id']}"
    for _ in range(300):
        final = (await api.get(batch_url)).json()
        if final["status"] == "completed":
            break
        await asyncio.sleep(0.01)
    assert final["status"] == "completed" and final["batch_activated"] is True

def test_websocket_streams_progress_until_done():
    fake = FakeDirectory(latency=0.05)
    with make_client(fake, max_concurrency=1) as client:
        batch_id = post_csv(client, csv_text(4)).json()["batch_id"]
        messages = stream(client, batch_id)

    processed = [m["processed_hospitals"] for m in messages]
    assert processed == sorted(processed)  # progress only moves forward
    assert len(set(processed)) > 2  # intermediate counts were pushed
    assert messages[-1]["status"] == "completed"
    assert messages[-1]["batch_activated"] is True


def test_websocket_on_finished_batch_sends_once_and_closes():
    fake = FakeDirectory()
    with make_client(fake) as client:
        body = post_csv(client, csv_text(2), path="/hospitals/bulk").json()
        messages = stream(client, body["batch_id"])
    assert len(messages) == 1
    assert messages[0]["status"] == "completed"


def test_websocket_unknown_batch_closes_with_4404():
    with make_client(FakeDirectory()) as client:
        with client.websocket_connect(
                "/hospitals/bulk/00000000-0000-4000-8000-000000000000/ws") as ws:
            try:
                ws.receive_json()
                raise AssertionError("expected the socket to close")
            except WebSocketDisconnect as exc:
                assert exc.code == 4404


def test_resume_wait_false_then_conflicts():
    fake = FakeDirectory()
    fake.create_faults["Hospital 2"] = ["422"]
    with make_client(fake) as client:
        batch_id = post_csv(client, csv_text(3), path="/hospitals/bulk").json()["batch_id"]

        fake.latency = 0.05
        started = client.post(f"/hospitals/bulk/{batch_id}/resume?wait=false")
        assert started.status_code == 202
        assert started.json()["status"] == "processing"
        # The run already holds the batch, so a second resume is refused.
        assert client.post(f"/hospitals/bulk/{batch_id}/resume?wait=false").status_code == 409

        assert stream(client, batch_id)[-1]["status"] == "completed"
        assert client.post(f"/hospitals/bulk/{batch_id}/resume?wait=false").status_code == 409


def test_cors_headers_for_configured_origin_only():
    with make_client(FakeDirectory(), cors_origins=("http://ui.test",)) as client:
        allowed = client.get("/health", headers={"Origin": "http://ui.test"})
        other = client.get("/health", headers={"Origin": "http://evil.test"})
        too_big = client.post("/hospitals/bulk", content=b"x" * (settings.max_upload_bytes + 1),
                              headers={"Origin": "http://ui.test"})
    assert allowed.headers["access-control-allow-origin"] == "http://ui.test"
    assert "access-control-allow-origin" not in other.headers
    assert too_big.status_code == 413
    assert too_big.headers["access-control-allow-origin"] == "http://ui.test"


def test_result_includes_timestamps():
    with make_client(FakeDirectory()) as client:
        body = post_csv(client, csv_text(1), path="/hospitals/bulk").json()
    assert body["created_at"] <= body["updated_at"]
