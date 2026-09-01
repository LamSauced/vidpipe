"""The three stages, plus the event bus the browser listens on."""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from pathlib import Path
from typing import Any

from . import comfy, config, db, openwebui, prompts, swap, video, workflow


# --------------------------------------------------------------------------
# event bus
# --------------------------------------------------------------------------

class Bus:
    def __init__(self):
        self._subs: set[asyncio.Queue] = set()
        self.log: list[dict] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def emit(self, event: dict) -> None:
        event = {"t": time.time(), **event}
        self.log = (self.log + [event])[-300:]
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


bus = Bus()


class Cancelled(RuntimeError):
    pass


class Busy(RuntimeError):
    """Raised only when a job already holds the slot."""


class Runner:
    """One job at a time. Keeps the cancel flag and the current job label."""

    def __init__(self):
        self.task: asyncio.Task | None = None
        self.label: str = ""
        self.started_at: float = 0.0
        self._cancel = False

    @property
    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    @property
    def elapsed(self) -> float:
        return time.time() - self.started_at if self.busy else 0.0

    #: A job older than this is presumed wedged and gets displaced by the next
    #: request. Long enough not to interrupt a real render, short enough that a
    #: stuck job can't lock the app until someone restarts the server.
    STALE_AFTER = 900.0

    def start(self, coro, label: str, preempt_stale: bool = True) -> None:
        if self.busy:
            age = self.elapsed
            if preempt_stale and age > self.STALE_AFTER:
                bus.emit({"type": "warning",
                          "message": f"Dropped a stuck job ({self.label}, {age:.0f}s old)."})
                self.cancel()
            else:
                raise Busy(
                    f"Already running: {self.label} (started {age:.0f}s ago). "
                    f"Press Stop to end it."
                )
        try:
            asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError(
                "Runner.start must be called from the event loop — the route "
                "handler needs to be 'async def'."
            ) from exc
        self._cancel = False
        self.label = label
        self.started_at = time.time()
        self.task = asyncio.create_task(self._wrap(coro))

    async def _wrap(self, coro):
        try:
            await coro
        except Cancelled:
            bus.emit({"type": "cancelled", "label": self.label})
        except asyncio.CancelledError:
            # Stop cancels the task outright, which lands here rather than as
            # Cancelled when the job was parked inside an await.
            bus.emit({"type": "cancelled", "label": self.label})
        except Exception as exc:  # surfaced in the UI, not swallowed
            bus.emit({"type": "failed", "label": self.label, "message": str(exc)})
        finally:
            bus.emit({"type": "idle"})

    def cancel(self) -> None:
        """Stop the current job now.

        Setting the flag alone only helps between steps — a job parked inside a
        long HTTP call would ignore it and hold the slot until that call timed
        out, blocking every later request with a 409. So cancel the task too.
        """
        self._cancel = True
        task = self.task
        if task is not None and not task.done():
            task.cancel()

    def check(self) -> None:
        if self._cancel:
            raise Cancelled()


runner = Runner()


# --------------------------------------------------------------------------
# workflow access
# --------------------------------------------------------------------------

def load_workflow() -> tuple[dict, dict]:
    if not config.WORKFLOW_PATH.exists():
        raise RuntimeError("No workflow loaded. Add one on the Workflow tab first.")
    graph = json.loads(config.WORKFLOW_PATH.read_text())
    return graph, workflow.detect_roles(graph)


# --------------------------------------------------------------------------
# request building
#
# Every call goes through one of these, and the preview endpoint calls the same
# functions — so what you inspect is exactly what gets sent, never a
# reconstruction that can drift from the real thing.
# --------------------------------------------------------------------------

def reference_descriptions(pid: int, index: int) -> list[str]:
    """Descriptions in the same order the render assigns slots.

    Static references first, capped by the slot count, then the carried frame —
    matching _slot_values, so "Reference 3" here is <Picture 3> there.
    """
    project = db.get_project(pid)
    if not project:
        return []
    s = project["settings"]
    wanted = s.get("ref_image_count")
    ids = list(s.get("ref_images") or [])
    if isinstance(wanted, int):
        ids = ids[:max(0, wanted)]

    out = []
    for aid in ids:
        asset = db.get_asset(aid)
        if not asset:
            continue
        text = (asset.get("description") or "").strip()
        out.append(text or f"(no description given — file {asset['label']})")

    if s.get("continuation") and index > 0:
        template = config.load().get("carried_frame_description") or ""
        position = len(out) + 1
        # Substituted here, before prompts.fill() runs: {x} means the reference
        # number in this one string, but the segment count everywhere else.
        for token in ("{x}", "{n}", "{number}", "{index}", "{reference}",
                      "{X}", "{N}", "{INDEX}"):
            template = template.replace(token, str(position))
        out.append(template.strip()
                   or f"the last frame of the previous clip (reference {position})")
    return out


def build_request(pid: int, stage: str, index: int = 0) -> dict:
    """The full outgoing request for a stage: model, system prompt, messages."""
    cfg, project = config.load(), db.get_project(pid)
    if not project:
        raise RuntimeError("Project not found.")
    s = project["settings"]
    duration, count = s["duration"], s["segment_count"]

    chapters = int(s.get("chapter_count", 4))

    if stage == "outline":
        system = cfg["outline_system"]
        user = prompts.outline_user(project["idea"], chapters, duration, count,
                                    cfg["outline_user"])
        model = cfg["outline_model"] or cfg["script_model"]
        temp = float(cfg.get("outline_temperature", 0.6))

    elif stage == "chapter":
        previous = None
        if index > 0:
            previous = (db.get_chapter(pid, index - 1) or {}).get("text") or None
        brief = (db.get_chapter(pid, index) or {}).get("brief") or None
        system = cfg["chapter_system"]
        user = prompts.chapter_user(project["outline"], index, chapters, brief,
                                    previous, duration, count, cfg["chapter_user"])
        model = cfg["chapter_model"] or cfg["script_model"]
        temp = float(cfg.get("chapter_temperature", 0.9))

    elif stage == "script":
        system = cfg["script_system"]
        user = prompts.script_user(project["idea"], count, duration, cfg["script_user"])
        model, temp = cfg["script_model"], float(cfg["script_temperature"])

    elif stage == "beats":
        system = cfg["beat_system"]
        user = prompts.plan_user(project["script"], count, duration, cfg["beat_user"])
        model = cfg["beat_model"] or cfg["segment_model"] or cfg["script_model"]
        temp = float(cfg.get("beat_temperature", 0.4))

    elif stage == "segment":
        # Without a brief the [[segment]] block drops out and the model gets only
        # the full script, so it divides the story itself and every clip drifts.
        # Fail loudly rather than let that reach the sampler.
        if s.get("plan_beats") and not ((db.get_segment(pid, index) or {}).get("beat") or "").strip():
            raise RuntimeError(
                f"Segment {index + 1} has no script segment attached, so only the full "
                f"script would be sent and the model would choose its own share of it. "
                f"Run 'Split script' first, or paste a segment into that card — or turn "
                f"off 'Split the script into segments first' if that is what you want."
            )
        previous = None
        if index > 0:
            previous = (db.get_segment(pid, index - 1) or {}).get("prompt") or None
        beat = (db.get_segment(pid, index) or {}).get("beat") or None
        system = cfg["segment_system"]
        script_context = project["script"] if s.get("send_full_script", True) else ""
        user = prompts.segment_user(script_context, index, count, duration,
                                    previous, beat, reference_descriptions(pid, index),
                                    cfg["segment_user"])
        model, temp = cfg["segment_model"], float(cfg["segment_temperature"])

    else:
        raise RuntimeError(f"Unknown stage: {stage}")

    system = prompts.fill(system, duration, count, chapters)
    if not user.strip():
        raise RuntimeError(
            f"The stage {stage!r} user message came out empty — the model would get "
            f"nothing to work with. Check the user message box in Settings; clearing it "
            f"is fine, but a template that renders to nothing is not."
        )
    messages = ([{"role": "system", "content": system}] if system.strip() else []) + [
        {"role": "user", "content": user}
    ]
    return {
        "stage": stage,
        "index": index,
        "model": model,
        "temperature": temp,
        "timeout": float(cfg.get("llm_timeout", 300)),
        "messages": messages,
        "url": cfg["openwebui_url"],
    }


async def _send(pid: int, stage: str, index: int = 0) -> str:
    cfg = config.load()
    req = build_request(pid, stage, index)
    return await openwebui.chat(
        cfg["openwebui_url"], cfg["openwebui_key"], req["model"],
        req["messages"], temperature=req["temperature"], timeout=req["timeout"],
    )


# --------------------------------------------------------------------------
# stage 1 — idea to script
# --------------------------------------------------------------------------

async def generate_script(pid: int) -> str:
    bus.emit({"type": "stage", "stage": "script", "project_id": pid, "state": "running"})
    text = await _send(pid, "script")
    db.update_project(pid, script=text)
    bus.emit({"type": "script", "project_id": pid, "text": text})
    bus.emit({"type": "stage", "stage": "script", "project_id": pid, "state": "done"})
    return text


# --------------------------------------------------------------------------
# stage 1, story mode — plan an outline, then write it a chapter at a time
#
# One long "write the whole story" call degrades badly. Planning first and
# writing chapter by chapter keeps every call short and gives each one an
# explicit brief plus the previous chapter to continue from.
# --------------------------------------------------------------------------

async def plan_outline(pid: int) -> list[str]:
    project = db.get_project(pid)
    if not (project["idea"] or "").strip():
        raise RuntimeError("No idea yet. Write one first.")
    count = int(project["settings"].get("chapter_count", 4))

    bus.emit({"type": "stage", "stage": "outline", "project_id": pid, "state": "running"})
    reply = await _send(pid, "outline")
    briefs = _parse_beats(reply, count)

    # Same reasoning as the segmenter: a plan that came out 5 chapters instead of
    # 4 is a fine plan, and forcing it into 4 would drop a chapter's worth of story.
    if len(briefs) != count:
        bus.emit({"type": "warning", "project_id": pid,
                  "message": f"The outline came back with {len(briefs)} chapters, not "
                             f"{count}. Using {len(briefs)}."})
        count = len(briefs)
        db.update_project(pid, settings={**project["settings"], "chapter_count": count})

    db.ensure_chapters(pid, count)
    db.update_project(pid, outline=reply.strip())
    for i, brief in enumerate(briefs):
        db.update_chapter(pid, i, brief=brief, status="planned" if brief else "empty")
    bus.emit({"type": "outline", "project_id": pid, "chapters": len(briefs)})
    bus.emit({"type": "stage", "stage": "outline", "project_id": pid, "state": "done"})
    return briefs


async def write_chapters(pid: int, only: int | None = None) -> None:
    project = db.get_project(pid)
    if not (project["outline"] or "").strip():
        raise RuntimeError("No story plan yet. Plan the outline first.")
    count = int(project["settings"].get("chapter_count", 4))
    db.ensure_chapters(pid, count)

    for i in ([only] if only is not None else list(range(count))):
        runner.check()
        db.update_chapter(pid, i, status="writing", error="")
        bus.emit({"type": "chapter", "project_id": pid, "index": i, "status": "writing"})
        try:
            text = prompts.clean_prompt(await _send(pid, "chapter", i))
        except Exception as exc:
            db.update_chapter(pid, i, status="error", error=str(exc))
            bus.emit({"type": "chapter", "project_id": pid, "index": i,
                      "status": "error", "error": str(exc)})
            raise
        db.update_chapter(pid, i, text=text, status="done")
        bus.emit({"type": "chapter", "project_id": pid, "index": i,
                  "status": "done", "text": text})

    assemble_story(pid)


def assemble_story(pid: int) -> str:
    """Join the written chapters into the script the later stages work from."""
    project = db.get_project(pid)
    parts = [(c["text"] or "").strip() for c in project["chapters"]]
    written = [t for t in parts if t]
    if not written:
        raise RuntimeError("No chapters have been written yet.")
    story = "\n\n".join(written)
    db.update_project(pid, script=story)
    bus.emit({"type": "script", "project_id": pid, "text": story})
    bus.emit({"type": "assembled", "project_id": pid, "chapters": len(written),
              "characters": len(story)})
    return story


# --------------------------------------------------------------------------
# stage 2a — split the script into beats
# --------------------------------------------------------------------------

def _beats_by_delimiter(text: str) -> list[str]:
    """`=== BEAT 1 ===` blocks — the documented format."""
    pattern = re.compile(prompts.BEAT_DELIMITER, re.I | re.M)
    marks = list(pattern.finditer(text))
    if not marks:
        return []
    beats = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[mark.end():end].strip()
        if body:
            beats.append(body)
    return beats


def _beats_by_json(text: str) -> list[str]:
    """A JSON array of strings, with or without fences and preamble."""
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(b).strip() for b in parsed if str(b).strip()]


def _beats_by_numbering(text: str) -> list[str]:
    """`1. text` or `Beat 1: text` on their own lines."""
    numbered = re.compile(
        r"^\s*(?:beat|segment|clip|shot|chapter|part)?\s*#?(\d+)\s*[.):\-]\s+(.+)$", re.I)
    beats, current = [], None
    for line in text.splitlines():
        m = numbered.match(line)
        if m:
            if current:
                beats.append(current.strip())
            current = m.group(2)
        elif current is not None and line.strip():
            current += " " + line.strip()
    if current:
        beats.append(current.strip())
    return [b for b in beats if len(b) > 15]


def _parse_beats(text: str, count: int) -> list[str]:
    """Read the planning reply. Delimiters first, then JSON, then numbering.

    Returns whatever it found, even if that is not `count` items — the caller
    decides what to do about a mismatch. Only raises when nothing is readable.
    """
    best: list[str] = []
    for reader in (_beats_by_delimiter, _beats_by_json, _beats_by_numbering):
        beats = reader(text)
        if len(beats) == count:
            return beats
        if len(beats) > len(best):
            best = beats
    if best:
        return best
    raise RuntimeError(
        "Couldn't read any segments out of the split. Expected blocks like:\n"
        "  === SEGMENT 1 ===\n  what happens\n\n"
        f"The model said:\n{text[:600]}"
    )


async def _split_with_retry(pid: int, count: int) -> tuple[list[str], str]:
    """Ask for the split; if the count is wrong, show the model its own answer.

    A single corrective turn fixes this most of the time — far better than
    failing the run and making the person rerun it by hand.
    """
    cfg = config.load()
    req = build_request(pid, "beats")
    reply = await openwebui.chat(
        cfg["openwebui_url"], cfg["openwebui_key"], req["model"],
        req["messages"], temperature=req["temperature"], timeout=req["timeout"])

    try:
        beats = _parse_beats(reply, count)
        if len(beats) == count:
            return beats, reply
        got = len(beats)
    except RuntimeError:
        beats, got = [], 0

    bus.emit({"type": "warning", "project_id": pid,
              "message": f"The segmenter returned {got or 'no'} segments, not {count}. "
                         f"Asking it again."})

    correction = (
        f"That gave {got} segments, but exactly {count} are needed. Redo the split into "
        f"exactly {count} segments covering the same material in the same order — "
        f"divide the longer ones rather than adding new events. Output only the "
        f"=== SEGMENT 1 === through === SEGMENT {count} === blocks."
    )
    retry = await openwebui.chat(
        cfg["openwebui_url"], cfg["openwebui_key"], req["model"],
        req["messages"] + [{"role": "assistant", "content": reply},
                           {"role": "user", "content": correction}],
        temperature=req["temperature"], timeout=req["timeout"])

    try:
        second = _parse_beats(retry, count)
    except RuntimeError:
        if beats:
            return beats, reply          # the first answer was at least parseable
        raise
    return second, retry


async def plan_beats(pid: int) -> list[str]:
    cfg, project = config.load(), db.get_project(pid)
    if not (project["script"] or "").strip():
        raise RuntimeError("No script yet. Write or generate one first.")
    s = project["settings"]
    count = int(s["segment_count"])

    bus.emit({"type": "stage", "stage": "beats", "project_id": pid, "state": "running"})
    beats, reply = await _split_with_retry(pid, count)

    # Adopting the model's count is the friendlier default when it is close: the
    # story usually divides more naturally its way than into a number we imposed.
    if len(beats) != count:
        if s.get("adopt_segment_count", True):
            bus.emit({"type": "warning", "project_id": pid,
                      "message": f"The segmenter returned {len(beats)} segments, not "
                                 f"{count}. Using {len(beats)}."})
            count = len(beats)
            db.update_project(pid, settings={**s, "segment_count": count})
        elif len(beats) > count:
            beats = beats[:count]
        else:
            while len(beats) < count:
                beats.append("")

    db.ensure_segments(pid, count)
    db.update_project(pid, beats=beats)
    for i, beat in enumerate(beats):
        db.update_segment(pid, i, beat=beat)
    bus.emit({"type": "beats", "project_id": pid, "beats": beats})
    bus.emit({"type": "stage", "stage": "beats", "project_id": pid, "state": "done"})
    return beats


# --------------------------------------------------------------------------
# stage 2b — script to segment prompts
# --------------------------------------------------------------------------

async def _one_prompt(pid: int, index: int) -> str:
    """Previous prompt and beat are read inside build_request, from the database."""
    return prompts.clean_prompt(await _send(pid, "segment", index))


async def generate_prompts(pid: int, only: int | None = None) -> None:
    cfg, project = config.load(), db.get_project(pid)
    if not (project["script"] or "").strip():
        raise RuntimeError("No script yet. Write or generate one first.")
    count = project["settings"]["segment_count"]
    db.ensure_segments(pid, count)

    indices = [only] if only is not None else list(range(count))
    for i in indices:
        runner.check()
        db.update_segment(pid, i, status="prompting", error="")
        bus.emit({"type": "segment", "project_id": pid, "index": i, "status": "prompting"})
        try:
            text = await _one_prompt(pid, i)
        except Exception as exc:
            db.update_segment(pid, i, status="error", error=str(exc))
            bus.emit({"type": "segment", "project_id": pid, "index": i,
                      "status": "error", "error": str(exc)})
            raise
        db.update_segment(pid, i, prompt=text, status="ready")
        bus.emit({"type": "segment", "project_id": pid, "index": i,
                  "status": "ready", "prompt": text})


# --------------------------------------------------------------------------
# stage 3 — render
# --------------------------------------------------------------------------

def _slot_values(settings: dict, slot_count: int,
                 continuation_name: str | None) -> tuple[list[str | None], str | None]:
    """Static references in order, then the previous clip's last frame after them.

    Three references plus a carried frame means the frame is reference four. On the
    first clip there is nothing to carry, so it is just the three.
    """
    wanted = settings.get("ref_image_count")
    ids = list(settings.get("ref_images") or [])
    if isinstance(wanted, int):
        ids = ids[:max(0, wanted)]
    assets = [db.get_asset(a) for a in ids]
    names = [a["comfy_name"] for a in assets if a and a["comfy_name"]]
    if continuation_name:
        names.append(continuation_name)

    warning = None
    if len(names) > slot_count:
        dropped = len(names) - slot_count
        warning = (f"The workflow has {slot_count} reference image slots but this run needs "
                   f"{len(names)}. Dropped {dropped} static reference(s) to keep the "
                   f"carried frame.")
        names = names[:slot_count - 1] + [names[-1]]
    return [names[i] if i < len(names) else None for i in range(slot_count)], warning


def _audio_values(settings: dict, slot_count: int) -> list[str | None]:
    wanted = settings.get("ref_audio_count")
    ids = list(settings.get("ref_audios") or [])
    if isinstance(wanted, int):
        ids = ids[:max(0, wanted)]
    assets = [db.get_asset(a) for a in ids]
    names = [a["comfy_name"] for a in assets if a and a["comfy_name"]]
    return [names[i] if i < len(names) else None for i in range(slot_count)]


async def render(pid: int, only: list[int] | None = None) -> None:
    cfg = config.load()

    # Hand the card back before ComfyUI loads its weights.
    note = await swap.unload_quietly(cfg.get("llm_unload_url", ""),
                                     cfg.get("llm_unload_path", "/api/models/unload"),
                                     cfg.get("llm_running_path", "/running"))
    if note:
        bus.emit({"type": "info", "project_id": pid, "message": note})

    graph, roles = load_workflow()
    patcher = workflow.Patcher(graph, roles)
    client = comfy.ComfyClient(cfg["comfy_url"])

    project = db.get_project(pid)
    s = project["settings"]
    img_slots = len(roles.get("ref_images") or [])
    aud_slots = len(roles.get("ref_audios") or [])

    targets = only if only is not None else [
        seg["idx"] for seg in project["segments"] if (seg["prompt"] or "").strip()
    ]
    if not targets:
        raise RuntimeError("No segments have a prompt to render.")

    # If we're starting partway in, pick up the frame the previous segment left.
    carry: str | None = None
    if s.get("continuation"):
        first = min(targets)
        if first > 0:
            prev = db.get_segment(pid, first - 1)
            carry = (prev or {}).get("last_frame") or None

    for idx in sorted(targets):
        runner.check()
        segment = db.get_segment(pid, idx)
        prompt_text = (segment or {}).get("prompt", "").strip()
        if not prompt_text:
            continue

        seed = random.randint(0, 2**53 - 1) if int(s.get("seed", -1)) < 0 else int(s["seed"])
        images, slot_warning = _slot_values(
            s, img_slots, carry if s.get("continuation") else None)
        if slot_warning:
            bus.emit({"type": "warning", "project_id": pid, "index": idx,
                      "message": slot_warning})
        graph_for_segment = patcher.build(
            prompt_text,
            images=images,
            audios=_audio_values(s, aud_slots),
            duration=float(s["duration"]),
            seed=seed,
            steps=int(s["steps"]),
            aspect_ratio=s.get("aspect_ratio"),
            megapixels=float(s.get("megapixels", 0.8)),
            filename_prefix=f"{s.get('filename_prefix','video/vidpipe')}_p{pid}_s{idx:02d}",
            loras=s.get("loras") or None,
            output_fps=float(s.get("output_fps") or 0) or None,
        )

        db.update_segment(pid, idx, status="rendering", error="", video="")
        bus.emit({"type": "segment", "project_id": pid, "index": idx,
                  "status": "rendering", "seed": seed})

        def on_event(ev, _idx=idx):
            if ev.get("type") == "progress":
                bus.emit({"type": "progress", "project_id": pid, "index": _idx,
                          "value": ev["value"], "max": ev["max"]})
            elif ev.get("type") == "queued":
                db.update_segment(pid, _idx, comfy_prompt_id=ev["prompt_id"])

        try:
            result = await client.run(graph_for_segment, on_event=on_event)
        except Exception as exc:
            db.update_segment(pid, idx, status="error", error=str(exc))
            bus.emit({"type": "segment", "project_id": pid, "index": idx,
                      "status": "error", "error": str(exc)})
            raise

        media = comfy.collect_media(result["outputs"])
        if not media:
            msg = "ComfyUI finished but returned no file. Check the SaveVideo node."
            db.update_segment(pid, idx, status="error", error=msg)
            bus.emit({"type": "segment", "project_id": pid, "index": idx,
                      "status": "error", "error": msg})
            raise RuntimeError(msg)

        ref = media[0]
        local = config.DATA / "renders" / f"p{pid}_s{idx:02d}_{ref['filename']}"
        await client.download(ref, local)
        db.update_segment(pid, idx, status="done", video=str(local))
        bus.emit({"type": "segment", "project_id": pid, "index": idx,
                  "status": "done", "video": local.name})

        if s.get("continuation"):
            try:
                frame_path = config.DATA / "frames" / f"p{pid}_s{idx:02d}_last.png"
                await video.extract_last_frame(local, frame_path, cfg.get("ffmpeg", "ffmpeg"))
                carry = await client.upload(frame_path)
                db.update_segment(pid, idx, last_frame=carry)
                bus.emit({"type": "frame", "project_id": pid, "index": idx, "name": carry})
            except Exception as exc:
                bus.emit({"type": "warning", "project_id": pid, "index": idx,
                          "message": f"Last frame not captured: {exc}"})
                carry = None

    if s.get("concat"):
        await join_clips(pid)

    bus.emit({"type": "render_complete", "project_id": pid})


async def join_clips(pid: int) -> str | None:
    """Stitch every rendered segment, in order, into one file."""
    project = db.get_project(pid)
    clips, gaps = [], []
    for seg in project["segments"]:
        if seg["video"] and Path(seg["video"]).exists():
            clips.append(Path(seg["video"]))
        elif (seg["prompt"] or "").strip():
            gaps.append(seg["idx"] + 1)

    if len(clips) < 2:
        bus.emit({"type": "info", "project_id": pid,
                  "message": "Nothing to join yet — needs at least two rendered clips."})
        return None
    if gaps:
        bus.emit({"type": "warning", "project_id": pid,
                  "message": f"Joining without segment(s) {gaps} — not rendered yet."})

    cfg = config.load()
    settings = project["settings"]
    fade = float(settings.get("crossfade") or 0) if settings.get("crossfade_on") else 0.0
    dest = config.DATA / "renders" / f"p{pid}_full.mp4"
    try:
        await video.concat(clips, dest, cfg.get("ffmpeg", "ffmpeg"),
                           crossfade_seconds=fade, ffprobe=cfg.get("ffprobe", "ffprobe"))
    except Exception as exc:
        bus.emit({"type": "warning", "project_id": pid, "message": f"Join failed: {exc}"})
        return None
    db.update_project(pid, final_video=str(dest))
    bus.emit({"type": "joined", "project_id": pid, "clips": len(clips), "crossfade": fade})
    return str(dest)


# --------------------------------------------------------------------------
# whole run
# --------------------------------------------------------------------------

async def run_all(pid: int) -> None:
    project = db.get_project(pid)
    if not (project["script"] or "").strip():
        if project["settings"].get("story_mode"):
            await plan_outline(pid)
            runner.check()
            await write_chapters(pid)
        else:
            await generate_script(pid)
    runner.check()
    if project["settings"].get("plan_beats"):
        await plan_beats(pid)
        runner.check()
    await generate_prompts(pid)
    runner.check()
    await render(pid)
