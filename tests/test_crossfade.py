"""Joining clips, with and without an overlapping fade."""
import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import video  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="vidpipe-xfade-"))


def make(name, seconds, colour, tone):
    path = TMP / name
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", f"color=c={colour}:s=320x180:r=24:d={seconds}",
         "-f", "lavfi", "-i", f"sine=f={tone}:d={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(path)], check=True, capture_output=True)
    return path


def check(label, cond, detail=""):
    assert cond, f"{label} failed {detail}"
    print(f"  ok  {label}")


clips = [make("a.mp4", 4, "red", 440), make("b.mp4", 4, "green", 550),
         make("c.mp4", 4, "blue", 660)]
lengths = [asyncio.run(video.probe_duration(c)) for c in clips]
total = sum(lengths)
check("fixture clips built", all(abs(l - 4) < 0.3 for l in lengths), lengths)

# hard cut: full length
plain = asyncio.run(video.concat(clips, TMP / "plain.mp4"))
plain_len = asyncio.run(video.probe_duration(plain))
check("a hard cut keeps the full running time", abs(plain_len - total) < 0.4,
      (plain_len, total))

# crossfade: each transition overlaps, so the result is shorter by fade x gaps
FADE = 0.6
faded = asyncio.run(video.concat(clips, TMP / "faded.mp4", crossfade_seconds=FADE))
faded_len = asyncio.run(video.probe_duration(faded))
expected = total - FADE * (len(clips) - 1)
check("a crossfade shortens by the overlap",
      abs(faded_len - expected) < 0.4, (faded_len, expected))
check("the crossfaded file is shorter than the hard cut", faded_len < plain_len - 0.5)

# audio survives, and is one continuous stream
check("audio is present in the crossfaded output",
      asyncio.run(video._has_audio(faded)))
streams = subprocess.run(
    ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
     "-of", "csv=p=0", str(faded)], capture_output=True, text=True).stdout.split()
check("exactly one video and one audio stream",
      sorted(streams) == ["audio", "video"], streams)

# the middle of a transition should be neither pure red nor pure green
frame = TMP / "mid.png"
subprocess.run(["ffmpeg", "-y", "-ss", str(4 - FADE / 2), "-i", str(faded),
                "-frames:v", "1", str(frame)], check=True, capture_output=True)
from PIL import Image  # noqa: E402
r, g, b = Image.open(frame).convert("RGB").resize((1, 1)).getpixel((0, 0))
check("mid-transition frame is a blend, not either source",
      r > 20 and g > 20 and r < 235 and g < 235, (r, g, b))

# a fade longer than the clips is refused with an explanation, not a crash
raised = None
try:
    asyncio.run(video.concat(clips, TMP / "bad.mp4", crossfade_seconds=9))
except video.FrameError as exc:
    raised = str(exc)
check("an over-long fade is refused clearly",
      raised and "shortest" in raised, raised)

# clips without audio still join
silent = [make("s1.mp4", 3, "white", 440), make("s2.mp4", 3, "black", 440)]
for p in silent:
    subprocess.run(["ffmpeg", "-y", "-i", str(p), "-an", "-c:v", "copy",
                    str(p.with_name("mute_" + p.name))], check=True, capture_output=True)
mute = [p.with_name("mute_" + p.name) for p in silent]
out = asyncio.run(video.concat(mute, TMP / "mute.mp4", crossfade_seconds=0.5))
check("silent clips crossfade without an audio stream",
      not asyncio.run(video._has_audio(out)))

print("\nall crossfade assertions passed")

# --- the on/off switch ----------------------------------------------------
import os  # noqa: E402
os.environ.setdefault("VIDPIPE_DATA", tempfile.mkdtemp(prefix="vidpipe-xfsw-"))
from app import db  # noqa: E402

db.init()
check("crossfade is off by default", db.DEFAULT_SETTINGS["crossfade_on"] is False)
check("but a sensible length is preset", db.DEFAULT_SETTINGS["crossfade"] == 0.5)


def fade_for(settings):
    """Mirror of the decision join_clips makes."""
    s = {**db.DEFAULT_SETTINGS, **settings}
    return float(s.get("crossfade") or 0) if s.get("crossfade_on") else 0.0


check("off means a hard cut even with seconds set",
      fade_for({"crossfade_on": False, "crossfade": 0.8}) == 0.0)
check("on uses the seconds given",
      fade_for({"crossfade_on": True, "crossfade": 0.8}) == 0.8)
check("on with zero seconds is still a cut",
      fade_for({"crossfade_on": True, "crossfade": 0}) == 0.0)

print("\nall crossfade-switch assertions passed")
