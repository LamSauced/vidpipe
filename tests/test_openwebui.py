"""Endpoint discovery against Open WebUI instances that expose different paths."""
import asyncio
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from app import openwebui  # noqa: E402

SEEN: list[str] = []


def make_server(chat_path: str, models_path: str, require_key: bool = False):
    api = FastAPI()

    @api.middleware("http")
    async def log(request: Request, call_next):
        SEEN.append(f"{request.method} {request.url.path}")
        return await call_next(request)

    @api.get(models_path)
    def models(request: Request):
        if require_key and "Bearer good" not in request.headers.get("authorization", ""):
            return JSONResponse({"detail": "not authenticated"}, status_code=401)
        return {"data": [{"id": "writer", "name": "Writer"}]}

    @api.post(chat_path)
    async def chat(request: Request):
        body = await request.json()
        if not body.get("model"):
            return JSONResponse({"detail": "model required"}, status_code=400)
        return {"choices": [{"message": {"content": "hello from " + chat_path}}]}

    return api


def serve(app, port):
    s = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=s.run, daemon=True).start()


# Three shapes: modern, the /api/v1 build, and plain OpenAI-compatible.
serve(make_server("/api/chat/completions", "/api/models"), 8811)
serve(make_server("/api/v1/chat/completions", "/api/v1/models"), 8812)
serve(make_server("/v1/chat/completions", "/v1/models", require_key=True), 8813)
time.sleep(1.5)


async def main():
    for port, expected in ((8811, "/api/chat/completions"),
                           (8812, "/api/v1/chat/completions"),
                           (8813, "/v1/chat/completions")):
        base = f"http://127.0.0.1:{port}"
        openwebui.forget()
        SEEN.clear()
        reply = await openwebui.chat(base, "good", "writer", [{"role": "user", "content": "hi"}])
        assert reply == "hello from " + expected, reply
        print(f"  ok  {port}: found {expected}")

        # the working path is remembered, so the next call probes nothing
        SEEN.clear()
        await openwebui.chat(base, "good", "writer", [{"role": "user", "content": "hi"}])
        assert SEEN == [f"POST {expected}"], SEEN
        print(f"  ok  {port}: path cached, one request on the next call")

        found = await openwebui.probe(base, "good")
        assert found["chat_path"] == expected and found["models"] == 1, found
        print(f"  ok  {port}: probe reports {found['models_path']} + {found['chat_path']}")

    # a rejected key is reported as such, not as a missing endpoint
    openwebui.forget()
    try:
        await openwebui.list_models("http://127.0.0.1:8813", "wrong")
        raise AssertionError("should have raised")
    except openwebui.OpenWebUIError as exc:
        assert "401" in str(exc) and "API key" in str(exc), exc
    print("  ok  bad key reported as an auth problem")

    # nothing listening at all
    openwebui.forget()
    try:
        await openwebui.chat("http://127.0.0.1:8819", "k", "writer", [])
        raise AssertionError("should have raised")
    except openwebui.OpenWebUIError as exc:
        assert "reach" in str(exc).lower(), exc
    print("  ok  unreachable host reported clearly")

    # a server with no matching path names what it tried
    serve(FastAPI(), 8814)
    await asyncio.sleep(1.0)
    openwebui.forget()
    try:
        await openwebui.chat("http://127.0.0.1:8814", "k", "writer", [])
        raise AssertionError("should have raised")
    except openwebui.OpenWebUIError as exc:
        assert "No chat endpoint found" in str(exc) and "/api/chat/completions" in str(exc), exc
    print("  ok  no matching path lists the attempts")

    print("\nall openwebui assertions passed")


asyncio.run(main())
