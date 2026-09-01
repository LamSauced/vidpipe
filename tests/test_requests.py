"""What each stage sends: model, system prompt, and message contents."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["VIDPIPE_DATA"] = tempfile.mkdtemp(prefix="vidpipe-req-")

from app import config, db, pipeline, prompts  # noqa: E402

db.init()
p = db.create_project("req", "a woman opens the shutters")
pid = p["id"]
db.update_project(pid, script="THE SCRIPT BODY",
                  settings={**p["settings"], "segment_count": 3, "duration": 15.0})
db.ensure_segments(pid, 3)
db.update_segment(pid, 0, beat="BEAT ONE", prompt="PROMPT ONE", status="ready")
db.update_segment(pid, 1, beat="BEAT TWO")

config.save({"script_model": "writer", "segment_model": "segmenter",
             "script_temperature": 0.9, "segment_temperature": 0.7})


def check(label, cond, detail=""):
    assert cond, f"{label} failed {detail}"
    print(f"  ok  {label}")


# --- stage 1 --------------------------------------------------------------
r = pipeline.build_request(pid, "script")
check("script uses the script model", r["model"] == "writer", r["model"])
check("script temperature applied", r["temperature"] == 0.9)
check("script sends the idea", "opens the shutters" in r["messages"][-1]["content"])
check("placeholders substituted in the system prompt",
      "{segment_count}" not in r["messages"][0]["content"]
      and "{total_seconds}" not in r["messages"][0]["content"])
check("stage 1 is told the total length, not a beat count",
      "45 seconds" in r["messages"][-1]["content"], r["messages"][-1]["content"])

# --- stage 2 --------------------------------------------------------------
r = pipeline.build_request(pid, "beats")
check("beats fall back to the segment model", r["model"] == "segmenter", r["model"])
check("beats send the script", "THE SCRIPT BODY" in r["messages"][-1]["content"])
check("segmenter asks for the documented format",
      "=== SEGMENT 1 ===" in r["messages"][0]["content"])

config.save({"beat_model": "planner", "beat_temperature": 0.2,
             "beat_system": "MY OWN BEAT RULES for {segment_count} clips"})
r = pipeline.build_request(pid, "beats")
check("beat model overrides the fallback", r["model"] == "planner")
check("beat temperature honoured", r["temperature"] == 0.2)
check("custom beat system used, with placeholders filled",
      r["messages"][0]["content"] == "MY OWN BEAT RULES for 3 clips",
      r["messages"][0]["content"])

# --- stage 3 --------------------------------------------------------------
r0 = pipeline.build_request(pid, "segment", 0)
body0 = r0["messages"][-1]["content"]
check("segment uses the segment model", r0["model"] == "segmenter")
check("segment 1 gets the full script", "THE SCRIPT BODY" in body0)
check("segment 1 gets its slice of script", "BEAT ONE" in body0)
check("segment 1 has no previous prompt", "PREVIOUS SEGMENT" not in body0)
check("segment 1 timecodes", "0s to 15s" in body0, body0)

r1 = pipeline.build_request(pid, "segment", 1)
body1 = r1["messages"][-1]["content"]
check("segment 2 carries the previous prompt", "PROMPT ONE" in body1)
check("segment 2 gets its own beat, not the previous one",
      "BEAT TWO" in body1 and "BEAT ONE" not in body1)
check("segment 2 timecodes", "15s to 30s" in body1, body1)

# an empty system prompt means none is sent at all, so the model's own skill rules
config.save({"segment_system": " "})
r = pipeline.build_request(pid, "segment", 0)
check("a cleared system box sends no system message at all",
      [m["role"] for m in r["messages"]] == ["user"], r["messages"])

config.save({"segment_system": "CUSTOM {segment_seconds}s RULES"})
r = pipeline.build_request(pid, "segment", 0)
check("custom segment system used",
      r["messages"][0]["content"] == "CUSTOM 15s RULES", r["messages"][0]["content"])

# --- preview and execution must agree ------------------------------------
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

c = TestClient(app)
for stage, index in (("script", 0), ("beats", 0), ("segment", 1)):
    api_req = c.get(f"/api/projects/{pid}/preview?stage={stage}&index={index}").json()
    direct = pipeline.build_request(pid, stage, index)
    assert api_req["messages"] == direct["messages"], stage
    assert api_req["model"] == direct["model"], stage
print("  ok  the preview endpoint returns what the run would send")

check("unknown stage rejected",
      c.get(f"/api/projects/{pid}/preview?stage=nope").status_code == 400)

# --- placeholders ---------------------------------------------------------
from app import prompts as _p  # noqa: E402

subs = [
    ("Split a story into {x} segments.", "Split a story into 3 segments."),
    ("Split into SEGMENT_AMOUNT parts.", "Split into 3 parts."),
    ("{segment_count} clips of {segment_seconds}s", "3 clips of 15s"),
    ("{{segments}} / ${total_seconds} / %duration%", "3 / 45 / 15"),
    ("{ SEGMENT_COUNT }", "3"),
]
for raw, want in subs:
    got = _p.fill(raw, 15.0, 3)
    assert got == want, f"{raw!r} -> {got!r}, wanted {want!r}"
print("  ok  placeholders substituted in every accepted spelling")

# literal braces must survive — a JSON example or H3 markup would otherwise
# raise KeyError and take down the run
keep = [
    'Return {"key": 1} exactly.',
    "Emit <d>[English] a line</d>.",
    "Leave {unknown_token} alone.",
    "The x factor, and segments generally, are untouched.",
]
for raw in keep:
    assert _p.fill(raw, 15.0, 3) == raw, raw
print("  ok  literal braces and ordinary prose left alone")

# --- reference descriptions ----------------------------------------------
a1 = db.add_asset("image", "her.png", "/tmp/a.png", "vidpipe/her.png")
a2 = db.add_asset("image", "room.png", "/tmp/b.png", "vidpipe/room.png")
db.update_asset(a1["id"], description="the woman, dark braids, grey wool coat")
db.update_asset(a2["id"], description="the room: lattice window, wooden floor")
db.update_project(pid, settings={**db.get_project(pid)["settings"],
                                 "ref_images": [a1["id"], a2["id"]],
                                 "ref_image_count": 2, "continuation": True})

body = pipeline.build_request(pid, "segment", 0)["messages"][-1]["content"]
check("segment 1 lists both descriptions in order",
      body.index("Reference 1's description: the woman") <
      body.index("Reference 2's description: the room"), body[-500:])
check("segment 1 has no carried frame reference",
      "Reference 3's description" not in body)

body1 = pipeline.build_request(pid, "segment", 1)["messages"][-1]["content"]
check("segment 2 adds the carried frame as the next reference",
      "Reference 3's description: This is the last frame" in body1, body1[-400:])

# the numbering must match the slots the render actually fills
slots, _ = pipeline._slot_values(db.get_project(pid)["settings"], 6, "vidpipe/frame.png")
descriptions = pipeline.reference_descriptions(pid, 1)
check("description order matches slot order",
      len(descriptions) == len([x for x in slots if x]) == 3, (slots, descriptions))
check("the carried frame is last in both",
      slots[2] == "vidpipe/frame.png" and "last frame" in descriptions[2].lower(),
      descriptions)

# an undescribed reference still occupies its number, so nothing shifts
a3 = db.add_asset("image", "prop.png", "/tmp/c.png", "vidpipe/prop.png")
db.update_project(pid, settings={**db.get_project(pid)["settings"],
                                 "ref_images": [a1["id"], a3["id"], a2["id"]],
                                 "ref_image_count": 3})
d = pipeline.reference_descriptions(pid, 0)
check("undescribed reference keeps its slot", len(d) == 3 and "prop.png" in d[1], d)

# lowering the count drops the trailing references from the description list too
db.update_project(pid, settings={**db.get_project(pid)["settings"], "ref_image_count": 1})
check("description list honours the slot count",
      len(pipeline.reference_descriptions(pid, 0)) == 1)

# --- numbering is consistent end to end ----------------------------------
db.update_project(pid, settings={**db.get_project(pid)["settings"],
                                 "ref_images": [a1["id"], a2["id"]],
                                 "ref_image_count": 2, "continuation": True})
slots, _ = pipeline._slot_values(db.get_project(pid)["settings"], 6, "vidpipe/frame.png")
descs = pipeline.reference_descriptions(pid, 1)
filled = [v for v in slots if v]
check("one description per filled slot, same order",
      len(filled) == len(descs) == 3, (filled, descs))
check("slot 0 (ref_image_0) is described as Reference 1",
      filled[0] == "vidpipe/her.png" and "the woman" in descs[0], (filled, descs))
check("the carried frame is the last filled slot and the last description",
      filled[-1] == "vidpipe/frame.png" and "last frame" in descs[-1].lower(), descs)

body = pipeline.build_request(pid, "segment", 1)["messages"][-1]["content"]
for i, d in enumerate(descs, start=1):
    assert f"Reference {i}'s description: {d}" in body, (i, d)
check("the prompt numbers references from 1 upward, matching slot order", True)

# --- nothing is injected behind a custom template -------------------------
config.save({"script_system": " ", "script_user": "Story idea: {idea}. Go."})
r = pipeline.build_request(pid, "script")
check("custom stage 1 user message is sent verbatim",
      r["messages"] == [{"role": "user",
                         "content": "Story idea: a woman opens the shutters. Go."}],
      r["messages"])
check("no beat language leaks into stage 1",
      "beat" not in r["messages"][-1]["content"].lower())

# the built-in stage 1 template must not presuppose beats either
config.save({"script_user": prompts.SCRIPT_USER})
r = pipeline.build_request(pid, "script")
built_in = r["messages"][-1]["content"]
check("built-in stage 1 template mentions no beats or segments",
      "beat" not in built_in.lower() and "segment" not in built_in.lower(), built_in)

# --- conditional blocks ---------------------------------------------------
config.save({"segment_user":
             "S:{script}[[previous]]\nPREV:{previous}[[/previous]]"
             "[[first]]\nOPENING[[/first]]"})
first = pipeline.build_request(pid, "segment", 0)["messages"][-1]["content"]
later = pipeline.build_request(pid, "segment", 1)["messages"][-1]["content"]
check("the previous block is dropped on segment 1", "PREV:" not in first, first)
check("the opening block appears only on segment 1",
      "OPENING" in first and "OPENING" not in later)
check("the previous block appears from segment 2", "PREV:" in later, later)

# a template that uses none of the optional blocks still works
db.update_segment(pid, 2, beat="BEAT THREE")   # the guard needs every segment to have one
config.save({"segment_user": "Just write segment {index} of {segment_count}."})
r = pipeline.build_request(pid, "segment", 2)
check("minimal template renders",
      r["messages"][-1]["content"] == "Just write segment 3 of 3.",
      r["messages"][-1]["content"])

# unknown tokens and literal braces survive a user template too
config.save({"segment_user": 'Keep {unknown} and {"json": 1} intact.'})
check("user templates tolerate literal braces",
      pipeline.build_request(pid, "segment", 0)["messages"][-1]["content"]
      == 'Keep {unknown} and {"json": 1} intact.')

config.save({"script_user": prompts.SCRIPT_USER, "beat_user": prompts.PLAN_USER,
             "segment_user": prompts.SEGMENT_USER})
# --- a cleared prompt box stays cleared -----------------------------------
for key in config.PROMPT_KEYS:
    config.save({key: ""})
    check(f"clearing {key} sticks", config.load()[key] == "")
    config.save({key: "SOMETHING {x}"})
    check(f"a custom {key} is stored verbatim", config.load()[key] == "SOMETHING {x}")

# a run with every box cleared sends only the user message vidpipe must build
for key in config.PROMPT_KEYS:
    config.save({key: ""})
# Clearing a user box means "no wrapper text of mine" — never "drop the data".
r = pipeline.build_request(pid, "script")
check("cleared stage 1 box still sends the idea",
      [m["role"] for m in r["messages"]] == ["user"]
      and r["messages"][0]["content"] == "a woman opens the shutters",
      r["messages"])

r = pipeline.build_request(pid, "beats")
check("cleared stage 2 box still sends the script",
      r["messages"][-1]["content"] == "THE SCRIPT BODY", r["messages"])

r = pipeline.build_request(pid, "segment", 1)
body = r["messages"][-1]["content"]
check("cleared stage 3 box still sends script, segment, references and previous",
      "THE SCRIPT BODY" in body and "BEAT TWO" in body
      and "PROMPT ONE" in body and "Reference 1" in body, body)
check("no user message is ever empty", body.strip() != "")

# --- placeholders work in BOTH the system prompt and the user message -----
db.update_project(pid, settings={**db.get_project(pid)["settings"],
                                 "segment_count": 6, "duration": 15.0})
config.save({"beat_system": "Split a story into {x} segments, SEGMENT_SECONDS each.",
             "beat_user": "SCRIPT:\n{script}\n\nGive me {x} segments."})
r = pipeline.build_request(pid, "beats")
check("{x} substituted in the segmenter SYSTEM prompt",
      r["messages"][0]["content"] == "Split a story into 6 segments, 15 each.",
      r["messages"][0]["content"])
check("{x} substituted in the segmenter USER message",
      "Give me 6 segments." in r["messages"][-1]["content"], r["messages"][-1]["content"])

# and in every other stage's boxes too
config.save({"script_system": "{x} clips", "script_user": "{x} clips: {idea}",
             "segment_system": "one of {x}", "segment_user": "one of {x}"})
for stage in ("script", "segment"):
    for m in pipeline.build_request(pid, stage, 0)["messages"]:
        assert "{x}" not in m["content"], (stage, m)
check("placeholders substituted in every stage, system and user alike", True)

# restore whatever the shipped default is for each box, whichever keys exist
for key in config.PROMPT_KEYS:
    config.save({key: config.DEFAULTS[key]})

# --- the carried frame's own description ---------------------------------
db.update_project(pid, settings={**db.get_project(pid)["settings"],
                                 "ref_images": [a1["id"], a2["id"], a3["id"]],
                                 "ref_image_count": 3, "continuation": True,
                                 "segment_count": 3})
config.save({"carried_frame_description":
             "This is the last frame from a previous clip and it has been included as "
             "reference image number {x}."})

d = pipeline.reference_descriptions(pid, 1)
check("the carried frame is described last", len(d) == 4, d)
check("{x} becomes the reference number, not the segment count",
      d[-1].endswith("reference image number 4."), d[-1])

# The trap: {x} means the segment count in every other prompt box. It must be
# resolved here first, or a 3-segment project would say "number 3".
body = pipeline.build_request(pid, "segment", 1)["messages"][-1]["content"]
check("the number survives prompt-placeholder substitution",
      "reference image number 4." in body, body[-300:])
check("it is not the segment count", "reference image number 3." not in body)

# fewer references -> the frame moves down
db.update_project(pid, settings={**db.get_project(pid)["settings"], "ref_image_count": 1})
check("with one reference the frame is number 2",
      pipeline.reference_descriptions(pid, 1)[-1].endswith("number 2."))
db.update_project(pid, settings={**db.get_project(pid)["settings"], "ref_image_count": 0})
check("with no references the frame is number 1",
      pipeline.reference_descriptions(pid, 1)[-1].endswith("number 1."))

# segment 1 never gets it
check("segment 1 has no carried-frame line",
      not any("last frame" in x for x in pipeline.reference_descriptions(pid, 0)))

# a cleared box still says something useful rather than an empty line
config.save({"carried_frame_description": ""})
check("an empty template falls back to a sensible line",
      "previous clip" in pipeline.reference_descriptions(pid, 1)[-1])

config.save({"carried_frame_description": config.DEFAULTS["carried_frame_description"]})
# --- the segment must lead, and never be silently replaced by the script ---
db.update_project(pid, settings={**db.get_project(pid)["settings"],
                                 "plan_beats": True, "send_full_script": True,
                                 "segment_count": 3})
config.save({"segment_user": config.DEFAULTS["segment_user"]})
db.update_segment(pid, 0, beat="BEAT ONE")

body = pipeline.build_request(pid, "segment", 0)["messages"][-1]["content"]
check("the segment comes before the full script",
      body.index("BEAT ONE") < body.index("THE SCRIPT BODY"), body[:200])
check("the script is labelled as context only",
      "CONTEXT ONLY" in body)

# A segment with no brief used to fall back to sending only the script, so the
# model divided the story itself. That must fail loudly instead.
db.update_segment(pid, 2, beat="")
raised = None
try:
    pipeline.build_request(pid, "segment", 2)
except RuntimeError as exc:
    raised = str(exc)
check("a segment with no brief is refused, not sent as the whole script",
      raised and "no script segment attached" in raised, raised)
check("the error says how to fix it", "Split script" in raised)

# ...unless splitting is off, when working from the script alone is the point
db.update_project(pid, settings={**db.get_project(pid)["settings"], "plan_beats": False})
body = pipeline.build_request(pid, "segment", 2)["messages"][-1]["content"]
check("with splitting off, the script alone is fine", "THE SCRIPT BODY" in body)
db.update_project(pid, settings={**db.get_project(pid)["settings"], "plan_beats": True})
db.update_segment(pid, 2, beat="BEAT THREE")

# the script can be left out entirely
db.update_project(pid, settings={**db.get_project(pid)["settings"],
                                 "send_full_script": False})
body = pipeline.build_request(pid, "segment", 0)["messages"][-1]["content"]
check("the script can be omitted", "THE SCRIPT BODY" not in body, body)
check("the segment survives on its own", "BEAT ONE" in body)
db.update_project(pid, settings={**db.get_project(pid)["settings"],
                                 "send_full_script": True})

print("\nall segment-context assertions passed")
