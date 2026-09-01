"""A segmenter that returns the wrong number of blocks must not kill the run."""
import asyncio
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["VIDPIPE_DATA"] = tempfile.mkdtemp(prefix="vidpipe-mismatch-")

import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402

BEHAVIOUR = {"mode": "short_then_correct"}
CALLS = []
ow = FastAPI()


def blocks(n):
    return "\n\n".join(f"=== SEGMENT {i} ===\nThing {i} happens." for i in range(1, n + 1))


@ow.post("/api/chat/completions")
async def chat(req: Request):
    body = await req.json()
    turns = len(body["messages"])
    CALLS.append(turns)
    mode = BEHAVIOUR["mode"]
    corrective = any(m["role"] == "assistant" for m in body["messages"])
    if mode == "short_then_correct":
        return {"choices": [{"message": {"content": blocks(6 if corrective else 4)}}]}
    if mode == "always_short":
        return {"choices": [{"message": {"content": blocks(4)}}]}
    if mode == "unreadable":
        return {"choices": [{"message": {"content": "I'm afraid I can't do that."}}]}
    return {"choices": [{"message": {"content": blocks(6)}}]}


threading.Thread(target=uvicorn.Server(uvicorn.Config(
    ow, host="127.0.0.1", port=8841, log_level="error")).run, daemon=True).start()
time.sleep(1.5)

from app import config, db, pipeline  # noqa: E402


def check(label, cond, detail=""):
    assert cond, f"{label} failed {detail}"
    print(f"  ok  {label}")


db.init()
config.save({"openwebui_url": "http://127.0.0.1:8841", "segment_model": "seg"})


def fresh(count=6, adopt=True):
    p = db.create_project("m", "idea")
    db.update_project(p["id"], script="A SCRIPT",
                      settings={**p["settings"], "segment_count": count,
                                "adopt_segment_count": adopt})
    return p["id"]


# 1. the model corrects itself when shown its own answer
CALLS.clear()
pid = fresh()
beats = asyncio.run(pipeline.plan_beats(pid))
check("a corrective second turn fixes the count", len(beats) == 6, len(beats))
check("it took exactly two calls", len(CALLS) == 2, CALLS)
check("the retry included the model's own reply", CALLS[1] > CALLS[0], CALLS)
check("six segments exist", len(db.get_project(pid)["segments"]) == 6)

# 2. a model that will not comply: adopt its count rather than fail
BEHAVIOUR["mode"] = "always_short"
CALLS.clear()
pid = fresh()
beats = asyncio.run(pipeline.plan_beats(pid))
check("an uncooperative model does not fail the run", len(beats) == 4, len(beats))
check("the project follows the model's count",
      db.get_project(pid)["settings"]["segment_count"] == 4)
check("segments match the new count", len(db.get_project(pid)["segments"]) == 4)
check("every segment has a brief",
      all(s["beat"] for s in db.get_project(pid)["segments"]))

# 3. with adoption off, the count is forced
pid = fresh(adopt=False)
beats = asyncio.run(pipeline.plan_beats(pid))
check("adoption off pads to the requested count", len(beats) == 6, len(beats))
check("the project keeps its count",
      db.get_project(pid)["settings"]["segment_count"] == 6)

# 4. genuinely unreadable output still raises, with the reply shown
BEHAVIOUR["mode"] = "unreadable"
pid = fresh()
raised = None
try:
    asyncio.run(pipeline.plan_beats(pid))
except RuntimeError as exc:
    raised = str(exc)
check("unreadable output raises", raised is not None)
check("the error quotes what the model said",
      "I'm afraid I can't do that." in raised, raised)

print("\nall mismatch assertions passed")
