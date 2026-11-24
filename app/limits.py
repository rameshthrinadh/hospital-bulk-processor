"""Request body size limit, enforced while the body streams in.

FastAPI parses multipart bodies before calling the endpoint, so a size check
inside the endpoint would run only after the whole upload had been received.
This ASGI middleware sits in front: it rejects a declared Content-Length that
is too large before reading anything, and counts bytes as they arrive so a
body without Content-Length (chunked upload) is cut off at the limit as well.
"""

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    def _too_large(self) -> str:
        return f"Request body exceeds {self.max_bytes} bytes"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await JSONResponse({"detail": self._too_large()}, status_code=413)(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # Raised inside the app's body parsing; FastAPI re-raises
                    # HTTPExceptions from there and its handler sends the 413.
                    raise HTTPException(status_code=413, detail=self._too_large())
            return message

        await self.app(scope, limited_receive, send)
