"""HTTP surface for the pipeline."""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import comfy, config, db, openwebui, pipeline, prompts, workflow

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="vidpipe")
db.init()


# --------------------------------------------------------------------------
# settings & connections
# --------------------------------------------------------------------------

@app.get("/api/settings")
def get_settings():
    cfg = config.load()
    return {
        **cfg,
        "openwebui_key": "••••" if cfg["openwebui_key"] else "",
        "has_key": bool(cfg["openwebui_key"]),
        "defaults": {
            "script_system": prompts.SCRIPT_SYSTEM,
            "segment_system": prompts.SEGMENT_SYSTEM,
            "beat_system": prompts.PLAN_SYSTEM,
            "script_user": prompts.SCRIPT_USER,
            "beat_user": prompts.PLAN_USER,
            "segment_user": prompts.SEGMENT_USER,
            "outline_system": prompts.OUTLINE_SYSTEM,
            "outline_user": prompts.OUTLINE_USER,
            "chapter_system": prompts.CHAPTER_SYSTEM,
            "chapter_user": prompts.CHAPTER_USER,
        },
    }


@app.post("/api/settings")
async def post_settings(patch: dict):
    if patch.get("openwebui_key") == "••••":
        patch.pop("openwebui_key")
    if "openwebui_url" in patch or "openwebui_key" in patch:
        openwebui.forget()
    config.save(patch)
    return get_settings()


@app.get("/api/models")
async def models():
    cfg = config.load()
    try:
        return {"models": await openwebui.list_models(cfg["openwebui_url"], cfg["openwebui_key"])}
    except Exception as exc:
        raise HTTPException(502, f"Open WebUI: {exc}")


@app.post("/api/upstream/unload")
async def llm_unload():
    cfg = config.load()
    if not cfg.get("llm_unload_url"):
        raise HTTPException(400, "No unload URL set. Add one under Settings → Connections.")
    from . import swap
    target = cfg["llm_unload_url"].rstrip("/") + cfg["llm_unload_path"]
    try:
        return {"detail": await swap.unload(cfg["llm_unload_url"], cfg["llm_unload_path"],
                                            cfg.get("llm_running_path", "/running"))}
    except Exception as exc:
        raise HTTPException(502, f"POST {target} failed: {exc}")


@app.get("/api/health")
async def health():
    cfg = config.load()
    out = {"openwebui": None, "comfy": None}
    try:
        found = await openwebui.probe(cfg["openwebui_url"], cfg["openwebui_key"])
        if found["error"]:
            out["openwebui"] = {"ok": False, "detail": found["error"][:300]}
        else:
            detail = f"{found['models']} models via {found['models_path']}"
            if found["chat_path"]:
                detail += f", chat on {found['chat_path']}"
            else:
                detail += ", but no chat endpoint answered"
            out["openwebui"] = {"ok": bool(found["chat_path"]), "detail": detail}
    except Exception as exc:
        out["openwebui"] = {"ok": False, "detail": str(exc)[:300]}
    try:
        stats = await comfy.ComfyClient(cfg["comfy_url"]).system_stats()
        name = (stats.get("devices") or [{}])[0].get("name", "connected")
        out["comfy"] = {"ok": True, "detail": name}
    except Exception as exc:
        out["comfy"] = {"ok": False, "detail": str(exc)[:200]}
    return out


# --------------------------------------------------------------------------
# workflow
# --------------------------------------------------------------------------

@app.get("/api/workflow")
def get_workflow():
    if not config.WORKFLOW_PATH.exists():
        return {"loaded": False}
    graph = json.loads(config.WORKFLOW_PATH.read_text())
    roles = workflow.detect_roles(graph)
    return {"loaded": True, "summary": workflow.summarize(graph, roles), "roles": roles}


@app.post("/api/workflow")
async def post_workflow(file: UploadFile = File(...)):
    try:
        doc = json.loads((await file.read()).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(400, f"That file isn't valid JSON: {exc}")

    converted = False
    if not workflow.is_api_format(doc):
        object_info = {}
        try:
            object_info = await comfy.ComfyClient(config.load()["comfy_url"]).object_info()
        except Exception:
            pass
        doc = workflow.ui_to_api(doc, object_info)
        converted = True
        if not object_info:
            for node in doc.values():
                if "_widgets_values" in node.get("inputs", {}):
                    raise HTTPException(
                        400,
                        "This is an editor-format workflow and ComfyUI wasn't reachable to "
                        "read its node schema. Start ComfyUI and retry, or export the "
                        "workflow with Workflow → Export (API).",
                    )
    config.WORKFLOW_PATH.write_text(json.dumps(doc, indent=2))
    result = get_workflow()
    result["converted"] = converted
    return result


# --------------------------------------------------------------------------
# assets
# --------------------------------------------------------------------------

@app.get("/api/capacity")
def capacity():
    """Slot budget: how many static references fit, given the reserved frame slot."""
    out = {"image_slots": 0, "audio_slots": 0, "reserved_image": 0,
           "max_static_images": 0, "max_audios": 0}
    if not config.WORKFLOW_PATH.exists():
        return out
    graph = json.loads(config.WORKFLOW_PATH.read_text())
    roles = workflow.detect_roles(graph)
    out["image_slots"] = len(roles.get("ref_images") or [])
    out["audio_slots"] = len(roles.get("ref_audios") or [])
    out["reserved_image"] = 1 if out["image_slots"] else 0
    out["max_static_images"] = max(0, out["image_slots"] - out["reserved_image"])
    out["max_audios"] = out["audio_slots"]
    return out


@app.get("/api/assets")
def get_assets():
    return {"assets": db.list_assets()}


@app.post("/api/assets")
async def post_asset(file: UploadFile = File(...), kind: str = Form("image")):
    safe = "".join(c for c in Path(file.filename or "upload").name if c.isalnum() or c in "._- ")
    local = config.DATA / "assets" / f"{int(time.time()*1000)}_{safe}"
    with local.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    try:
        comfy_name = await comfy.ComfyClient(config.load()["comfy_url"]).upload(local)
    except Exception as exc:
        local.unlink(missing_ok=True)
        raise HTTPException(502, f"Couldn't send that to ComfyUI: {exc}")
    return db.add_asset(kind, safe, str(local), comfy_name)


class AssetEdit(BaseModel):
    description: str | None = None
    label: str | None = None


@app.patch("/api/assets/{aid}")
def patch_asset(aid: int, body: AssetEdit):
    if not db.get_asset(aid):
        raise HTTPException(404, "Asset not found.")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    return db.update_asset(aid, **fields)


@app.delete("/api/assets/{aid}")
def remove_asset(aid: int):
    asset = db.get_asset(aid)
    if asset:
        Path(asset["local_path"]).unlink(missing_ok=True)
        db.delete_asset(aid)
    return {"ok": True}


@app.get("/api/assets/{aid}/file")
def asset_file(aid: int):
    asset = db.get_asset(aid)
    if not asset or not Path(asset["local_path"]).exists():
        raise HTTPException(404, "Asset not found.")
    return FileResponse(asset["local_path"])


# --------------------------------------------------------------------------
# projects
# --------------------------------------------------------------------------

class NewProject(BaseModel):
    name: str = "Untitled"
    idea: str = ""


@app.get("/api/projects")
def projects():
    return {"projects": db.list_projects()}


@app.post("/api/projects")
def new_project(body: NewProject):
    return db.create_project(body.name or "Untitled", body.idea)


@app.get("/api/projects/{pid}")
def project(pid: int):
    p = db.get_project(pid)
    if not p:
        raise HTTPException(404, "Project not found.")
    return p


@app.patch("/api/projects/{pid}")
def patch_project(pid: int, body: dict):
    if not db.get_project(pid):
        raise HTTPException(404, "Project not found.")
    if "settings" in body:
        merged = db.get_project(pid)["settings"]
        merged.update(body["settings"])
        body["settings"] = merged
        db.ensure_segments(pid, int(merged.get("segment_count", 4)))
    return db.update_project(pid, **body)


@app.delete("/api/projects/{pid}")
def remove_project(pid: int):
    db.delete_project(pid)
    return {"ok": True}


class SegmentEdit(BaseModel):
    prompt: str


@app.patch("/api/projects/{pid}/segments/{idx}")
def patch_segment(pid: int, idx: int, body: SegmentEdit):
    seg = db.update_segment(pid, idx, prompt=body.prompt,
                            status="ready" if body.prompt.strip() else "empty")
    if not seg:
        raise HTTPException(404, "Segment not found.")
    return seg


# --------------------------------------------------------------------------
# runs
# --------------------------------------------------------------------------

def _start(coro, label: str):
    """Schedule a job. Must be called from the event loop, not a worker thread."""
    try:
        pipeline.runner.start(coro, label)
    except pipeline.Busy as exc:
        coro.close()
        # Header values must be latin-1; job labels contain "·".
        safe_label = pipeline.runner.label.encode("ascii", "replace").decode("ascii")
        raise HTTPException(409, str(exc), headers={
            "X-Busy-Label": safe_label,
            "X-Busy-Elapsed": str(round(pipeline.runner.elapsed, 1)),
        })
    except RuntimeError as exc:
        # Anything else here is a bug on our side, not a busy runner. Reporting
        # it as 409 once sent people hunting for a job that did not exist.
        coro.close()
        raise HTTPException(500, f"Couldn't start the job: {exc}")
    return {"started": label}


@app.post("/api/projects/{pid}/script")
async def run_script(pid: int):
    return _start(pipeline.generate_script(pid), f"Script · project {pid}")


@app.get("/api/projects/{pid}/preview")
def preview(pid: int, stage: str = "segment", index: int = 0):
    """Exactly what would be sent for a stage — same builder the run uses."""
    if not db.get_project(pid):
        raise HTTPException(404, "Project not found.")
    try:
        return pipeline.build_request(pid, stage, index)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/projects/{pid}/outline")
async def run_outline(pid: int):
    return _start(pipeline.plan_outline(pid), f"Outline · project {pid}")


@app.post("/api/projects/{pid}/chapters")
async def run_chapters(pid: int, index: int | None = None):
    label = f"Chapters · project {pid}" + (f" · {index+1}" if index is not None else "")
    return _start(pipeline.write_chapters(pid, only=index), label)


class ChapterEdit(BaseModel):
    brief: str | None = None
    text: str | None = None


@app.patch("/api/projects/{pid}/chapters/{idx}")
def patch_chapter(pid: int, idx: int, body: ChapterEdit):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    chapter = db.update_chapter(pid, idx, **fields)
    if not chapter:
        raise HTTPException(404, "Chapter not found.")
    if "text" in fields:
        try:
            pipeline.assemble_story(pid)
        except RuntimeError:
            pass
    return chapter


@app.post("/api/projects/{pid}/assemble")
def run_assemble(pid: int):
    try:
        return {"script": pipeline.assemble_story(pid)}
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/projects/{pid}/beats")
async def run_beats(pid: int):
    return _start(pipeline.plan_beats(pid), f"Beats · project {pid}")


@app.post("/api/projects/{pid}/join")
async def run_join(pid: int):
    return _start(pipeline.join_clips(pid), f"Join clips · project {pid}")


class BeatEdit(BaseModel):
    beat: str


@app.patch("/api/projects/{pid}/segments/{idx}/beat")
def patch_beat(pid: int, idx: int, body: BeatEdit):
    seg = db.update_segment(pid, idx, beat=body.beat)
    if not seg:
        raise HTTPException(404, "Segment not found.")
    return seg


@app.get("/api/projects/{pid}/final")
def final_video(pid: int):
    project = db.get_project(pid)
    if not project or not project["final_video"] or not Path(project["final_video"]).exists():
        raise HTTPException(404, "The clips haven't been joined yet.")
    return FileResponse(project["final_video"], media_type="video/mp4",
                        filename=f"{project['name']}.mp4")


@app.post("/api/projects/{pid}/prompts")
async def run_prompts(pid: int, index: int | None = None):
    label = f"Prompts · project {pid}" + (f" · segment {index+1}" if index is not None else "")
    return _start(pipeline.generate_prompts(pid, only=index), label)


class RenderBody(BaseModel):
    indices: list[int] | None = None


@app.post("/api/projects/{pid}/render")
async def run_render(pid: int, body: RenderBody | None = None):
    indices = body.indices if body else None
    return _start(pipeline.render(pid, only=indices), f"Render · project {pid}")


@app.post("/api/projects/{pid}/run")
async def run_everything(pid: int):
    return _start(pipeline.run_all(pid), f"Full run · project {pid}")


@app.post("/api/cancel")
async def cancel():
    pipeline.runner.cancel()
    try:
        await comfy.ComfyClient(config.load()["comfy_url"]).interrupt()
    except Exception:
        pass
    return {"ok": True}


@app.get("/api/status")
def status():
    return {"busy": pipeline.runner.busy, "label": pipeline.runner.label,
            "elapsed": round(pipeline.runner.elapsed, 1)}


@app.get("/api/projects/{pid}/segments/{idx}/video")
def segment_video(pid: int, idx: int):
    seg = db.get_segment(pid, idx)
    if not seg or not seg["video"] or not Path(seg["video"]).exists():
        raise HTTPException(404, "No render for that segment yet.")
    return FileResponse(seg["video"], media_type="video/mp4")


@app.websocket("/ws")
async def ws(socket: WebSocket):
    await socket.accept()
    q = pipeline.bus.subscribe()
    try:
        await socket.send_json({"type": "hello", "busy": pipeline.runner.busy,
                                "label": pipeline.runner.label,
                                "elapsed": round(pipeline.runner.elapsed, 1)})
        while True:
            event = await q.get()
            await socket.send_json(event)
    except (WebSocketDisconnect, asyncio.CancelledError, RuntimeError):
        pass
    finally:
        pipeline.bus.unsubscribe(q)


@app.exception_handler(Exception)
async def unhandled(request, exc):
    return JSONResponse(status_code=500, content={"detail": str(exc)})


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
