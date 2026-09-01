"""Long-story mode: plan, write chapter by chapter, assemble."""
import asyncio
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["VIDPIPE_DATA"] = tempfile.mkdtemp(prefix="vidpipe-story-")

import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402

CALLS = []
ow = FastAPI()


@ow.post("/api/chat/completions")
async def chat(req: Request):
    body = await req.json()
    user = body["messages"][-1]["content"]
    if "Plan this as a story" in user:
        CALLS.append(("outline", body["model"], len(user)))
        return {"choices": [{"message": {"content": "\n\n".join(
            f"=== CHAPTER {i} ===\nChapter {i} plan." for i in range(1, 4))}}]}
    CALLS.append(("chapter", body["model"], len(user)))
    n = user.split("Write chapter ")[1].split(" ")[0]
    prev = "CONTINUES" if "PREVIOUS CHAPTER" in user else "OPENING"
    return {"choices": [{"message": {"content": f"Prose of chapter {n} ({prev})."}}]}


threading.Thread(target=uvicorn.Server(uvicorn.Config(
    ow, host="127.0.0.1", port=8831, log_level="error")).run, daemon=True).start()
time.sleep(1.5)

from app import config, db, pipeline  # noqa: E402


def check(label, cond, detail=""):
    assert cond, f"{label} failed {detail}"
    print(f"  ok  {label}")


db.init()
p = db.create_project("story", "a woman walks home through a storm")
pid = p["id"]
db.update_project(pid, settings={**p["settings"], "story_mode": True, "chapter_count": 3})
config.save({"openwebui_url": "http://127.0.0.1:8831", "script_model": "writer",
             "outline_model": "planner", "chapter_model": "novelist"})

asyncio.run(pipeline.plan_outline(pid))
project = db.get_project(pid)
check("outline stored", "CHAPTER 1" in project["outline"])
check("three chapters created", len(project["chapters"]) == 3)
check("each chapter got its own brief",
      [c["brief"] for c in project["chapters"]] ==
      ["Chapter 1 plan.", "Chapter 2 plan.", "Chapter 3 plan."])
check("outline used the outline model", CALLS[0][1] == "planner")

asyncio.run(pipeline.write_chapters(pid))
project = db.get_project(pid)
check("one call per chapter, on the chapter model",
      [c[0] for c in CALLS] == ["outline"] + ["chapter"] * 3
      and all(c[1] == "novelist" for c in CALLS[1:]), CALLS)
check("chapter 1 had no previous chapter",
      "OPENING" in project["chapters"][0]["text"])
check("later chapters continue from the one before",
      all("CONTINUES" in c["text"] for c in project["chapters"][1:]))
check("all chapters done", all(c["status"] == "done" for c in project["chapters"]))

# assembled into the script the later stages read
check("chapters assembled into the script",
      project["script"] == "\n\n".join(c["text"] for c in project["chapters"]),
      project["script"])
check("the script is what stage 2 would segment",
      "Prose of chapter 1" in project["script"] and "Prose of chapter 3" in project["script"])

# editing one chapter re-assembles without touching the others
db.update_chapter(pid, 1, text="REWRITTEN MIDDLE")
pipeline.assemble_story(pid)
after = db.get_project(pid)["script"]
check("a hand-edited chapter flows into the script", "REWRITTEN MIDDLE" in after)
check("the other chapters are untouched",
      "Prose of chapter 1" in after and "Prose of chapter 3" in after)

# rewriting a single chapter is one call
before = len(CALLS)
asyncio.run(pipeline.write_chapters(pid, only=2))
check("rewriting one chapter makes exactly one call", len(CALLS) == before + 1)

# each chapter call stays short — that is the point of the whole mode
sizes = [n for kind, _, n in CALLS if kind == "chapter"]
check("no chapter call carries the whole story",
      max(sizes) < 900, sizes)

# short mode still works and skips the chapter machinery
p2 = db.create_project("short", "an idea")
db.update_project(p2["id"], settings={**p2["settings"], "story_mode": False})
r = pipeline.build_request(p2["id"], "script")
check("short mode still builds a plain script request",
      "Idea:" in r["messages"][-1]["content"])

print("\nall story assertions passed")
