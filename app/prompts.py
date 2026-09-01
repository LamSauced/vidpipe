"""Starter system prompts. Both are editable in Settings — if your Open WebUI model
already carries a skill, clear these and the model's own instructions take over."""

OUTLINE_SYSTEM = """You plan stories before they are written.

Given an idea, plan a story told in {chapter_count} chapters. Decide what happens in each,
in order, so the whole arc is covered with no gaps and no repetition. Say who is present,
where it happens, and what changes by the end of the chapter.

Plan only — do not write the prose.

OUTPUT FORMAT — exactly this, nothing before or after:

=== CHAPTER 1 ===
What happens in the first chapter.

=== CHAPTER 2 ===
What happens in the second chapter.

Emit exactly {chapter_count} blocks. No commentary, no preamble, no markdown headings."""


OUTLINE_USER = """Idea:
{idea}

Plan this as a story in {chapter_count} chapters."""

OUTLINE_USER_BARE = "{idea}"


CHAPTER_SYSTEM = """You write one chapter of a story at a time.

Write the requested chapter in full, as continuous prose. Cover exactly what the plan gives
this chapter — no more, no less. Do not summarise, do not skip ahead, and do not write the
next chapter.

If a previous chapter is given, continue straight on from it: same characters, same
wardrobe, same location and time of day unless the plan says they change. Assume the reader
has just finished it, so do not recap.

Write the prose only. No chapter heading, no title, no commentary, no markdown."""


CHAPTER_USER = """STORY PLAN:
{outline}

Write chapter {index} of {chapter_count}.
[[brief]]
THIS CHAPTER COVERS EXACTLY THIS — everything in it, nothing beyond it:
{brief}
[[/brief]]
[[previous]]
PREVIOUS CHAPTER (you are continuing directly from this):
{previous}
[[/previous]]
[[first]]
This is the opening chapter. Establish the characters and the setting.
[[/first]]"""

CHAPTER_USER_BARE = """{outline}
[[brief]]
{brief}
[[/brief]]
[[previous]]
{previous}
[[/previous]]"""


def outline_user(idea: str, chapter_count: int, segment_seconds: float,
                 segment_count: int, template: str | None = None) -> str:
    return render(_choose(template, OUTLINE_USER, OUTLINE_USER_BARE),
                  {"idea": idea.strip(), "chapter_count": str(chapter_count)},
                  segment_seconds, segment_count)


def chapter_user(outline: str, index: int, chapter_count: int, brief: str | None,
                 previous: str | None, segment_seconds: float, segment_count: int,
                 template: str | None = None) -> str:
    values = {
        "outline": outline.strip(),
        "index": str(index + 1),
        "chapter_count": str(chapter_count),
        "brief": (brief or "").strip(),
        "chapter": (brief or "").strip(),
        "previous": (previous or "").strip(),
        "first": "" if index == 0 else "x",
    }
    return render(_choose(template, CHAPTER_USER, CHAPTER_USER_BARE), values,
                  segment_seconds, segment_count, inverted={"first"})


SCRIPT_SYSTEM = """You write scripts for short videos.

Given an idea, write a script for a video of about {total_seconds} seconds. Write it as \
continuous prose, in order, the way the finished video plays — not as a shot list, not \
in numbered sections, and not in markdown.

Cover what happens, where it happens, how it is lit and framed, any spoken line with who \
says it and how it sounds, and what can be heard. Keep one through-line: the same \
characters, wardrobe, location and lighting carry through unless the script says \
otherwise. Describe only what a camera would see and a microphone would hear."""


SEGMENT_SYSTEM = """You convert a video script into a single MiniMax H3 prompt for one \
{segment_seconds}-second segment.

Output the prompt only — no preamble, no markdown, no commentary. Use this structure:

For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot N]) \
is fully referenced.

integrated_multimodal_description: [Shot N] <visual style and lighting>. <One continuous \
take: what the subject does, in order, for the full {segment_seconds} seconds, with camera \
framing and any movement. Spoken lines appear inline as: in a <voice description> (S1): \
<d>[English] the line</d>.> The shot ends with <where the frame lands at the final moment>.

overall_soundscape: <diegetic sound — room tone, movement, contact sounds, breathing>

non_diegetic_music: <score, instrumentation, how it moves and ends>

Rules:
- Cover exactly {segment_seconds} seconds of action. One unbroken take, no cuts.
- Present tense, concrete and physical. Describe only what is visible or audible.
- Carry wardrobe, hair, location, lighting and framing over from the previous segment \
verbatim where they haven't changed — this is what keeps segments consistent.
- When a previous segment is given, begin exactly where its final frame left off, and say \
so in the opening reference line.
- Describe the subject fully every time. The model has no memory of earlier segments."""


PLAN_SYSTEM = """You divide a video script into consecutive clips of equal length.

The script will be shot as {segment_count} clips of {segment_seconds} seconds each. \
Split it into exactly {segment_count} segments covering the whole script in order, with \
no gaps and no overlap.

Each segment must hold roughly {segment_seconds} seconds of action — one unbroken take, \
no cuts. If the script front-loads its events, spread them; if a stretch is thin, say \
what continues through it. Every segment needs something to actually happen.

OUTPUT FORMAT — exactly this, nothing before or after:

=== SEGMENT 1 ===
What happens in the first clip, in order.

=== SEGMENT 2 ===
What happens in the second clip.

Emit exactly {segment_count} blocks. No commentary, no preamble, no markdown headings."""


PLAN_USER = """SCRIPT:
{script}

Split this into {segment_count} segments of {segment_seconds} seconds each ({ranges}).
Return the segment blocks only."""

PLAN_USER_BARE = "{script}"


def plan_user(script: str, segment_count: int, segment_seconds: float,
              template: str | None = None) -> str:
    ranges = ", ".join(
        f"{i+1}: {i*segment_seconds:g}-{(i+1)*segment_seconds:g}s" for i in range(segment_count)
    )
    return render((_choose(template, PLAN_USER, PLAN_USER_BARE)),
                  {"script": script.strip(), "ranges": ranges},
                  segment_seconds, segment_count)


SCRIPT_USER = """Idea:
{idea}

Write the script. Target length: {total_seconds} seconds."""

#: What a stage sends when its user-message box is cleared. Blank means "no
#: wrapper text of mine" — it must never mean "send nothing", or the model
#: would get an empty message and the idea would be silently dropped.
SCRIPT_USER_BARE = "{idea}"


def script_user(idea: str, segment_count: int, segment_seconds: float,
                template: str | None = None) -> str:
    return render((_choose(template, SCRIPT_USER, SCRIPT_USER_BARE)), {"idea": idea.strip()},
                  segment_seconds, segment_count)


SEGMENT_USER = """THIS SEGMENT COVERS EXACTLY THIS — everything in it, nothing beyond it:
{segment}

Write segment {index} of {segment_count}: {start} to {end} of the finished video.
[[script]]
THE FULL SCRIPT, FOR CONTEXT ONLY — do not cover any of it beyond the part above:
{script}
[[/script]]
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
[[/first]]"""


SEGMENT_USER_BARE = """[[segment]]
{segment}
[[/segment]]
[[script]]
{script}
[[/script]]
[[references]]
{references}
[[/references]]
[[previous]]
{previous}
[[/previous]]"""


def segment_user(
    script: str,
    index: int,
    total: int,
    segment_seconds: float,
    previous_prompt: str | None,
    beat: str | None = None,
    references: list[str] | None = None,
    template: str | None = None,
) -> str:
    start = index * segment_seconds
    refs = "\n".join(f"Reference {i}'s description: {text}"
                      for i, text in enumerate(references or [], start=1))
    values = {
        "script": script.strip(),
        "index": str(index + 1),
        "start": f"{start:g}s",
        "end": f"{start + segment_seconds:g}s",
        "segment": (beat or "").strip(),
        "beat": (beat or "").strip(),
        "previous": (previous_prompt or "").strip(),
        "previous_prompt": (previous_prompt or "").strip(),
        "references": refs,
        "first": "" if index == 0 else "x",   # drives the [[first]] block
    }
    return render((_choose(template, SEGMENT_USER, SEGMENT_USER_BARE)), values, segment_seconds, total,
                  inverted={"first"})


#: Token -> which value it stands for. Every name is accepted braced ({x}),
#: doubly braced ({{x}}) and bare when it is unambiguous enough to be safe
#: (SEGMENT_COUNT but not X, which would hit ordinary prose).
TOKENS = {
    "count": ("segment_count", "segment_amount", "segments", "num_segments",
              "number_of_segments", "clip_count", "clips", "x", "n"),
    "seconds": ("segment_seconds", "segment_duration", "seconds", "duration",
                "clip_seconds", "y"),
    "total": ("total_seconds", "total_duration", "total_length", "total"),
    "chapters": ("chapter_count", "chapter_amount", "chapters", "num_chapters"),
}

#: Bare (unbraced) names only substituted when written in this exact form —
#: uppercase with an underscore, so a sentence containing "segments" or "x"
#: is never mangled.
_BARE_SAFE = {name for names in TOKENS.values() for name in names if "_" in name}


def fill(template: str, segment_seconds: float, segment_count: int,
         chapter_count: int | None = None) -> str:
    """Substitute placeholders in a system prompt.

    Deliberately not str.format: a prompt containing a literal brace — a JSON
    example, or the H3 <d>[English]</d> markup — would raise KeyError and take
    down the whole run. This only ever replaces names it knows.
    """
    import re as _re

    values = {
        "count": str(int(segment_count)),
        "seconds": f"{segment_seconds:g}",
        "total": f"{segment_seconds * segment_count:g}",
        "chapters": str(int(chapter_count)) if chapter_count is not None else "",
    }
    lookup = {name: values[kind] for kind, names in TOKENS.items() for name in names
              if values[kind] != ""}

    out = template

    # {name}, {{name}}, ${name}, %name% — any case, optional surrounding spaces
    def braced(m):
        name = m.group("name").strip().lower()
        return lookup.get(name, m.group(0))

    out = _re.sub(r"\{\{\s*(?P<name>[A-Za-z_]+)\s*\}\}", braced, out)
    out = _re.sub(r"\$\{\s*(?P<name>[A-Za-z_]+)\s*\}", braced, out)
    out = _re.sub(r"\{\s*(?P<name>[A-Za-z_]+)\s*\}", braced, out)
    out = _re.sub(r"%(?P<name>[A-Za-z_]+)%", braced, out)

    # Bare uppercase names, e.g. SEGMENT_AMOUNT
    def bare(m):
        name = m.group(0).lower()
        return lookup[name] if name in _BARE_SAFE else m.group(0)

    out = _re.sub(r"\b[A-Z][A-Z_]{4,}\b", bare, out)
    return out


def tokens_help() -> list[dict]:
    """For the UI: what may be written, and what it becomes."""
    return [
        {"stands_for": "number of segments", "write": "{segment_count}, {x}, SEGMENT_AMOUNT"},
        {"stands_for": "seconds per segment", "write": "{segment_seconds}, {duration}"},
        {"stands_for": "total length", "write": "{total_seconds}"},
    ]


def _choose(template: str | None, full: str, bare: str) -> str:
    """None means "not specified, use the default"; blank means "just the data"."""
    if template is None:
        return full
    return template if template.strip() else bare


def render(template: str, values: dict, segment_seconds: float, segment_count: int,
           inverted: set | None = None, chapter_count: int | None = None) -> str:
    """Fill a user-message template.

    `[[name]] ... [[/name]]` blocks are kept only when `values[name]` is
    non-empty, so a template can carry the previous-segment section without it
    appearing on segment 1. Names in `inverted` behave the opposite way.
    """
    import re as _re

    inverted = inverted or set()
    out = template

    def block(m):
        name, body = m.group("name"), m.group("body")
        present = bool((values.get(name) or "").strip())
        if name in inverted:
            present = not present
        return ("\n" + body.strip("\n") + "\n") if present else ""

    out = _re.sub(
        r"\[\[(?P<name>[a-z_]+)\]\]\n?(?P<body>.*?)\[\[/(?P=name)\]\]",
        block, out, flags=_re.S)

    for name, value in values.items():
        for form in (f"{{{name}}}", f"{{{{{name}}}}}", f"${{{name}}}"):
            out = out.replace(form, str(value))

    if chapter_count is None:
        try:
            chapter_count = int(values.get("chapter_count") or 0) or None
        except (TypeError, ValueError):
            chapter_count = None
    out = fill(out, segment_seconds, segment_count, chapter_count)
    return _re.sub(r"\n{3,}", "\n\n", out).strip()


# --------------------------------------------------------------------------
# Output contracts
#
# The planning call is the only one that gets parsed. Anything the per-segment
# call returns is used as the prompt verbatim, after light cleanup.
# --------------------------------------------------------------------------

BEAT_DELIMITER = r"^\s*(?:={2,}|-{2,}|#{1,3})?\s*(?:BEAT|SEGMENT|CLIP|SHOT|CHAPTER|PART)\s*#?\s*(\d+)\s*(?:={2,}|-{2,})?\s*:?\s*$"

#: Accepted planning replies, in the order they're tried:
#:   1. ``=== BEAT 1 ===`` delimiter blocks (also SEGMENT / CLIP / SHOT, any
#:      number of = - or #, with or without a trailing colon)
#:   2. a JSON array of strings, with or without ``` fences and preamble
#:   3. numbered lines: ``1. text`` / ``Beat 1: text``
#: Anything else raises, rather than silently misaligning clips.

FENCE_NOISE = ("```json", "```JSON", "```", "~~~")


def clean_prompt(text: str) -> str:
    """Tidy a per-segment reply without changing its content.

    Strips code fences and a leading ``Segment 2:`` style label, since models add
    those even when told not to and they would otherwise be sent to the sampler.
    """
    out = (text or "").strip()
    for fence in FENCE_NOISE:
        if out.startswith(fence):
            out = out[len(fence):].lstrip()
        if out.endswith("```") or out.endswith("~~~"):
            out = out[:-3].rstrip()
    import re as _re
    out = _re.sub(
        r"^\s*(?:\*\*|__)?\s*(?:BEAT|SEGMENT|CLIP|SHOT|PROMPT)\s*#?\s*\d+"
        r"\s*[:.\-—]?\s*(?:\*\*|__)?\s*[:.\-—]?\s*",
        "", out, count=1, flags=_re.I)
    return out.strip()
