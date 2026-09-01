"""Output-node detection across workflows that save in different ways."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import comfy, workflow  # noqa: E402
from tests.test_workflow import OI  # noqa: E402

OI2 = dict(OI)
OI2["VHS_VideoCombine"] = {"input": {"required": {
    "images": ["IMAGE"], "audio": ["AUDIO"], "frame_rate": ["FLOAT", {}],
    "loop_count": ["INT", {}], "filename_prefix": ["STRING", {}],
    "format": [["video/nvenc_h264-mp4"]], "pix_fmt": [["yuv420p"]],
    "bitrate": ["INT", {}], "megabit": ["BOOLEAN", {}],
    "save_metadata": ["BOOLEAN", {}], "pingpong": ["BOOLEAN", {}],
    "save_output": ["BOOLEAN", {}]}}}


def check(label, cond, detail=""):
    assert cond, f"{label} failed {detail}"
    print(f"  ok  {label}")


FIX = ROOT / "tests" / "fixtures"
cases = [("workflow.ui.json", "SaveVideo"), ("workflow.vhs.json", "VHS_VideoCombine")]

for filename, expected in cases:
    path = FIX / filename
    if not path.exists():
        continue
    api = workflow.ui_to_api(json.loads(path.read_text()), OI2)
    roles = workflow.detect_roles(api)
    save = roles.get("save")
    check(f"{filename}: output node found", save is not None, roles.get("warnings"))
    check(f"{filename}: it is the {expected}",
          api[save["node"]]["class_type"] == expected, api[save["node"]]["class_type"])
    check(f"{filename}: the filename input was identified",
          save["key"] == "filename_prefix", save)

    g = workflow.Patcher(api, roles).build(
        "PROMPT", images=["a.png"] + [None] * 5, audios=["v.wav", None, None],
        duration=15.0, seed=1, steps=8, filename_prefix="video/test_p1_s00",
        output_fps=24)
    ins = g[save["node"]]["inputs"]
    check(f"{filename}: prefix patched", ins["filename_prefix"] == "video/test_p1_s00")
    check(f"{filename}: the output node survives pruning", save["node"] in g)
    if "save_output" in ins:
        check(f"{filename}: saving is on, not preview-only", ins["save_output"] is True)
    if "frame_rate" in ins:
        check(f"{filename}: frame rate override applied", ins["frame_rate"] == 24.0)

    # leaving the override at 0 must not touch the workflow's own value
    g2 = workflow.Patcher(api, roles).build(
        "PROMPT", images=["a.png"] + [None] * 5, audios=["v.wav", None, None],
        filename_prefix="p", output_fps=None)
    if "frame_rate" in g2[save["node"]]["inputs"]:
        original = json.loads(path.read_text())
        stored = next(n["widgets_values"]["frame_rate"] for n in original["nodes"]
                      if n["id"] == int(save["node"]))
        check(f"{filename}: no override leaves the frame rate alone",
              g2[save["node"]]["inputs"]["frame_rate"] == stored)

# whatever the output node, the finished file is located from history, by
# filename — not by guessing at the prefix
vhs_history = {"233": {"gifs": [
    {"filename": "MiniMax_H3_00001.mp4", "subfolder": "video", "type": "output",
     "format": "video/h264-mp4"}]}}
media = comfy.collect_media(vhs_history)
check("VHS history output is recognised", len(media) == 1 and
      media[0]["filename"].endswith(".mp4"), media)

mixed = {"9": {"images": [{"filename": "preview.png", "subfolder": "", "type": "output"}]},
         "233": {"gifs": [{"filename": "clip.mp4", "subfolder": "", "type": "output"}]}}
check("a video is preferred over a preview image",
      comfy.collect_media(mixed)[0]["filename"] == "clip.mp4")

print("\nall output assertions passed")
