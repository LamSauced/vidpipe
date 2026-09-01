"""Every run route, exercised through a real HTTP server.

TestClient can mask this class of bug, so these go over the wire to a live
uvicorn process — the same path the browser takes. A sync `def` route that
schedules an asyncio task fails here with "no running event loop", which is
exactly what slipped through before.
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["VIDPIPE_DATA"] = tempfile.mkdtemp(prefix="vidpipe-routes-")

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from app import config, db, workflow  # noqa: E402
from tests.test_workflow import OI  # noqa: E402

ui = json.loads((ROOT / "tests" / "fixtures" / "workflow.ui.json").read_text())
config.WORKFLOW_PATH.write_text(json.dumps(workflow.ui_to_api(ui, OI)))
# Point at nothing, so jobs fail fast rather than hanging on a real service.
config.save({"openwebui_url": "http://127.0.0.1:9", "comfy_url": "http://127.0.0.1:9",
             "script_model": "m", "segment_model": "m", "llm_timeout": 5})

db.init()
project = db.create_project("routes", "an idea")
pid = project["id"]
db.update_project(pid, script="a script", settings={**project["settings"], "segment_count": 2})
db.ensure_segments(pid, 2)
db.update_segment(pid, 0, prompt="a prompt", status="ready")

from app.main import app  # noqa: E402

PORT = 8817
threading.Thread(
    target=uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error")).run,
    daemon=True).start()
time.sleep(1.8)

BASE = f"http://127.0.0.1:{PORT}"
ROUTES = [
    ("POST", f"/api/projects/{pid}/script", None),
    ("POST", f"/api/projects/{pid}/beats", None),
    ("POST", f"/api/projects/{pid}/prompts", None),
    ("POST", f"/api/projects/{pid}/render", {}),
    ("POST", f"/api/projects/{pid}/join", None),
    ("POST", f"/api/projects/{pid}/run", None),
]

failures = []
with httpx.Client(base_url=BASE, timeout=30) as c:
    for method, path, body in ROUTES:
        c.post("/api/cancel")            # free the slot from the previous job
        time.sleep(0.4)
        r = c.request(method, path, json=body) if body is not None else c.request(method, path)
        label = f"{method} {path}"

        if r.status_code != 200:
            failures.append(f"{label} -> {r.status_code} {r.text[:160]}")
            continue
        # The job is scheduled; it may still fail against the dead upstream, but
        # scheduling itself must not error.
        assert "started" in r.json(), (label, r.text)
        print(f"  ok  {label} scheduled")

    # a 409 must only ever mean a genuinely busy runner
    c.post("/api/cancel")
    time.sleep(0.4)
    first = c.post(f"/api/projects/{pid}/run")
    assert first.status_code == 200, first.text
    second = c.post(f"/api/projects/{pid}/script")
    if second.status_code == 409:
        assert "Already running" in second.json()["detail"], second.text
        assert second.headers.get("X-Busy-Label"), second.headers
        print("  ok  409 only when genuinely busy, and it says which job")
    c.post("/api/cancel")

    # a job that fails reports the failure rather than vanishing
    time.sleep(0.5)
    c.post(f"/api/projects/{pid}/script")
    time.sleep(6)
    st = c.get("/api/status").json()
    assert st["busy"] is False, f"a failed job should free the slot: {st}"
    print("  ok  a job failing against a dead upstream frees the slot")

if failures:
    print("\nFAILURES:")
    for f in failures:
        print("  " + f)
    sys.exit(1)

print("\nall route assertions passed")
