"""Open WebUI client.

Open WebUI has moved its chat endpoint between releases — `/api/chat/completions`,
`/api/v1/chat/completions` and the OpenAI-compatible `/v1/chat/completions` are all
current somewhere. Rather than pick one, we try each and remember what answered,
so the same instance isn't re-probed on every call.
"""
from __future__ import annotations

import httpx

CHAT_PATHS = (
    "/api/chat/completions",
    "/api/v1/chat/completions",
    "/v1/chat/completions",
    "/ollama/v1/chat/completions",
)
MODEL_PATHS = (
    "/api/models",
    "/api/v1/models",
    "/v1/models",
)

#: base_url -> path that worked, so we probe once per process
_resolved: dict[str, str] = {}

#: status codes meaning "wrong path, try the next one". Anything else (401, 403,
#: 500) is a real answer from a real endpoint and should surface as-is.
_WRONG_PATH = {404, 405}


class OpenWebUIError(RuntimeError):
    pass


def _headers(key: str) -> dict:
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def _key(base_url: str, kind: str) -> str:
    return f"{kind}@{base_url.rstrip('/')}"


def forget(base_url: str | None = None) -> None:
    """Drop cached paths, so a changed URL or upgraded server is re-probed."""
    if base_url is None:
        _resolved.clear()
        return
    for k in [k for k in _resolved if k.endswith("@" + base_url.rstrip("/"))]:
        _resolved.pop(k, None)


async def _try_paths(client, method, base_url, api_key, paths, kind, **kwargs):
    """Call each candidate until one isn't a 404/405. Returns (response, path)."""
    base = base_url.rstrip("/")
    known = _resolved.get(_key(base, kind))
    ordered = [known] + [p for p in paths if p != known] if known else list(paths)

    attempts = []
    for path in ordered:
        try:
            r = await client.request(method, base + path, headers=_headers(api_key), **kwargs)
        except httpx.TimeoutException as exc:
            raise OpenWebUIError(
                f"{base}{path} didn't answer in time. Raise 'LLM timeout' in Settings "
                f"if the model is just slow, or check it isn't stuck loading."
            ) from exc
        except httpx.HTTPError as exc:
            raise OpenWebUIError(f"Couldn't reach {base}: {exc}") from exc
        if r.status_code in _WRONG_PATH:
            attempts.append(f"{path} -> {r.status_code}")
            continue
        _resolved[_key(base, kind)] = path
        return r, path

    raise OpenWebUIError(
        f"No {kind} endpoint found on {base}. Tried: {', '.join(attempts)}. "
        f"Check the URL, or that this is an Open WebUI instance."
    )


async def list_models(base_url: str, api_key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r, _ = await _try_paths(client, "GET", base_url, api_key, MODEL_PATHS, "models")
        if r.status_code >= 400:
            raise OpenWebUIError(_explain(r))
        payload = r.json()

    items = payload.get("data", payload if isinstance(payload, list) else [])
    out = []
    for m in items:
        if not isinstance(m, dict):
            continue
        mid = m.get("id") or m.get("name")
        if not mid:
            continue
        out.append({"id": mid, "name": m.get("name") or mid})
    out.sort(key=lambda m: m["name"].lower())
    return out


async def chat(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    temperature: float = 0.8,
    timeout: float = 300.0,
) -> str:
    """Single non-streaming completion. Returns the assistant text."""
    if not model:
        raise OpenWebUIError("No model selected.")
    body = {"model": model, "messages": messages,
            "temperature": temperature, "stream": False}

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r, path = await _try_paths(client, "POST", base_url, api_key,
                                   CHAT_PATHS, "chat", json=body)
        if r.status_code >= 400:
            raise OpenWebUIError(f"{path}: {_explain(r)}")
        data = r.json()

    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        detail = data.get("detail") or data.get("error") if isinstance(data, dict) else None
        if detail:
            raise OpenWebUIError(str(detail)[:300]) from exc
        raise OpenWebUIError(f"Unexpected response shape: {str(data)[:300]}") from exc


def _explain(r: httpx.Response) -> str:
    body = r.text[:300]
    if r.status_code in (401, 403):
        return (f"{r.status_code} - the API key was rejected. Open WebUI -> Settings -> "
                f"Account -> API Keys. {body}")
    return f"{r.status_code}: {body}"


async def probe(base_url: str, api_key: str) -> dict:
    """Which paths this instance answers on - for the Test button."""
    forget(base_url)
    out: dict = {"models_path": None, "chat_path": None, "models": 0, "error": None}
    try:
        models = await list_models(base_url, api_key)
        out["models"] = len(models)
        out["models_path"] = _resolved.get(_key(base_url, "models"))
    except OpenWebUIError as exc:
        out["error"] = str(exc)
        return out

    # Probe the chat path with an unrouted request: a 404/405 rules the path out,
    # while any other status means we found the endpoint.
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for path in CHAT_PATHS:
            try:
                r = await client.post(base + path, headers=_headers(api_key),
                                      json={"model": "", "messages": []})
            except httpx.HTTPError:
                continue
            if r.status_code not in _WRONG_PATH:
                out["chat_path"] = path
                _resolved[_key(base, "chat")] = path
                break
    return out
