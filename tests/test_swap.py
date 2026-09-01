"""End-to-end check of the unload flow against a llama-swap lookalike on :9292."""
import asyncio, os, sys, tempfile, threading, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["VIDPIPE_DATA"] = tempfile.mkdtemp()
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

hits = []
swap_app = FastAPI()
STATE = {"loaded": True}

@swap_app.post("/api/models/unload")
def unload():
    hits.append("POST /api/models/unload")
    STATE["loaded"] = False
    return {"ok": True}

@swap_app.get("/running")
def running():
    hits.append("GET /running")
    return JSONResponse({"running": [] if not STATE["loaded"] else [{"model": "q", "state": "running"}]})

threading.Thread(target=uvicorn.Server(uvicorn.Config(swap_app, host="127.0.0.1", port=9292, log_level="error")).run, daemon=True).start()
time.sleep(1.2)

from app import config
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)

# exactly what you typed, trailing slash included
c.post("/api/settings", json={"llm_unload_url": "http://127.0.0.1:9292/",
                              "llm_unload_path": "/api/models/unload"})
r = c.post("/api/upstream/unload")
print("status:", r.status_code)
print("detail:", r.json().get("detail"))
print("upstream saw:", hits)
assert r.status_code == 200, r.text
assert hits[0] == "POST /api/models/unload", hits
assert "9292/api/models/unload" in r.json()["detail"], r.json()

# and the render path uses the same settings
STATE["loaded"] = True; hits.clear()
from app import swap
note = asyncio.run(swap.unload_quietly(config.load()["llm_unload_url"],
                                       config.load()["llm_unload_path"],
                                       config.load()["llm_running_path"]))
print("render-path note:", note)
assert hits[0] == "POST /api/models/unload"

# a wrong path reports the URL it tried instead of failing vaguely
c.post("/api/settings", json={"llm_unload_path": "/nope/unload"})
r = c.post("/api/upstream/unload")
print("bad path ->", r.status_code, r.json()["detail"][:90])
assert "9292/nope/unload" in r.json()["detail"]
print("\nall swap assertions passed")
