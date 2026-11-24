"""In-memory stand-in for the Hospital Directory API, plugged into httpx via
MockTransport. Its behaviour mirrors what we observed on the live API:
creates are not deduplicated, re-activating returns 400, and batch
endpoints return 404 for an empty batch.

Faults are queued per hospital name (for creates) or per operation:
  "timeout"       - raise ReadTimeout without doing anything
  "lost_response" - perform the operation, then raise ReadTimeout
  "connect_error" - raise ConnectError
  "empty_200"     - perform the operation, then return 200 with an empty body
  "500" / "422" / "429" - return that status without doing anything
"""

import asyncio
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List

import httpx

BATCH_PATH = re.compile(r"^/hospitals/batch/([^/]+)(/activate)?$")
HOSPITAL_PATH = re.compile(r"^/hospitals/(\d+)$")


class FakeDirectory:
    def __init__(self, latency: float = 0.0):
        self.hospitals: Dict[int, dict] = {}
        self.next_id = 1
        self.latency = latency
        self.create_faults: Dict[str, List[str]] = defaultdict(list)
        self.activate_faults: List[str] = []
        self.list_faults: List[str] = []
        # Called just before an activation is applied; lets a test simulate
        # a timed-out create that lands late on the server.
        self.before_activate = None
        self.calls: List[str] = []
        self.in_flight = 0
        self.max_in_flight = 0

    def client(self, **kwargs) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self._handle),
            base_url="http://directory.test",
            **kwargs,
        )

    def batch(self, batch_id: str) -> List[dict]:
        return [h for h in self.hospitals.values() if h["creation_batch_id"] == batch_id]

    # ---- request handling ------------------------------------------------

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.latency:
                await asyncio.sleep(self.latency)
            self.calls.append(f"{request.method} {request.url.path}")
            return self._route(request)
        finally:
            self.in_flight -= 1

    def _route(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/" and method == "GET":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/hospitals/" and method == "POST":
            body = json.loads(request.content)
            return self._with_fault(
                self.create_faults[body.get("name", "")], request, lambda: self._create(body)
            )
        m = BATCH_PATH.match(path)
        if m and m.group(2) and method == "PATCH":
            return self._with_fault(self.activate_faults, request,
                                    lambda: self._activate(m.group(1)))
        if m and method == "GET":
            return self._with_fault(self.list_faults, request,
                                    lambda: self._list(m.group(1)))
        if m and method == "DELETE":
            return self._delete_batch(m.group(1))
        m = HOSPITAL_PATH.match(path)
        if m and method == "DELETE":
            if self.hospitals.pop(int(m.group(1)), None) is None:
                return httpx.Response(404, json={"detail": "Hospital not found"})
            return httpx.Response(204)
        return httpx.Response(404, json={"detail": "Not Found"})

    def _with_fault(self, queue: List[str], request: httpx.Request, operation) -> httpx.Response:
        fault = queue.pop(0) if queue else None
        if fault == "timeout":
            raise httpx.ReadTimeout("simulated read timeout", request=request)
        if fault == "connect_error":
            raise httpx.ConnectError("simulated connection refused", request=request)
        if fault in ("500", "422", "429"):
            return httpx.Response(int(fault), json={"detail": f"simulated {fault}"})
        response = operation()
        if fault == "lost_response":
            raise httpx.ReadTimeout("simulated lost response", request=request)
        if fault == "empty_200":
            return httpx.Response(200, json={})
        return response

    # ---- operations ------------------------------------------------------

    def _create(self, body: dict) -> httpx.Response:
        for field in ("name", "address"):
            if not body.get(field):
                return httpx.Response(422, json={"detail": [{
                    "type": "string_too_short", "loc": ["body", field],
                    "msg": "String should have at least 1 character",
                }]})
        hospital = {
            "id": self.next_id,
            "name": body["name"],
            "address": body["address"],
            "phone": body.get("phone"),
            "creation_batch_id": body.get("creation_batch_id"),
            "active": body.get("creation_batch_id") is None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.hospitals[self.next_id] = hospital
        self.next_id += 1
        return httpx.Response(200, json=hospital)

    def _list(self, batch_id: str) -> httpx.Response:
        found = self.batch(batch_id)
        if not found:
            return httpx.Response(404, json={"detail": "No hospitals found with the specified batch ID"})
        return httpx.Response(200, json=found)

    def _activate(self, batch_id: str) -> httpx.Response:
        if self.before_activate:
            self.before_activate(batch_id)
        found = self.batch(batch_id)
        if not found:
            return httpx.Response(404, json={"detail": "No hospitals found with the specified batch ID"})
        if any(h["active"] for h in found):
            return httpx.Response(400, json={
                "detail": "Cannot activate batch: one or more hospitals in the batch are already active"
            })
        for h in found:
            h["active"] = True
        return httpx.Response(200, json={
            "activated_count": len(found),
            "message": f"Activated {len(found)} hospital(s) with batch ID {batch_id}",
        })

    def _delete_batch(self, batch_id: str) -> httpx.Response:
        found = self.batch(batch_id)
        if not found:
            return httpx.Response(404, json={"detail": "No hospitals found with the specified batch ID"})
        for h in found:
            del self.hospitals[h["id"]]
        return httpx.Response(200, json={"deleted_count": len(found)})
