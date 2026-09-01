# vidpipe

Idea → script → 15-second MiniMax H3 prompts → ComfyUI renders, in one browser tab.

```
        idea
         │
         ▼  1 · SCRIPT MODEL          (or long-story mode, below)
        script
         │
         ▼  2 · SEGMENTER MODEL   (once)
        segment 1 | segment 2 | segment 3 | ...
         │
         ▼  3 · PROMPT MODEL      (once per segment)
        H3 prompt 1 | H3 prompt 2 | H3 prompt 3 | ...
             input: full script + this segment + the previous segment's prompt
         │
         ▼  (LLM unloaded here, so ComfyUI gets the VRAM)
        ComfyUI, one queue per prompt
             input: prompt + reference images + audio + last frame of the previous clip
         │
         ▼
        ffmpeg joins the clips in order
```

## Requirements

- Python 3.10+
- A reachable Open WebUI and ComfyUI
- `ffmpeg` on PATH (for pulling the last frame out of each clip). Without it, install
  `opencv-python-headless` and the fallback path takes over.

## Run

```bash
./run.sh                 # creates .venv, installs, serves on http://localhost:8777
```

Or by hand:

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8777
```

Everything lives in `data/` — SQLite, uploaded references, downloaded clips, extracted
frames. Delete that folder to start over. Set `VIDPIPE_DATA` to move it.

## First run

1. **Settings** — Open WebUI URL and API key (Settings → Account → API Keys in Open
   WebUI), ComfyUI URL. Hit **Test**, then **Refresh list** and pick your two models.
   Fields save as you change them.

   The Open WebUI URL is just the host (`http://localhost:3000`) — don't include a path.
   Which endpoint your build uses is worked out automatically: `/api/chat/completions`,
   `/api/v1/chat/completions`, `/v1/chat/completions` and `/ollama/v1/chat/completions`
   are tried in that order, and the one that answers is remembered. **Test** shows you
   which paths it settled on, so you can confirm they match what your instance serves.
2. **Workflow** — load your ComfyUI JSON. The Workflow tab then shows which node each
   thing was wired to. If a row says *not found*, that control won't be patched.
3. **References** — upload reference images and audio, then assign them to slots.
4. **Build** — write an idea, set the segment count, press **Run everything**.

Any stage also runs on its own: **Write script**, **Build segment prompts**, **Render
all**, or **Rewrite prompt** / **Render this** on a single segment. Everything you can
generate you can also just type in — paste your own script, hand-edit any prompt.

## System prompts

Settings has a system prompt box per model. **Leave them empty if your Open WebUI model
already carries a skill** — vidpipe then sends only the user message and your skill does
the work. Fill them in (or press *Insert the built-in prompts*) if you'd rather drive a
plain model from here. `{segment_seconds}`, `{segment_count}` and `{total_seconds}` are
substituted.

Whatever the system prompt, each segment call always carries:

- the full script, labelled `FULL SCRIPT`
- which segment this is, and its timecode range
- the previous segment's prompt verbatim, labelled `PREVIOUS SEGMENT'S PROMPT`

## What gets sent, and to which model

Three calls, each with its own model, system prompt and temperature in Settings.
**Show what gets sent** on the Settings tab renders the exact outgoing request for any
stage — it calls the same builder the run does, so it can't drift from reality.

| # | Stage | Model setting | User message contains |
| --- | --- | --- | --- |
| 1 | idea → script | **Script model** | the idea and the target length |
| 1a | idea → chapter plan | **Outline model** (blank = script model) | the idea, the chapter count |
| 1b | plan → one chapter | **Chapter model** (blank = script model) | the plan, this chapter's brief, the previous chapter |
| 2 | script → N segments | **Segmenter model** (blank = prompt model) | the full script, the count, the timecode ranges |
| 3 | one segment → H3 prompt | **Prompt model** | full script, this segment's index and timecodes, its slice of script, the previous segment's prompt |

**Stage 3 runs once per segment, as a separate call with no shared history.** A 4-segment
project makes 1 segmenter call and 4 prompt calls, producing 4 independent H3 prompts. They
run in order, because each one is given the previous segment's finished prompt. Stage 2 runs
once and is optional — with it off, stage 3 gets the whole script and the segment index
alone, and decides its own share.

Each stage has its own model, system prompt and temperature. Setting all three to the same
model is fine; they're separate so you can put a writer on stage 1, a cheap fast model on
stage 2, and your H3 skill on stage 3.

Note the config keys in `data/config.json` predate this naming: the segmenter is
`beat_model` / `beat_system` / `beat_temperature`, and the prompt model is
`segment_model` / `segment_system` / `segment_temperature`. The UI labels are what matter.

Each stage 3 call is **independent** — no conversation history is carried. Everything the
model needs is rebuilt from the database each time, in this order:

```
FULL SCRIPT (for context — do not cover all of it):
<the script, as it is in the editor>

Write segment 2 of 4: 15s to 30s of the finished video.

THIS SEGMENT COVERS EXACTLY THIS BEAT — everything in it, nothing beyond it:
<this segment's beat>

PREVIOUS SEGMENT'S PROMPT (the frame you are continuing from):
<segment 1's prompt, verbatim>

Continue directly from where that segment ends. Repeat the subject, wardrobe,
setting and lighting descriptions so this segment stands alone.
```

Segment 1 gets a different closing line ("This is the opening segment...") and no
previous-prompt block. The beat block is omitted when beat planning is off.

Because each call is independent, editing segment 2's prompt by hand changes what
segment 3 receives — regenerating forward from an edit propagates it.

**Every prompt is editable and nothing is added behind it.** Settings shows six boxes — a
system prompt and a user message for each stage — prefilled with the built-ins on first
run. What's in the box is what's sent, and **a box you clear stays clear**: the seeding
happens once, not on every save. Clearing the system box sends no system message at all,
which is what you want when the model already carries its own skill in Open WebUI.
*Insert the built-in prompts* restores all six.

The user message matters as much as the system prompt: it carries the idea, the script, the
segment and the previous prompt. Earlier versions hardcoded it, which meant stage 1 asked
for "N beats, 15 seconds each" no matter what the system prompt said.

Stage 3 leads with the segment, then the full script as clearly-labelled context. That
order matters: with the script first, models tend to follow it and cover the whole story in
every clip. **Also send the whole script as context** (Build tab) turns the script off
entirely if the segments stand on their own.

If splitting is on and a segment has no text attached, the run stops rather than sending
only the script — otherwise the model silently picks its own share of the story and every
clip drifts. Run *Split script*, paste something into that card, or turn splitting off.

Stage 3's template supports conditional blocks, since segment 1 has no previous prompt:

```
[[previous]]
PREVIOUS SEGMENT'S PROMPT (the frame you are continuing from):
{previous}
[[/previous]]
[[first]]
This is the opening segment. Establish the subject and setting.
[[/first]]
```

A `[[name]] … [[/name]]` block is kept only when that value exists. Available values:
`{script}` `{segment}` `{references}` `{previous}` `{index}` `{start}` `{end}`, plus the
placeholders below. Stage 1 has `{idea}`; stage 2 has `{script}` and `{ranges}`.

### Placeholders in system prompts

Write any of these in **any of the six boxes** — system prompts and user messages alike,
for all three stages — and the real value is substituted just before sending:

| Stands for | Write any of |
| --- | --- |
| number of segments | `{segment_count}` `{segment_amount}` `{segments}` `{clips}` `{x}` `{n}` `SEGMENT_COUNT` `SEGMENT_AMOUNT` |
| seconds per segment | `{segment_seconds}` `{segment_duration}` `{duration}` `{seconds}` `{y}` |
| total length | `{total_seconds}` `{total_duration}` `{total}` |

Case doesn't matter and `{{name}}`, `${name}` and `%name%` all work. So a segmenter system prompt reading `Split a story into {x} segments.` becomes
`Split a story into 4 segments.` when the project is set to 4 — and the same `{x}` in the
segmenter's user message is substituted too. Note the value is the bare number, so write
`{segment_seconds} seconds` if you want the unit.

Names are only replaced when braced, or written bare in uppercase-with-underscore form
(`SEGMENT_AMOUNT`). A lone `x` or the word `segments` in ordinary prose is left alone, and
unknown placeholders like `{whatever}` pass through untouched — as do literal braces, so a
JSON example or the H3 `<d>[English]</d>` markup in your prompt is safe.

### Reference descriptions

Each reference image has a description box on the References tab. Whatever you write is
sent to the prompt model with the segment, numbered to match the slots:

```
REFERENCE IMAGES SENT WITH THIS SEGMENT, in order — <Picture 1> is the first,
<Picture 2> the second, and so on:
Reference 1's description: the woman: dark braids, grey wool coat, mid-20s
Reference 2's description: the room: lattice window, bare wooden floor, dawn light
Reference 3's description: the final frame of the previous segment, which this one continues from
```

**The carried frame describes itself.** Settings → References has a *Carried-frame
description* box, sent as that frame's entry in the list. `{x}` in it becomes the reference
number the frame actually landed on — with three references assigned, it renders as
"…included as reference image number 4." Note `{x}` means the segment count in every other
prompt box; in this one box it means the reference number, and is resolved before the other
placeholders run.

**Numbering.** The slot pickers are labelled 1, 2, 3… to match `Reference 1, 2, 3` in the
prompt. The workflow's own input names are 0-based, so slot 1 is `ref_image_0` — hover a
slot number to see which input it is. The mapping:

| Slot picker | Workflow input | Prompt says |
| --- | --- | --- |
| 1 | `ref_image_0` | Reference 1 |
| 2 | `ref_image_1` | Reference 2 |
| 3 | `ref_image_2` | Reference 3 |

The order is computed by the same function the renderer uses to fill slots, so the two
can't drift. The carried frame is appended automatically with its own description and only
appears from segment 2 onward. A reference left undescribed keeps its number — nothing
shifts — and is listed by filename so the model knows a picture is there.

Whether your MiniMax build calls the first reference `<Picture 1>` or something else is up
to the model; vidpipe describes them by position ("Reference 1 is the first picture
reference"), so adjust your system prompt if your build numbers them differently.

## What the models must return

Only the **planning** call is parsed. Everything else is passed through.

### Stage 2 (script → segments)

Preferred format, one block per clip:

```
=== SEGMENT 1 ===
What happens in the first clip, in order.

=== SEGMENT 2 ===
What happens in the second clip.
```

The delimiter line is matched loosely: `SEGMENT`, `BEAT`, `CLIP` or `SHOT`, any number
(or none) of `=`, `-` or `#` around it, optional `#` before the digit, optional trailing
colon. `## SEGMENT 1:` and `--- CLIP 1 ---` both work. Everything up to the next delimiter
is that beat's body, newlines included. Text before the first delimiter is discarded, so a
chatty preamble is harmless.

Two fallbacks if your skill emits something else:

- **JSON array of strings** — `["first segment", "second segment"]`, fences and preamble tolerated
- **Numbered lines** — `1. text` or `Segment 1: text`, with continuation lines folded in

If the count doesn't match the project's segment count, the run stops and tells you both
numbers. It never pads or truncates silently, because that would misalign every clip after
the mismatch.

### Stage 3 (segment → H3 prompt)

**No format required — the entire reply becomes the prompt.** Write your skill to emit the
MiniMax H3 prompt and nothing else.

Two things are stripped before storage, since models add them regardless of instructions:

- surrounding ``` or ~~~ fences
- a single leading label: `Segment 2:`, `**SHOT 3** —`, `PROMPT 1.` and similar

Nothing else is touched — no reformatting, no trimming of the H3 field names. What the
model returns is what reaches the sampler, so `integrated_multimodal_description`,
`overall_soundscape` and `non_diegetic_music` pass through exactly as written.

## Long stories

One "write the whole thing" call degrades as the story gets longer — the model loses the
thread, rushes the ending, or repeats itself. Tick **Long story** on the Build tab and
stage 1 splits in two:

1. **Outline model** reads the idea and returns a plan as `=== CHAPTER n ===` blocks, one
   per chapter.
2. **Chapter model** is then called once per chapter, given the plan, that chapter's brief,
   and the previous chapter in full. Every call stays short.

The chapters are joined, in order, into the script — which is exactly what stage 2 then
segments, so nothing downstream changes.

A Story panel appears with the plan and one card per chapter, each showing its word count.
Everything is editable: rewrite a brief and press **Rewrite chapter** to redo just that one,
or edit the prose directly. Any edit re-assembles the script immediately.

Set the chapter count independently of the segment count — a 12-chapter story cut into 6
clips is fine. Outline and chapter models each have their own temperature; the defaults run
the planner cooler (0.6) than the prose (0.9).

Leave **Long story** off and stage 1 is a single call, as before.

## Why stage 2 exists

Asking a model to write "segment 3 of 5" from a whole script means it decides on the fly
how much ground segment 3 covers — which is how you get four clips of setup and one that
sprints through the ending. Stage 2 settles that once: the segmenter reads the script and
returns N slices, one per clip, and each slice is handed to the stage 3 call that turns it
into an H3 prompt.

The slices are editable — each segment card shows its slice above its prompt. Rewrite one
and press **Rewrite prompt** on that card to redo just that segment. Turn the pass off with
*Split the script into segments first* and stage 3 works from the script and index alone.

See *What the models must return* above for the exact formats.

## Freeing VRAM before rendering

If your LLM and ComfyUI share a card, set **LLM unload URL** in Settings (your llama-swap
host, e.g. `http://localhost:8080`). Before every render vidpipe POSTs to the unload path,
falls back to GET if that's not allowed, then polls the running-check path until it reports
nothing loaded — up to 30 seconds — before queueing the first graph.

Both paths are configurable and default to llama-swap's: `/api/models/unload` and
`/running`. Settings save as you change them, and the activity log prints the full URL
that was called — if it doesn't match what you'd `curl`, that line will show it. The running check reads `running`, `data` or `models` from the reply and
ignores entries whose state is `stopped`, `unloaded` or `ready`. If the path 404s, it
falls back to a 2 second pause.

Leave the URL empty to disable. If the endpoint is unreachable the render continues anyway
and the activity log says the unload failed, since stopping the run would be the worse
outcome. **Unload now** on the Settings tab tests it without rendering.

Nothing reloads the LLM afterwards; llama-swap does that on the next request, which is the
next time you generate a script or prompts.

## Joining the clips

With *Join clips when finished* on, a completed render concatenates the segments in order
into `data/renders/pN_full.mp4`, shown with a player and a download button under the chain.
It tries stream copy first and re-encodes only if that fails, so normally it's near-instant
and lossless.

**Crossfade between clips** (Build tab, next to the join toggle) overlaps each pair of clips
instead of cutting, fading picture and sound together. Off by default, with the length
preset to 0.5s; the seconds field greys out while it's off. Because the clips overlap,
the result is *shorter*: four 15s clips with a 0.5s fade run 58.5s, not 60s. A crossfade
always re-encodes, so it's slower than a straight join, and a fade longer than the shortest
clip is refused with an explanation. Leave it at 0 for hard cuts. Needs `ffprobe` on PATH
alongside `ffmpeg` — they ship together.

**Join clips now** stitches whatever exists at any time — useful after re-rendering one
segment. If some segments have a prompt but no render, it joins the rest and warns you which
were skipped.

## Choosing how many references

The References tab has a **Slots in use** stepper per media type. It sets how many of the
workflow's loaders are switched on for a run: the ones above the count stay bypassed and
are removed from the graph entirely, so they cost nothing and can't fail validation.

While continuation is on, **one image slot is always held for the carried frame** — it
shows as *held* and can't be assigned. Raising the count moves it along: two static
references puts the frame in slot 2, three puts it in slot 3. With six slots in your
workflow that means up to five static references plus the frame.

Turning continuation off releases that slot for a sixth static reference. Lowering the
count drops the assignments in the slots you removed.

## Continuation

With *Carry the last frame into the next segment* on:

1. Segment N renders, the clip is downloaded to `data/renders/`.
2. Its final frame is extracted and uploaded to ComfyUI's input folder.
3. Segment N+1 gets that frame as **an additional reference, after your static ones**.

So with three references assigned, clip 1 uses those three and clip 2 uses the same three
plus the carried frame as reference four. Nothing is displaced and there is nothing to
configure — segment 1 simply has nothing to carry.

If the references plus the frame exceed the workflow's slot count, the carried frame is
kept and the last static reference is dropped, with a warning in the activity log.

The line between segment cards shows whether a frame is actually being carried.

## What gets patched, and how it's found

Nodes are located by walking links out from the `MiniMaxH3ReferenceToVideo` node, not by
ID, so re-saving in ComfyUI won't break anything:

| Control | Found by |
| --- | --- |
| Prompt | whatever feeds the H3 node's `prompt` input (a `TextBox1`, a string primitive, or the literal on the node) |
| Reference images | each `ref_image_N` input → its `LoadImage` |
| Reference audio | each `ref_audio_N` input → its audio loader |
| Duration | the float primitive upstream of `length` |
| Seed / steps / resolution / LoRAs | `RandomNoise`, `BasicScheduler`, `ResolutionSelector`, Power Lora Loader |
| Output | the first node that saves a clip: `SaveVideo`, `VHS_VideoCombine`, `SaveWEBM`, `SaveAnimatedWEBP`/`PNG`, `SaveImage` — or, failing those, anything with a `filename_prefix` input |

Swapping `SaveVideo` for `VHS_VideoCombine` needs no configuration. `save_output` is forced
on where the node has it, since a preview-only run leaves nothing to download. **Output
frame rate** on the Build tab overrides the save node's frame rate; leave it at 0 to keep
whatever the workflow has.

Even with no output node found, rendering still works — the finished file is located from
ComfyUI's history for that `prompt_id`, not by searching for a filename.

Reference slots you leave empty are removed from the graph along with their loaders, so
an unused `LoadImage` pointing at `example.png` can't fail the run.

**Bypassed reference loaders count as spare slots.** See *Choosing how many references*
below for the controls. A muted or bypassed `LoadImage` or
audio loader wired to the H3 node is kept and marked *spare* in the slot picker; assigning
something to it switches it back on for that run, and leaving it empty removes it as usual.
Your workflow reports six image slots and three audio slots this way — four and two live,
plus two and one spare — which is what gives the carried frame somewhere to go when all
your static references are already assigned.

Only bypassed *leaf* nodes are revived. A bypassed node mid-chain is still dropped, since
reviving it would mean reproducing the editor's link-rerouting, which isn't worth guessing at.

Editor-format workflows (the kind you get from Save) are converted on upload using your
running ComfyUI's `/object_info`, so **ComfyUI must be up when you load one**. An API
export (Workflow → Export (API)) needs no conversion and is the more reliable input.

## Notes

- One job at a time. **Stop** cancels the running task outright and sends `/interrupt`
  to ComfyUI — it works even when the job is parked inside a slow model call, not only
  between steps.
- Starting a second job while one is running returns 409 and names the job holding the
  slot and how long it's been there. The UI offers to stop it and retry.
- The header shows the running job and its age, polled every few seconds — so a job
  that started before you opened the tab (or before a page refresh) still shows up with
  a working **Stop**. Reloading the page does not restart the server or clear a job.
- A job older than 15 minutes is presumed wedged and gets displaced by the next request,
  so nothing can lock the app until the server is restarted.
- **LLM timeout** (Settings, default 300s) caps how long one model reply may take. A
  model that never answers fails with a clear message instead of holding the slot.
- Seed `-1` means a fresh seed per segment. Pin it to a number to compare prompt edits.
- Segment prompts are stored per project, so you can rewrite one segment and re-render
  only that one — though with continuation on, later segments were built from the old
  frame, so re-render forward from wherever you changed something.
- The reference audio loader's trim duration is set to the segment duration on each run.
- Which file belongs to which segment isn't guessed: each queue returns a `prompt_id`, and
  `/history/{prompt_id}` names the files that run produced. The last frame is pulled from
  that file. The per-segment `filename_prefix` is only for readable names on disk.

## Tests

```bash
./run_tests.sh                    # everything

python3 tests/test_workflow.py    # conversion, node detection, spare slots, patching
python3 tests/test_outputs.py     # output-node detection across SaveVideo and VHS
python3 tests/test_crossfade.py   # joining, timing, audio, blended transition frames
python3 tests/test_parsing.py     # beat formats and prompt cleanup
python3 tests/test_requests.py    # what each stage sends, and preview matching it
python3 tests/test_story.py       # outline, chapter-by-chapter writing, assembly
python3 tests/test_slots.py       # reference counts and the reserved frame slot
python3 tests/test_swap.py        # LLM unload against a llama-swap stub
python3 tests/test_openwebui.py   # endpoint discovery across Open WebUI layouts
python3 tests/test_runner.py      # one-job-at-a-time, and Stop freeing a stuck job
python3 tests/test_routes.py      # every run route over real HTTP
python3 tests/test_pipeline.py    # full run against stub Open WebUI + ComfyUI
python3 tests/test_api.py         # HTTP surface
```

`tests/test_routes.py` runs a real uvicorn process and hits every run route over the
wire, because routes that schedule background work must be `async def` — a sync one is
handed to a worker thread where `asyncio.create_task` fails. TestClient can hide that.

`tests/test_pipeline.py` stands up fake servers and checks the real things that matter:
that segment 2 receives segment 1's prompt as context, that the carried frame lands in
slot 0 and pushes the static reference to slot 1, and that each segment gets its own seed.
It also stands up a stub llama-swap whose `/prompt` handler fails the test if ComfyUI is
queued while a model is still loaded, checks each segment prompt carried its own beat,
confirms the carried frame lands after the static references rather than displacing one,
and ffprobes the joined file to confirm it's the length of all three clips.
