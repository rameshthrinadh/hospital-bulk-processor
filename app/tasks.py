"""Tracking for batch-processing tasks that must outlive their HTTP request.

asyncio's event loop holds only a *weak* reference to a task, so a task
nobody else references can be garbage-collected mid-run. When a client
disconnects, the request handler is cancelled and drops its reference; this
registry keeps the strong reference until the task finishes, and lets the app
wait for or cancel running work at shutdown.
"""

import asyncio
import logging
from typing import Coroutine, Set

logger = logging.getLogger(__name__)


class TaskRegistry:
    def __init__(self) -> None:
        self._tasks: Set[asyncio.Task] = set()

    def __len__(self) -> int:
        return len(self._tasks)

    def spawn(self, coro: Coroutine) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    def _on_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        # Retrieve the exception so asyncio doesn't warn "exception was never
        # retrieved" when the request that would have awaited it is gone.
        if not task.cancelled() and task.exception() is not None:
            logger.info("background task ended with %r", task.exception())

    async def shutdown(self, grace_seconds: float) -> None:
        """Let running batches finish for up to `grace_seconds`, then cancel.

        A cancelled batch records itself as failed and stays inactive (see
        BatchProcessor.run), so it is never left half-activated.
        """
        if not self._tasks:
            return
        logger.info("waiting up to %ss for %d running batch(es)", grace_seconds, len(self._tasks))
        _, pending = await asyncio.wait(set(self._tasks), timeout=grace_seconds)
        for task in pending:
            task.cancel()
        if pending:
            logger.warning("cancelled %d batch(es) still running at shutdown", len(pending))
            await asyncio.gather(*pending, return_exceptions=True)
