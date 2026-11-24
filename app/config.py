"""Runtime configuration, read once from environment variables."""

import os
from dataclasses import dataclass
from typing import Tuple


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_list(name: str, default: str) -> Tuple[str, ...]:
    return tuple(v.strip() for v in os.environ.get(name, default).split(",") if v.strip())


@dataclass(frozen=True)
class Settings:
    directory_api_url: str = os.environ.get(
        "DIRECTORY_API_URL", "https://hospital-directory.onrender.com"
    )

    # CSV limits. MAX_ROWS comes from the assignment; the byte cap stops us
    # from reading an arbitrarily large upload into memory before counting rows.
    max_rows: int = _env_int("MAX_CSV_ROWS", 20)
    # Applies to the whole request body (CSV plus multipart framing). 20 rows
    # of hospital data is a few KiB, so 64 KiB is generous.
    max_upload_bytes: int = _env_int("MAX_UPLOAD_BYTES", 64 * 1024)

    # Outbound HTTP. The directory API takes ~5s per create, so the read
    # timeout must sit well above that; connect failures should surface fast.
    connect_timeout: float = _env_float("DIRECTORY_CONNECT_TIMEOUT", 5.0)
    read_timeout: float = _env_float("DIRECTORY_READ_TIMEOUT", 20.0)
    max_concurrency: int = _env_int("DIRECTORY_MAX_CONCURRENCY", 10)
    max_attempts: int = _env_int("DIRECTORY_MAX_ATTEMPTS", 3)
    retry_backoff_seconds: float = _env_float("DIRECTORY_RETRY_BACKOFF", 1.0)
    # The directory API sleeps when idle; waking it can take ~50s.
    cold_start_timeout: float = _env_float("DIRECTORY_COLD_START_TIMEOUT", 60.0)

    # How long app shutdown waits for still-running batches before cancelling
    # them. Runs *after* uvicorn's own --timeout-graceful-shutdown (20s in the
    # Dockerfile), during which batches also keep running; the two together
    # must stay under the platform's SIGKILL deadline (30s on Render).
    shutdown_grace_seconds: float = _env_float("SHUTDOWN_GRACE_SECONDS", 5.0)
    max_stored_batches: int = _env_int("MAX_STORED_BATCHES", 1000)

    # Browser origins allowed to call the API (the frontend). Comma-separated;
    # empty disables CORS. Defaults to the Vite dev server.
    cors_origins: Tuple[str, ...] = _env_list(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")

    def __post_init__(self) -> None:
        # Fail at startup, not mid-batch: e.g. a concurrency of 0 would make
        # every outbound request wait forever on the semaphore.
        for name in ("max_rows", "max_upload_bytes", "connect_timeout", "read_timeout",
                     "max_concurrency", "max_attempts", "cold_start_timeout",
                     "max_stored_batches"):
            if getattr(self, name) <= 0:
                raise ValueError(f"Setting {name} must be positive, got {getattr(self, name)}")
        for name in ("retry_backoff_seconds", "shutdown_grace_seconds"):
            if getattr(self, name) < 0:
                raise ValueError(f"Setting {name} must not be negative")


settings = Settings()
