"""Free the LLM's VRAM before ComfyUI starts, so the two don't fight over the card.

Aimed at llama-swap (GET/POST /unload, GET /running), but any endpoint that
unloads on request works — set the URL in Settings.
"""
from __future__ import annotations

import asyncio

import httpx


class SwapError(RuntimeError):
    pass


async def unload(base_url: str, path: str = "/api/models/unload",
                 running_path: str = "/running", wait: float = 20.0) -> str:
    """Ask the LLM server to unload, then wait until it reports nothing running.

    Returns a short status line for the activity log. Raises only if the server
    is reachable but refuses; an unreachable server is the caller's decision.
    """
    base = base_url.rstrip("/")
    url = base + (path if path.startswith("/") else "/" + path)

    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url)
        if r.status_code in (404, 405):
            r = await c.get(url)
        if r.status_code >= 400:
            raise SwapError(f"{url} returned {r.status_code}: {r.text[:200]}")

        # Poll the running endpoint until it's empty. Not every build has one;
        # if it's missing we fall back to a short fixed pause.
        deadline = asyncio.get_event_loop().time() + wait
        checked = False
        while asyncio.get_event_loop().time() < deadline:
            try:
                s = await c.get(base + (running_path if running_path.startswith("/")
                                            else "/" + running_path))
            except httpx.HTTPError:
                break
            if s.status_code >= 400:
                break
            checked = True
            try:
                payload = s.json()
            except ValueError:
                break
            models = _active(payload)
            if not models:
                return f"LLM unloaded via {url} — VRAM free"
            await asyncio.sleep(1.0)

        if checked:
            return (f"Called {url}, but a model is still loaded after {wait:g}s")
    await asyncio.sleep(2.0)
    return f"LLM unload requested via {url}"


LOADED_STATES = {"running", "loaded", "ready", "starting", "active", "busy"}
UNLOADED_STATES = {"stopped", "unloaded", "shutdown", "idle", "exited", "stopping"}


def _active(payload) -> list:
    """Models still holding VRAM, across the shapes these servers return.

    Deliberately conservative: an entry only counts as loaded when it says so.
    A listing endpoint that reports every configured model without any state
    would otherwise look permanently busy and stall each render for the full
    timeout, which is worse than starting ComfyUI a moment early.
    """
    if isinstance(payload, dict):
        for key in ("running", "data", "models"):
            if key in payload:
                items = payload[key]
                break
        else:
            items = []
    else:
        items = payload
    if not isinstance(items, list):
        return []

    live = []
    for item in items:
        if isinstance(item, dict):
            state = str(item.get("state", item.get("status", ""))).strip().lower()
            if state in UNLOADED_STATES:
                continue
            if state in LOADED_STATES:
                live.append(item)
            # no recognisable state -> don't treat it as holding VRAM
        elif isinstance(item, str) and item.strip():
            live.append(item)
    return live


async def unload_quietly(base_url: str, path: str = "/api/models/unload",
                         running_path: str = "/running") -> str | None:
    """Best-effort unload. Returns a status line, or None if it wasn't possible."""
    if not (base_url or "").strip():
        return None
    try:
        return await unload(base_url, path, running_path)
    except Exception as exc:
        return f"LLM unload via {base_url.rstrip('/')}{path} failed ({exc}); continuing anyway"
