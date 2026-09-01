"""Reference counts, the reserved frame slot, and bypass behaviour."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["VIDPIPE_DATA"] = tempfile.mkdtemp(prefix="vidpipe-slots-")

from app import db  # noqa: E402
from app.pipeline import _audio_values, _slot_values  # noqa: E402

db.init()
ids = [db.add_asset("image", f"r{i}.png", f"/tmp/r{i}.png", f"vidpipe/r{i}.png")["id"]
       for i in range(5)]
aids = [db.add_asset("audio", f"a{i}.wav", f"/tmp/a{i}.wav", f"vidpipe/a{i}.wav")["id"]
        for i in range(3)]

SLOTS = 6  # what the sample workflow exposes once spares are counted


def images(count, carry, slots=SLOTS, assigned=None):
    return _slot_values(
        {"ref_images": assigned if assigned is not None else ids, "ref_image_count": count},
        slots, carry)


def check(label, got, want):
    assert got == want, f"{label}\n  got  {got}\n  want {want}"
    print(f"  ok  {label}")


# only as many static references as the count allows
v, warn = images(2, None)
check("2 static, no carry", v, ["vidpipe/r0.png", "vidpipe/r1.png"] + [None] * 4)
assert warn is None

# the carried frame lands immediately after them
v, warn = images(2, "vidpipe/frame.png")
check("2 static + carry -> frame is slot 2",
      v, ["vidpipe/r0.png", "vidpipe/r1.png", "vidpipe/frame.png"] + [None] * 3)
assert warn is None

# raising the count pushes the frame further along, never displacing a static
v, _ = images(4, "vidpipe/frame.png")
check("4 static + carry -> frame is slot 4",
      v, [f"vidpipe/r{i}.png" for i in range(4)] + ["vidpipe/frame.png", None])

# zero static references is legitimate: the frame alone
v, _ = images(0, "vidpipe/frame.png")
check("no static, carry only", v, ["vidpipe/frame.png"] + [None] * 5)

# filling every slot with statics and still carrying: the frame wins, with a warning
v, warn = images(6, "vidpipe/frame.png", slots=6, assigned=ids + [ids[0]])
check("over capacity keeps the frame last",
      v, [f"vidpipe/r{i}.png" for i in range(5)] + ["vidpipe/frame.png"])
assert warn and "Dropped" in warn, warn
print("  ok  over-capacity warns")

# a count larger than the assignments is harmless
v, _ = images(9, None, assigned=[ids[0]])
check("count beyond what is assigned", v, ["vidpipe/r0.png"] + [None] * 5)

# audio honours its own count
check("audio count clamps",
      _audio_values({"ref_audios": aids, "ref_audio_count": 1}, 3),
      ["vidpipe/a0.wav", None, None])
check("audio count zero",
      _audio_values({"ref_audios": aids, "ref_audio_count": 0}, 3), [None] * 3)

print("\nall slot assertions passed")
