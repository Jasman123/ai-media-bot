"""Serve an ASGI app or a FakeTelegram on a real localhost socket, for integration tests."""

import asyncio
import contextlib
import socket

import httpx
import uvicorn


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fake_telegram_asgi(fake, delay_s: float = 0.0):
    """Expose FakeTelegram's handler over real HTTP (multipart travels over the wire)."""

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        if delay_s:
            await asyncio.sleep(delay_s)
        headers = [(k.decode(), v.decode()) for k, v in scope["headers"]]
        request = httpx.Request(scope["method"], f"http://test{scope['path']}", headers=headers, content=body)
        response = fake._handle(request)
        await send({"type": "http.response.start", "status": response.status_code,
                    "headers": [(b"content-type", response.headers.get("content-type", "application/json").encode())]})
        await send({"type": "http.response.body", "body": response.content})

    return app


@contextlib.asynccontextmanager
async def serve(app, port: int | None = None):
    port = port or free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task
