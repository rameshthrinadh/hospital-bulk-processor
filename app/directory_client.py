"""Client for the external Hospital Directory API.

The API has no idempotency keys, so a blind retry of POST /hospitals/ can
create a duplicate. We therefore sort every failure into one of three kinds:

  PERMANENT  - the API rejected the request (e.g. 422). Retrying won't help.
  RETRYABLE  - the request provably never took effect (connection refused,
               429). Safe to resend.
  AMBIGUOUS  - the request may or may not have taken effect (read timeout,
               5xx, dropped connection). Before resending, we look at the
               batch to see whether the first attempt actually landed.
"""

import asyncio
import enum
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.csv_parser import HospitalRow

logger = logging.getLogger(__name__)


class ErrorKind(str, enum.Enum):
    PERMANENT = "permanent"
    RETRYABLE = "retryable"
    AMBIGUOUS = "ambiguous"


class DirectoryError(Exception):
    def __init__(self, kind: ErrorKind, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status_code = status_code


def matches(hospital: Dict[str, Any], row: HospitalRow) -> bool:
    return (hospital.get("name"), hospital.get("address"), hospital.get("phone")) == row.identity()


class DirectoryClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
        max_concurrency: int = 10,
    ):
        self._http = http
        # One semaphore per client, and one client per app, so this caps
        # concurrent outbound requests across *all* batches, not per batch.
        self._slots = asyncio.Semaphore(max_concurrency)
        self._max_attempts = max_attempts
        self._backoff = retry_backoff_seconds

    # ---- single-shot calls: one HTTP request, failures classified --------

    async def create_hospital(self, row: HospitalRow, batch_id: str) -> Dict[str, Any]:
        payload = {
            "name": row.name,
            "address": row.address,
            "phone": row.phone,
            "creation_batch_id": batch_id,
        }
        return await self._request("POST", "/hospitals/", json=payload)

    async def list_batch(self, batch_id: str) -> List[Dict[str, Any]]:
        try:
            return await self._request("GET", f"/hospitals/batch/{batch_id}")
        except DirectoryError as exc:
            if exc.status_code == 404:  # the API's way of saying "empty batch"
                return []
            raise

    async def activate_batch(self, batch_id: str) -> Dict[str, Any]:
        return await self._request("PATCH", f"/hospitals/batch/{batch_id}/activate")

    async def delete_hospital(self, hospital_id: int) -> None:
        await self._request("DELETE", f"/hospitals/{hospital_id}")

    async def delete_batch(self, batch_id: str) -> Dict[str, Any]:
        return await self._request("DELETE", f"/hospitals/batch/{batch_id}")

    async def ping(self, timeout: float) -> None:
        """Hit the health check, allowing a long timeout for a cold start."""
        await self._request("GET", "/", timeout=timeout)

    # ---- calls with retry and recovery -----------------------------------

    async def create_hospital_safely(self, row: HospitalRow, batch_id: str) -> Dict[str, Any]:
        """Create a hospital, retrying without creating duplicates where we can tell.

        After an ambiguous failure we list the batch and adopt a matching
        hospital if the earlier attempt landed. This narrows the duplicate
        window but cannot close it: the earlier request may still be in
        flight server-side when we look. The processor therefore re-checks
        the whole batch before activation and removes any extras.
        """
        last_error: Optional[DirectoryError] = None
        for attempt in range(1, self._max_attempts + 1):
            if last_error is not None:
                await self._sleep_before(attempt)
                if last_error.kind is ErrorKind.AMBIGUOUS:
                    existing = await self._find_existing(row, batch_id)
                    if existing is not None:
                        logger.info("row %s: recovered hospital %s after lost response",
                                    row.row, existing.get("id"))
                        return existing
            try:
                return await self.create_hospital(row, batch_id)
            except DirectoryError as exc:
                if exc.kind is ErrorKind.PERMANENT:
                    raise
                logger.warning("row %s: create attempt %d failed (%s): %s",
                               row.row, attempt, exc.kind.value, exc.message)
                last_error = exc
        assert last_error is not None
        raise last_error

    async def activate_batch_safely(self, batch_id: str, expected_count: int) -> int:
        """Activate a batch, treating "it was already activated" as success.

        A lost activation response looks like a timeout, and a retry after one
        gets a 400 ("already active"). In both cases we confirm by reading the
        batch back rather than trusting the error.

        Returns how many hospitals were activated: the API's activated_count,
        or, when that isn't available, the count confirmed by reading the
        batch back. The caller must still compare it with what it expects.
        """
        last_error: Optional[DirectoryError] = None
        for attempt in range(1, self._max_attempts + 1):
            if last_error is not None:
                await self._sleep_before(attempt)
            try:
                response = await self.activate_batch(batch_id)
            except DirectoryError as exc:
                logger.warning("batch %s: activate attempt %d failed (%s): %s",
                               batch_id, attempt, exc.kind.value, exc.message)
                last_error = exc
            else:
                count = response.get("activated_count") if isinstance(response, dict) else None
                if isinstance(count, int) and not isinstance(count, bool):
                    return count
                # A 200 we can't interpret: don't assume, read the batch back.
                last_error = DirectoryError(
                    ErrorKind.AMBIGUOUS, "Activation response had no activated_count", 200
                )

            state = await self._activation_state(batch_id, expected_count)
            if state == "active":
                # Read-back saw exactly expected_count hospitals, all active.
                return expected_count
            if state == "inconsistent":
                raise DirectoryError(
                    ErrorKind.PERMANENT,
                    "Batch contents or activation state do not match what we "
                    "created; refusing to retry activation",
                )
            # A 400 that isn't explained by the batch already being active, a
            # 404, or a 422 won't change on retry.
            if last_error.kind is ErrorKind.PERMANENT:
                raise last_error
        assert last_error is not None
        raise last_error

    # ---- helpers ---------------------------------------------------------

    async def _find_existing(self, row: HospitalRow, batch_id: str) -> Optional[Dict[str, Any]]:
        try:
            hospitals = await self.list_batch(batch_id)
        except DirectoryError as exc:
            # Can't tell; fall through to a retry. Pre-activation verification
            # catches any duplicate this produces.
            logger.warning("row %s: could not check batch for existing record: %s",
                           row.row, exc.message)
            return None
        return next((h for h in hospitals if matches(h, row)), None)

    async def _activation_state(self, batch_id: str, expected_count: int) -> str:
        """Return 'active', 'inactive', 'inconsistent', or 'unknown'."""
        try:
            hospitals = await self.list_batch(batch_id)
        except DirectoryError:
            return "unknown"
        active = sum(1 for h in hospitals if h.get("active"))
        if hospitals and active == len(hospitals) == expected_count:
            return "active"
        if active == 0:
            return "inactive"
        return "inconsistent"

    async def _sleep_before(self, attempt: int) -> None:
        # Exponential backoff: 1x, 2x, 4x ... the base delay.
        await asyncio.sleep(self._backoff * 2 ** (attempt - 2))

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        try:
            async with self._slots:
                response = await self._http.request(method, path, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            # Never reached the server, so resending is safe.
            raise DirectoryError(ErrorKind.RETRYABLE, f"{type(exc).__name__}: {exc}") from exc
        except httpx.TransportError as exc:
            # Read/write timeouts, dropped connections: the request may have
            # been processed even though we never saw the response.
            raise DirectoryError(ErrorKind.AMBIGUOUS, f"{type(exc).__name__}: {exc}") from exc

        status = response.status_code
        if status == 204:
            return None
        if 200 <= status < 300:
            try:
                return response.json()
            except ValueError as exc:
                raise DirectoryError(
                    ErrorKind.AMBIGUOUS, "Invalid JSON in success response", status
                ) from exc

        detail = _error_detail(response)
        if status == 429:
            kind = ErrorKind.RETRYABLE
        elif status >= 500 or status == 408:
            kind = ErrorKind.AMBIGUOUS
        else:
            kind = ErrorKind.PERMANENT
        raise DirectoryError(kind, f"HTTP {status}: {detail}", status)


def _error_detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        return response.text[:200]
    if isinstance(detail, list):  # FastAPI/Pydantic validation errors
        return "; ".join(
            f"{'.'.join(str(p) for p in d.get('loc', [])[1:])}: {d.get('msg')}" for d in detail
        )
    return str(detail)
