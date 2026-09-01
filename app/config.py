"""Settings storage. One JSON file, loaded on demand, written atomically."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from . import prompts

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("VIDPIPE_DATA", ROOT / "data"))
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "assets").mkdir(exist_ok=True)
(DATA / "renders").mkdir(exist_ok=True)
(DATA / "frames").mkdir(exist_ok=True)

CONFIG_PATH = DATA / "config.json"
WORKFLOW_PATH = DATA / "workflow.api.json"

_lock = threading.Lock()

DEFAULTS = {
    "openwebui_url": "http://localhost:3000",
    "openwebui_key": "",
    "comfy_url": "http://127.0.0.1:8188",
    "script_model": "",
    "segment_model": "",
    "outline_model": "",             # empty falls back to the script model
    "chapter_model": "",
    "beat_model": "",                # empty falls back to the segment model
    # Prefilled with the built-ins, so the Settings boxes show exactly what is
    # sent. Clearing a box sends nothing for it — no hidden fallback.
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
    "beat_temperature": 0.4,
    "outline_temperature": 0.6,
    "chapter_temperature": 0.9,
    "script_temperature": 0.9,
    "segment_temperature": 0.7,
    "ffmpeg": "ffmpeg",
    "ffprobe": "ffprobe",
    # {x} here is the reference number this frame lands on, not the segment
    # count — it is substituted before the prompt placeholders run.
    "carried_frame_description": (
        "This is the last frame from the previous clip and it has been included as "
        "reference image number {x}."),
    "llm_timeout": 300,              # seconds to wait on one model reply
    "prompts_initialized": False,    # set once, after the prompt boxes are seeded
    "llm_unload_url": "",            # e.g. http://localhost:8080 — empty disables
    "llm_unload_path": "/api/models/unload",
    "llm_running_path": "/running",
}


#: Values written by older builds that should be replaced by today's default
#: rather than kept. A saved setting normally wins over the default — these are
#: the exceptions, where the old value was simply wrong.
STALE = {
    "llm_unload_path": {"/unload", "/api/unload", ""},
    "llm_running_path": {""},
}

#: Prompt boxes. Empty used to mean "fall back to the built-in"; now the box is
#: the truth, so an existing config's empties are filled in ONCE. After that an
#: empty box stays empty — clearing one is a deliberate choice and must stick.
#: Verbatim earlier defaults. A box still holding one of these was never edited,
#: so it is safe to move it to the current default — that is how a fix to a
#: built-in prompt reaches people who already have a config file. Anything the
#: person actually changed is left exactly as they wrote it.
SUPERSEDED = {
    "segment_user": [
        """FULL SCRIPT (for context — do not cover all of it):
{script}

Write segment {index} of {segment_count}: {start} to {end} of the finished video.
[[segment]]
THIS SEGMENT COVERS EXACTLY THIS — everything in it, nothing beyond it:
{segment}
[[/segment]]
[[references]]
REFERENCE IMAGES SENT WITH THIS SEGMENT, in the order they are attached. Reference 1 is \
the first picture reference, Reference 2 the second, and so on:
{references}
[[/references]]
[[previous]]
PREVIOUS SEGMENT'S PROMPT (the frame you are continuing from):
{previous}

Continue directly from where that segment ends. Repeat the subject, wardrobe, setting and \
lighting descriptions so this segment stands alone.
[[/previous]]
[[first]]
This is the opening segment. Establish the subject and setting.
[[/first]]""",
    ],
}

PROMPT_KEYS = ("script_system", "segment_system", "beat_system",
               "script_user", "beat_user", "segment_user",
               "outline_system", "outline_user", "chapter_system", "chapter_user")


def _upgrade(cfg: dict) -> tuple[dict, bool]:
    changed = False
    for key, bad in STALE.items():
        if cfg.get(key) in bad:
            cfg[key] = DEFAULTS[key]
            changed = True

    for key, old_versions in SUPERSEDED.items():
        current = cfg.get(key)
        if current and any(current.strip() == old.strip() for old in old_versions):
            cfg[key] = DEFAULTS[key]
            changed = True

    if not cfg.get("prompts_initialized"):
        for key in PROMPT_KEYS:
            if not (cfg.get(key) or "").strip():
                cfg[key] = DEFAULTS[key]
        cfg["prompts_initialized"] = True
        changed = True
    return cfg, changed


def load() -> dict:
    with _lock:
        cfg = dict(DEFAULTS)
        if CONFIG_PATH.exists():
            try:
                cfg.update(json.loads(CONFIG_PATH.read_text()))
            except json.JSONDecodeError:
                pass
        cfg, changed = _upgrade(cfg)
        if changed:
            tmp = CONFIG_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(cfg, indent=2))
            tmp.replace(CONFIG_PATH)
        return cfg


def save(patch: dict) -> dict:
    with _lock:
        cfg = dict(DEFAULTS)
        if CONFIG_PATH.exists():
            try:
                cfg.update(json.loads(CONFIG_PATH.read_text()))
            except json.JSONDecodeError:
                pass
        cfg.update({k: v for k, v in patch.items() if k in DEFAULTS})
        cfg, _ = _upgrade(cfg)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2))
        tmp.replace(CONFIG_PATH)
        return cfg
