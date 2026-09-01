"""End-to-end run against stub Open WebUI and ComfyUI servers.

Exercises: script generation, per-segment prompting with previous-segment context,
graph patching, queueing, output download, last-frame extraction and carry-forward.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="vidpipe-test-")
os.environ["VIDPIPE_DATA"] = TMP

import uvicorn  # noqa: E402
from fastapi import FastAPI, Request, UploadFile, File, Form  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402

from app import comfy, config, db, pipeline, workflow  # noqa: E402
from tests.test_workflow import OI  # noqa: E402

RECEIVED = {"chats": [], "graphs": [], "uploads": [], "unloads": [], "order": []}
COMFY_DIR = Path(TMP) / "comfy_out"
COMFY_DIR.mkdir(parents=True, exist_ok=True)


def make_clip(path: Path, seconds=1):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i",
         f"testsrc=size=320x180:rate=24:duration={seconds}",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )


# --- stub Open WebUI ------------------------------------------------------
ow = FastAPI()


@ow.get("/api/models")
def ow_models():
    return {"data": [{"id": "writer", "name": "Writer"}, {"id": "segmenter", "name": "Segmenter"}]}


@ow.post("/api/chat/completions")
async def ow_chat(req: Request):
    body = await req.json()
    RECEIVED["chats"].append(body)
    user = body["messages"][-1]["content"]
    if body["model"] == "writer":
        RECEIVED["order"].append("script")
        text = "She opens the shutters, pulls on a coat, then steps outside."
    elif "Split this into" in user:
        RECEIVED["order"].append("plan")
        # fenced + preamble, to exercise the tolerant parser
        text = ("Sure, here's the split:\n\n"
                "=== SEGMENT 1 ===\n"
                "She crosses the room and opens the shutters.\n\n"
                "=== SEGMENT 2 ===\n"
                "She lifts a coat from the chair and pulls it on.\n\n"
                "=== SEGMENT 3 ===\n"
                "She steps through the doorway onto wet stone.")
    else:
        RECEIVED["order"].append("segment")
        n = user.split("Write segment ")[1].split(" ")[0]
        cont = "CONTINUES" if "PREVIOUS SEGMENT" in user else "OPENING"
        text = f"```\nSegment {n}: [Shot {n}] {cont} prompt body for segment {n}.\n```"
    return {"choices": [{"message": {"content": text}}]}


# --- stub llama-swap ------------------------------------------------------
swap_app = FastAPI()
LOADED = {"models": ["qwen3-32b"]}


@swap_app.post("/api/models/unload")
def swap_unload():
    RECEIVED["unloads"].append(len(RECEIVED["graphs"]))
    LOADED["models"] = []
    return {"ok": True}


@swap_app.get("/running")
def swap_running():
    return {"running": LOADED["models"]}


# --- stub ComfyUI ---------------------------------------------------------
cf = FastAPI()
HISTORY: dict[str, dict] = {}


@cf.get("/system_stats")
def stats():
    return {"devices": [{"name": "stub-gpu"}]}


@cf.get("/object_info")
def object_info():
    return OI


@cf.post("/upload/image")
async def upload(image: UploadFile = File(...), subfolder: str = Form(""),
                 type: str = Form("input"), overwrite: str = Form("true")):
    RECEIVED["uploads"].append(image.filename)
    return {"name": image.filename, "subfolder": subfolder, "type": type}


@cf.post("/prompt")
async def queue(req: Request):
    body = await req.json()
    assert not LOADED["models"], "ComfyUI was queued while the LLM still held VRAM"
    RECEIVED["order"].append("render")
    RECEIVED["graphs"].append(body["prompt"])
    pid_ = f"stub-{len(RECEIVED['graphs'])}"
    name = f"out_{len(RECEIVED['graphs'])}.mp4"
    make_clip(COMFY_DIR / name)
    HISTORY[pid_] = {
        "status": {"status_str": "success", "completed": True},
        "outputs": {"200": {"images": [
            {"filename": name, "subfolder": "", "type": "output"}]}},
    }
    return {"prompt_id": pid_}


@cf.get("/history/{pid_}")
def history(pid_: str):
    return {pid_: HISTORY.get(pid_, {})} if pid_ in HISTORY else {}


@cf.get("/view")
def view(filename: str, subfolder: str = "", type: str = "output"):
    path = COMFY_DIR / filename
    if not path.exists():
        return JSONResponse({"detail": "missing"}, status_code=404)
    return FileResponse(path)


@cf.post("/interrupt")
def interrupt():
    return {"ok": True}


def serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    return server


async def main():
    serve(ow, 8801)
    serve(cf, 8802)
    serve(swap_app, 8803)
    await asyncio.sleep(1.2)

    config.save({
        "openwebui_url": "http://127.0.0.1:8801",
        "openwebui_key": "test-key",
        "comfy_url": "http://127.0.0.1:8802",
        "script_model": "writer",
        "segment_model": "segmenter",
        "llm_unload_url": "http://127.0.0.1:8803",
        "llm_unload_path": "/api/models/unload",
        "llm_running_path": "/running",
    })

    ui = json.loads((ROOT / "tests" / "fixtures" / "workflow.ui.json").read_text())
    api_graph = workflow.ui_to_api(ui, OI)
    config.WORKFLOW_PATH.write_text(json.dumps(api_graph))

    db.init()
    project = db.create_project("test", "a woman opens the shutters at dawn")
    pid_ = project["id"]

    # one static reference image, uploaded through the real asset path
    img = Path(TMP) / "ref.png"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1",
                    "-frames:v", "1", str(img)], check=True, capture_output=True)
    name = await comfy.ComfyClient("http://127.0.0.1:8802").upload(img)
    asset = db.add_asset("image", "ref.png", str(img), name)
    img2 = Path(TMP) / "ref2.png"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1",
                    "-frames:v", "1", str(img2)], check=True, capture_output=True)
    name2 = await comfy.ComfyClient("http://127.0.0.1:8802").upload(img2)
    asset2 = db.add_asset("image", "ref2.png", str(img2), name2)

    db.update_project(pid_, settings={
        **project["settings"],
        "segment_count": 3, "duration": 15.0, "steps": 8,
        "ref_images": [asset["id"], asset2["id"]], "ref_image_count": 2,
        "ref_audios": [], "ref_audio_count": 0, "continuation": True,
        "plan_beats": True, "concat": True,
    })

    await pipeline.run_all(pid_)

    # --- assertions ---
    done = db.get_project(pid_)
    # order: script, then the planning pass, then segments, then renders
    assert RECEIVED["order"] == ["script", "plan"] + ["segment"] * 3 + ["render"] * 3, \
        RECEIVED["order"]
    assert len(RECEIVED["chats"]) == 5, len(RECEIVED["chats"])  # script + plan + 3 segments

    # beats were parsed out of a fenced reply and stored per segment
    beats = done["beats"]
    assert len(beats) == 3 and beats[1].startswith("She lifts a coat"), beats
    for i, seg in enumerate(done["segments"]):
        assert seg["beat"] == beats[i], seg

    # each segment prompt call carried its own beat
    for i in range(3):
        msg = RECEIVED["chats"][2 + i]["messages"][-1]["content"]
        assert "THIS SEGMENT COVERS EXACTLY THIS" in msg
        assert beats[i] in msg, f"segment {i + 1} got the wrong beat"

    # the LLM was unloaded before any ComfyUI work
    assert RECEIVED["unloads"] == [0], RECEIVED["unloads"]

    assert "PREVIOUS SEGMENT" not in RECEIVED["chats"][2]["messages"][-1]["content"]
    for i in (3, 4):
        msg = RECEIVED["chats"][i]["messages"][-1]["content"]
        assert "PREVIOUS SEGMENT" in msg, f"segment {i} lost its context"
        assert "FULL SCRIPT" in msg, f"segment {i} lost the full script"
        assert f"prompt body for segment {i - 2}" in msg, "wrong previous segment passed"
        # the fence and "Segment N:" label were stripped before storage
        assert "```" not in msg

    assert len(RECEIVED["graphs"]) == 3
    for i, g in enumerate(RECEIVED["graphs"]):
        prompt_text = g["212"]["inputs"]["text1"]
        assert f"segment {i + 1}" in prompt_text
        assert not prompt_text.startswith("```") and not prompt_text.lower().startswith("segment"), \
            f"cleanup left noise: {prompt_text[:60]}"
        assert g["202"]["inputs"]["value"] == 15.0
        assert g["195"]["inputs"]["steps"] == 8
        assert g["200"]["inputs"]["filename_prefix"].endswith(f"s{i:02d}")
    seeds = [g["193"]["inputs"]["noise_seed"] for g in RECEIVED["graphs"]]
    assert len(set(seeds)) == 3, "seed -1 should give a new seed per segment"

    # Segment 1: two static references, nothing carried, third slot unused.
    g0 = RECEIVED["graphs"][0]
    assert g0["226"]["inputs"]["image"] == "vidpipe/ref.png"
    assert g0["227"]["inputs"]["image"] == "vidpipe/ref2.png"
    assert "228" not in g0, "an empty third slot should be removed"

    # Later segments: statics keep their slots and the carried frame is appended
    # as reference 3 — it does not displace anything.
    for i in (1, 2):
        g = RECEIVED["graphs"][i]
        assert g["226"]["inputs"]["image"] == "vidpipe/ref.png"
        assert g["227"]["inputs"]["image"] == "vidpipe/ref2.png"
        assert g["228"]["inputs"]["image"].endswith(f"p{pid_}_s{i - 1:02d}_last.png"), \
            g["228"]["inputs"]["image"]

    for seg in done["segments"]:
        assert seg["status"] == "done", seg
        assert Path(seg["video"]).exists(), seg
        assert seg["last_frame"], "last frame was not captured"

    frames = [u for u in RECEIVED["uploads"] if u.endswith("_last.png")]
    assert len(frames) == 3, frames

    # clips were joined into one file, and it is longer than any single clip
    final = done["final_video"]
    assert final and Path(final).exists(), "no joined video"

    def seconds(path):
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, check=True)
        return float(out.stdout.strip())

    total, one = seconds(final), seconds(done["segments"][0]["video"])
    assert total > one * 2.5, f"joined video is {total:.2f}s, one clip is {one:.2f}s"
    print(f"joined: {total:.2f}s from 3 x {one:.2f}s clips")

    print(f"chats: {len(RECEIVED['chats'])}  graphs: {len(RECEIVED['graphs'])}  "
          f"frames carried: {len(frames)}")
    print("all pipeline assertions passed")


if __name__ == "__main__":
    asyncio.run(main())
