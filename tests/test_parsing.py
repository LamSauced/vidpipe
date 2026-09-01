"""The beat parser and prompt cleanup, against the shapes models actually emit."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import prompts  # noqa: E402
from app.pipeline import _parse_beats  # noqa: E402

DELIMITED = """Sure!

=== BEAT 1 ===
She opens the shutters.

=== BEAT 2 ===
She pulls on a coat.
"""

LOOSE_DELIM = """## SEGMENT 1:
First thing happens here.
## SEGMENT 2:
Second thing happens here.
"""

JSON_FENCED = '''Here you go:
```json
["First beat text here.", "Second beat text here."]
```'''

NUMBERED = """1. She opens the shutters and looks out
   at the mist over the garden.
2. She pulls on a wool coat and steps outside.
"""

for label, text in (("delimited", DELIMITED), ("loose delimiters", LOOSE_DELIM),
                    ("fenced json", JSON_FENCED), ("numbered", NUMBERED)):
    beats = _parse_beats(text, 2)
    assert len(beats) == 2, (label, beats)
    assert all(len(b) > 10 for b in beats), (label, beats)
    assert "===" not in beats[0] and "```" not in beats[0], (label, beats)
    print(f"  ok  {label}: {beats[0][:40]!r}")

# multi-line bodies survive intact
beats = _parse_beats("=== BEAT 1 ===\nLine one.\nLine two.\n\n=== BEAT 2 ===\nOther.", 2)
assert beats[0] == "Line one.\nLine two.", beats
print("  ok  multi-line beat bodies kept")

# A wrong count is reported to the caller, not raised: plan_beats retries with a
# corrective turn and can adopt the model's count. Only unreadable output raises.
got = _parse_beats(DELIMITED, 5)
assert len(got) == 2, got
print("  ok  a count mismatch returns what was found, for the caller to handle")

try:
    _parse_beats("total nonsense with no blocks at all", 3)
    raise AssertionError("unreadable output should raise")
except RuntimeError as exc:
    assert "segment" in str(exc).lower(), exc
print("  ok  unreadable output still raises")

# prompt cleanup
cases = [
    ("```\nSegment 2: the body\n```", "the body"),
    ("```json\nthe body\n```", "the body"),
    ("**SEGMENT 3:** the body", "the body"),
    ("SHOT 1 — the body", "the body"),
    ("For the target video, at 0.00 seconds", "For the target video, at 0.00 seconds"),
]
for raw, expected in cases:
    got = prompts.clean_prompt(raw)
    assert got == expected, f"{raw!r} -> {got!r}, wanted {expected!r}"
print("  ok  prompt cleanup strips fences and labels, leaves real prompts alone")

print("\nall parsing assertions passed")
