"""Smoke test the HTTP surface with FastAPI's TestClient."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["VIDPIPE_DATA"] = tempfile.mkdtemp(prefix="vidpipe-api-")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

c = TestClient(app)


def check(label, condition, detail=""):
    assert condition, f"{label} failed {detail}"
    print(f"  ok  {label}")


# static
for path, needle in (("/", "vidpipe"), ("/style.css", "--signal"), ("/app.js", "renderChain")):
    r = c.get(path)
    check(f"GET {path}", r.status_code == 200 and needle in r.text, r.status_code)

# settings round-trip, key never echoed back
c.post("/api/settings", json={"openwebui_url": "http://x:3000", "openwebui_key": "secret"})
s = c.get("/api/settings").json()
check("settings saved", s["openwebui_url"] == "http://x:3000")
check("key masked", s["openwebui_key"] == "••••" and s["has_key"])
c.post("/api/settings", json={"openwebui_key": "••••", "comfy_url": "http://y:8188"})
from app import config  # noqa: E402
check("mask doesn't overwrite key", config.load()["openwebui_key"] == "secret")

# workflow load (editor format, ComfyUI unreachable -> clear error)
ui = (ROOT / "tests" / "fixtures" / "workflow.ui.json").read_bytes()
r = c.post("/api/workflow", files={"file": ("wf.json", ui, "application/json")})
check("editor format without ComfyUI is refused clearly",
      r.status_code == 400 and "Export (API)" in r.json()["detail"], r.text[:200])

# ...but an API-format file loads fine
from app import workflow  # noqa: E402
from tests.test_workflow import OI  # noqa: E402
api_graph = workflow.ui_to_api(json.loads(ui), OI)
r = c.post("/api/workflow", files={
    "file": ("wf.api.json", json.dumps(api_graph).encode(), "application/json")})
check("API format loads", r.status_code == 200, r.text[:200])
summary = c.get("/api/workflow").json()["summary"]
check("slots detected, spares included",
      summary["ref_image_slots"] == 6 and summary["ref_audio_slots"] == 3)
check("spares flagged",
      summary["ref_image_slots_disabled"] == 2 and summary["ref_audio_slots_disabled"] == 1)

# projects
p = c.post("/api/projects", json={"name": "demo", "idea": "an idea"}).json()
pid = p["id"]
check("project created", p["name"] == "demo")
r = c.patch(f"/api/projects/{pid}", json={"settings": {"segment_count": 2}}).json()
check("segments materialise with the count", len(r["segments"]) == 2)
check("other settings survive the merge", r["settings"]["duration"] == 15.0)
r = c.patch(f"/api/projects/{pid}/segments/0", json={"prompt": "hello"}).json()
check("segment edit saved", r["prompt"] == "hello" and r["status"] == "ready")
check("projects listed", any(x["id"] == pid for x in c.get("/api/projects").json()["projects"]))

# guardrails
check("render without ComfyUI reports, not crashes",
      c.post(f"/api/projects/{pid}/render", json={}).status_code in (200, 409))
check("missing project 404s", c.get("/api/projects/9999").status_code == 404)

# a busy runner must refuse cleanly, with headers a client can actually read
import asyncio as _asyncio  # noqa: E402
from app import pipeline as _pipeline  # noqa: E402


async def _busy_check():
    async def hang():
        await _asyncio.sleep(30)
    _pipeline.runner.start(hang(), "Script · project 1")
    await _asyncio.sleep(0.1)
    r = c.post(f"/api/projects/{pid}/script")
    assert r.status_code == 409, r.status_code
    assert "Already running" in r.json()["detail"], r.json()
    assert r.headers.get("X-Busy-Label"), r.headers
    _pipeline.runner.cancel()
    await _asyncio.sleep(0.3)
    assert not _pipeline.runner.busy
    r2 = c.get("/api/status").json()
    assert r2["busy"] is False, r2


_asyncio.run(_busy_check())
check("busy runner refuses with a readable 409, then Stop frees it", True)
check("missing video 404s", c.get(f"/api/projects/{pid}/segments/1/video").status_code == 404)
check("health returns both services", set(c.get("/api/health").json()) == {"openwebui", "comfy"})
check("unload route exists and reports no URL configured",
      c.post("/api/upstream/unload").status_code == 400)
cap = c.get("/api/capacity").json()
check("capacity reserves one image slot for the carried frame",
      cap["image_slots"] == 6 and cap["max_static_images"] == 5, cap)
check("beat edit saved",
      c.patch(f"/api/projects/{pid}/segments/0/beat", json={"beat": "a beat"}).json()["beat"] == "a beat")
check("final video 404s before joining", c.get(f"/api/projects/{pid}/final").status_code == 404)
check("status shape", "busy" in c.get("/api/status").json())

c.delete(f"/api/projects/{pid}")
check("project deleted", c.get(f"/api/projects/{pid}").status_code == 404)

print("\nall api assertions passed")
