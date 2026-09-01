"""ComfyUI client: upload inputs, queue a graph, follow progress, fetch outputs."""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import httpx
import websockets


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, base_url: str, timeout: float = 60.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def ws_url(self) -> str:
        scheme = "wss" if self.base.startswith("https") else "ws"
        host = self.base.split("://", 1)[1]
        return f"{scheme}://{host}/ws"

    async def object_info(self) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(f"{self.base}/object_info")
            r.raise_for_status()
            return r.json()

    async def system_stats(self) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(f"{self.base}/system_stats")
            r.raise_for_status()
            return r.json()

    async def upload(self, path: str | Path, subfolder: str = "vidpipe") -> str:
        """Upload a file into ComfyUI's input dir. Returns the name to use in a node."""
        path = Path(path)
        data = {"type": "input", "overwrite": "true"}
        if subfolder:
            data["subfolder"] = subfolder
        async with httpx.AsyncClient(timeout=300) as c:
            with path.open("rb") as fh:
                r = await c.post(
                    f"{self.base}/upload/image",
                    data=data,
                    files={"image": (path.name, fh, "application/octet-stream")},
                )
        if r.status_code >= 400:
            raise ComfyError(f"Upload failed ({r.status_code}): {r.text[:300]}")
        res = r.json()
        name, sub = res.get("name", path.name), res.get("subfolder", "")
        return f"{sub}/{name}" if sub else name

    async def queue(self, graph: dict, client_id: str) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(
                f"{self.base}/prompt", json={"prompt": graph, "client_id": client_id}
            )
        if r.status_code >= 400:
            detail = r.text[:2000]
            try:
                j = r.json()
                detail = json.dumps(j.get("node_errors") or j, indent=2)[:2000]
            except Exception:
                pass
            raise ComfyError(f"ComfyUI rejected the graph:\n{detail}")
        return r.json()["prompt_id"]

    async def history(self, prompt_id: str) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(f"{self.base}/history/{prompt_id}")
            r.raise_for_status()
            return r.json().get(prompt_id, {})

    async def interrupt(self) -> None:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            await c.post(f"{self.base}/interrupt")

    async def download(self, ref: dict, dest: Path) -> Path:
        params = {
            "filename": ref.get("filename", ""),
            "subfolder": ref.get("subfolder", ""),
            "type": ref.get("type", "output"),
        }
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.get(f"{self.base}/view", params=params)
            r.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
        return dest

    async def run(
        self,
        graph: dict,
        on_event: Callable[[dict], Any] | None = None,
        poll_interval: float = 2.0,
    ) -> dict:
        """Queue a graph and wait for it. Returns the history entry's outputs."""
        client_id = str(uuid.uuid4())
        prompt_id = await self.queue(graph, client_id)
        if on_event:
            await _maybe_await(on_event({"type": "queued", "prompt_id": prompt_id}))

        done = asyncio.Event()
        failure: dict = {}

        async def follow():
            try:
                async with websockets.connect(
                    f"{self.ws_url}?clientId={client_id}", max_size=None,
                    ping_interval=20, ping_timeout=60,
                ) as ws:
                    while not done.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5)
                        except asyncio.TimeoutError:
                            continue
                        if isinstance(raw, bytes):
                            continue
                        msg = json.loads(raw)
                        data = msg.get("data") or {}
                        if data.get("prompt_id") not in (None, prompt_id):
                            continue
                        mtype = msg.get("type")
                        if mtype == "progress" and on_event:
                            await _maybe_await(on_event({
                                "type": "progress",
                                "value": data.get("value", 0),
                                "max": data.get("max", 1),
                            }))
                        elif mtype == "executing" and on_event:
                            await _maybe_await(on_event({
                                "type": "executing", "node": data.get("node")
                            }))
                            if data.get("node") is None:
                                done.set()
                        elif mtype == "execution_error":
                            failure.update(data)
                            done.set()
                        elif mtype in ("execution_success", "execution_cached"):
                            if mtype == "execution_success":
                                done.set()
            except Exception:
                pass  # fall back to polling

        follower = asyncio.create_task(follow())
        try:
            while not done.is_set():
                await asyncio.sleep(poll_interval)
                hist = await self.history(prompt_id)
                status = hist.get("status") or {}
                if status.get("completed") or status.get("status_str") == "success":
                    done.set()
                elif status.get("status_str") == "error":
                    failure.setdefault("exception_message", _first_error(status))
                    done.set()
        finally:
            done.set()
            follower.cancel()
            try:
                await follower
            except (asyncio.CancelledError, Exception):
                pass

        if failure:
            msg = failure.get("exception_message") or "Execution failed."
            node = failure.get("node_type") or failure.get("node_id")
            raise ComfyError(f"{msg}" + (f" (node {node})" if node else ""))

        hist = await self.history(prompt_id)
        status = hist.get("status") or {}
        if status.get("status_str") == "error":
            raise ComfyError(_first_error(status))
        return {"prompt_id": prompt_id, "outputs": hist.get("outputs", {})}


def _first_error(status: dict) -> str:
    for kind, payload in status.get("messages", []):
        if kind == "execution_error" and isinstance(payload, dict):
            return payload.get("exception_message", "Execution failed.")
    return "Execution failed."


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return await value
    return value


def collect_media(outputs: dict) -> list[dict]:
    """Pull every file reference out of a history outputs blob, videos first."""
    found: list[dict] = []
    for node_out in outputs.values():
        if not isinstance(node_out, dict):
            continue
        for key, items in node_out.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and item.get("filename"):
                    found.append({**item, "_key": key})
    video_ext = (".mp4", ".webm", ".mkv", ".mov", ".m4v")
    found.sort(key=lambda i: 0 if str(i["filename"]).lower().endswith(video_ext) else 1)
    return found
