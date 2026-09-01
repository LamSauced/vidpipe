const $ = (id) => document.getElementById(id);
const state = { project: null, projects: [], assets: [], workflow: null, models: [], busy: false };

/* ---------- helpers ---------- */
async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) throw new Error((data && data.detail) || res.statusText);
  return data;
}
const json = (path, method, body) =>
  api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

let toastTimer;
function toast(message, bad = false) {
  const el = $("toast");
  el.textContent = message;
  el.className = "toast" + (bad ? " bad" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), bad ? 8000 : 3500);
}
function logLine(text, cls = "") {
  const li = document.createElement("li");
  li.className = cls;
  li.textContent = new Date().toLocaleTimeString([], { hour12: false }) + "  " + text;
  $("log").prepend(li);
  while ($("log").children.length > 120) $("log").lastChild.remove();
}
const debounce = (fn, ms = 500) => {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
};

/* ---------- tabs ---------- */
$("tabs").addEventListener("click", (e) => {
  const tab = e.target.closest(".tab");
  if (!tab) return;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("is-active", t === tab));
  document.querySelectorAll(".view").forEach((v) =>
    v.classList.toggle("is-active", v.id === "view-" + tab.dataset.view));
});

/* ---------- projects ---------- */
async function loadProjects() {
  state.projects = (await api("/api/projects")).projects;
  const list = $("projectList");
  list.innerHTML = "";
  state.projects.forEach((p) => {
    const li = document.createElement("li");
    li.className = state.project && state.project.id === p.id ? "is-active" : "";
    li.innerHTML = `<span class="pid">${String(p.id).padStart(2, "0")}</span><span class="pname"></span>`;
    li.querySelector(".pname").textContent = p.name;
    li.onclick = () => openProject(p.id);
    list.appendChild(li);
  });
}

async function openProject(pid) {
  state.project = await api(`/api/projects/${pid}`);
  $("buildEmpty").hidden = true;
  $("buildBody").hidden = false;
  renderProject();
  await loadProjects();
}

/* Full render only on open — otherwise a debounced save would overwrite the
   field under the cursor mid-sentence. */
function renderFinal() {
  const p = state.project;
  const has = !!p.final_video;
  const rendered = (p.segments || []).filter((x) => x.status === "done").length;
  $("finalStage").hidden = !has && rendered < 2;
  $("joinNow").disabled = rendered < 2;
  if (has) {
    const url = `/api/projects/${p.id}/final?t=${p.updated_at}`;
    $("finalVideo").src = url;
    $("downloadFinal").href = url;
    $("downloadFinal").hidden = false;
  } else {
    $("finalVideo").removeAttribute("src");
    $("downloadFinal").hidden = true;
  }
}

function renderChapters() {
  const p = state.project, s = p.settings, host = $("chapterChain");
  $("storyStage").hidden = !s.story_mode;
  $("chapterCountWrap").style.display = s.story_mode ? "" : "none";
  $("genScript").style.display = s.story_mode ? "none" : "";
  if (!s.story_mode) return;

  const active = document.activeElement;
  const keep = active && host.contains(active)
    ? { id: active.closest(".chapter")?.id, brief: active.classList.contains("chapter-brief"),
        start: active.selectionStart, end: active.selectionEnd }
    : null;

  host.innerHTML = "";
  const chapters = p.chapters || [];
  if (!chapters.length) {
    host.innerHTML = `<p class="hint">Plan the outline to create the chapters.</p>`;
    return;
  }
  chapters.forEach((c, i) => host.appendChild(chapterCard(c, i)));

  if (keep && keep.id) {
    const el = document.querySelector(
      `#${keep.id} ${keep.brief ? ".chapter-brief" : ".chapter-text"}`);
    if (el) { el.focus(); el.setSelectionRange(keep.start, keep.end); }
  }
}

function chapterCard(c, i) {
  const words = (c.text || "").trim() ? (c.text.trim().split(/\s+/).length) : 0;
  const el = document.createElement("div");
  el.className = "chapter";
  el.id = `chapter-${i}`;
  el.innerHTML = `
    <div class="seg-head">
      <span class="seg-index">CH ${String(i + 1).padStart(2, "0")}</span>
      <span class="wordcount">${words ? words + " words" : ""}</span>
      <span class="seg-state ${c.status}">${c.status}</span>
    </div>
    <div class="seg-body">
      <textarea class="chapter-brief" rows="2" placeholder="What this chapter covers."></textarea>
      <textarea class="chapter-text" placeholder="Not written yet."></textarea>
      <div class="seg-error"></div>
      <div class="seg-actions">
        <button class="btn btn-small" data-act="write">Rewrite chapter</button>
      </div>
    </div>`;
  const brief = el.querySelector(".chapter-brief");
  const text = el.querySelector(".chapter-text");
  brief.value = c.brief || "";
  text.value = c.text || "";
  el.querySelector(".seg-error").textContent = c.error || "";

  const save = (field, box) => debounce(() =>
    json(`/api/projects/${state.project.id}/chapters/${i}`, "PATCH", { [field]: box.value })
      .then(() => { c[field] = box.value; if (field === "text") refreshProject(); }), 800);
  brief.addEventListener("input", save("brief", brief));
  text.addEventListener("input", save("text", text));
  el.querySelector('[data-act="write"]').onclick = () =>
    start_(`/api/projects/${state.project.id}/chapters?index=${i}`);
  return el;
}

function renderDerived() {
  const s = state.project.settings;
  $("totalHint").textContent = `${(s.segment_count * s.duration).toFixed(0)}s total`;
  renderChain();
  renderChapters();
  renderSlots();
  renderFinal();
}

function renderProject() {
  const p = state.project, s = p.settings;
  $("projectName").value = p.name;
  $("idea").value = p.idea || "";
  $("script").value = p.script || "";
  $("segCount").value = s.segment_count;
  $("segDuration").value = s.duration;
  $("continuation").checked = !!s.continuation;
  $("concat").checked = !!s.concat;
  $("crossfadeOn").checked = !!s.crossfade_on;
  $("crossfade").value = s.crossfade ?? 0.5;
  $("crossfade").disabled = !s.crossfade_on;
  $("crossfadeWrap").style.opacity = s.crossfade_on ? "1" : ".4";
  $("storyMode").checked = !!s.story_mode;
  $("chapterCount").value = s.chapter_count ?? 4;
  $("outline").value = p.outline || "";
  $("planBeats").checked = !!s.plan_beats;
  $("sendScript").checked = s.send_full_script !== false;
  $("adoptCount").checked = s.adopt_segment_count !== false;
  $("aspect").value = s.aspect_ratio;
  $("megapixels").value = s.megapixels;
  $("steps").value = s.steps;
  $("seed").value = s.seed;
  $("prefix").value = s.filename_prefix;
  $("outputFps").value = s.output_fps ?? 0;
  renderDerived();
}

const saveProject = debounce(async (patch) => {
  if (!state.project) return;
  state.project = await json(`/api/projects/${state.project.id}`, "PATCH", patch);
  renderDerived();
  loadProjects();
});

function bindProjectField(id, key, transform = (v) => v) {
  $(id).addEventListener("input", () => saveProject({ [key]: transform($(id).value) }));
}
function bindSetting(id, key, transform = (v) => v) {
  const el = $(id);
  el.addEventListener("change", () =>
    saveProject({ settings: { [key]: transform(el.type === "checkbox" ? el.checked : el.value) } }));
}

bindProjectField("projectName", "name");
bindProjectField("idea", "idea");
bindProjectField("script", "script");
bindProjectField("outline", "outline");
bindSetting("segCount", "segment_count", Number);
bindSetting("segDuration", "duration", Number);
bindSetting("continuation", "continuation", Boolean);
$("continuation").addEventListener("change", () => setTimeout(renderSlots, 600));
bindSetting("concat", "concat", Boolean);
bindSetting("crossfade", "crossfade", Number);
bindSetting("crossfadeOn", "crossfade_on", Boolean);
$("crossfadeOn").addEventListener("change", () => {
  const on = $("crossfadeOn").checked;
  $("crossfade").disabled = !on;
  $("crossfadeWrap").style.opacity = on ? "1" : ".4";
});
bindSetting("storyMode", "story_mode", Boolean);
bindSetting("chapterCount", "chapter_count", Number);
bindSetting("planBeats", "plan_beats", Boolean);
bindSetting("sendScript", "send_full_script", Boolean);
bindSetting("adoptCount", "adopt_segment_count", Boolean);
bindSetting("aspect", "aspect_ratio");
bindSetting("megapixels", "megapixels", Number);
bindSetting("steps", "steps", Number);
bindSetting("seed", "seed", Number);
bindSetting("prefix", "filename_prefix");
bindSetting("outputFps", "output_fps", Number);

async function newProject() {
  const name = prompt("Project name", "Untitled");
  if (name === null) return;
  const p = await json("/api/projects", "POST", { name: name || "Untitled" });
  await openProject(p.id);
}
$("newProject").onclick = newProject;
$("newProjectEmpty").onclick = newProject;

/* ---------- the segment chain ---------- */
function renderChain() {
  const p = state.project, s = p.settings, chain = $("chain");
  const active = document.activeElement;
  const keep = active && chain.contains(active)
    ? { seg: active.closest(".seg")?.id, beat: active.classList.contains("seg-beat"),
        start: active.selectionStart, end: active.selectionEnd }
    : null;
  chain.innerHTML = "";
  const segments = p.segments || [];
  if (!segments.length) {
    chain.innerHTML = `<p class="hint">Set a segment count, then build the prompts.</p>`;
    return;
  }
  segments.forEach((seg, i) => {
    if (i > 0) {
      const link = document.createElement("div");
      const carrying = s.continuation && segments[i - 1].last_frame;
      link.className = "link" + (carrying ? " is-carry" : "");
      link.dataset.label = s.continuation
        ? (carrying ? "last frame carried forward" : "waiting on the previous render")
        : "independent";
      chain.appendChild(link);
    }
    chain.appendChild(segmentCard(seg, i, s));
  });

  if (keep && keep.seg) {
    const restored = document.querySelector(
      `#${keep.seg} ${keep.beat ? ".seg-beat" : ".seg-body textarea:not(.seg-beat)"}`);
    if (restored) {
      restored.focus();
      restored.setSelectionRange(keep.start, keep.end);
    }
  }
}

function segmentCard(seg, i, s) {
  const start = i * s.duration, end = start + s.duration;
  const el = document.createElement("div");
  el.className = "seg";
  el.id = `seg-${i}`;
  el.innerHTML = `
    <div class="seg-head">
      <span class="seg-index">SEG ${String(i + 1).padStart(2, "0")}</span>
      <span class="seg-time">${start.toFixed(0)}s → ${end.toFixed(0)}s</span>
      <span class="seg-state ${seg.status}">${seg.status}</span>
    </div>
    <div class="bar"><span></span></div>
    <div class="seg-body">
      <textarea class="seg-beat" rows="2" placeholder="Segment — the slice of script this clip covers.">${seg.beat || ""}</textarea>
      <textarea placeholder="No prompt yet.">${seg.prompt || ""}</textarea>
      <div class="seg-error"></div>
      <div class="seg-actions">
        <button class="btn btn-small" data-act="prompt">Rewrite prompt</button>
        <button class="btn btn-small" data-act="render">Render this</button>
      </div>
    </div>`;

  const beatBox = el.querySelector(".seg-beat");
  beatBox.addEventListener("input", debounce(() =>
    json(`/api/projects/${state.project.id}/segments/${i}/beat`, "PATCH", { beat: beatBox.value })
      .then((updated) => { seg.beat = updated.beat; }), 700));

  const textarea = el.querySelector(".seg-body textarea:not(.seg-beat)");
  textarea.addEventListener("input", debounce(() =>
    json(`/api/projects/${state.project.id}/segments/${i}`, "PATCH", { prompt: textarea.value })
      .then((updated) => { seg.prompt = updated.prompt; seg.status = updated.status; }), 700));

  el.querySelector(".seg-error").textContent = seg.error || "";
  el.querySelector('[data-act="prompt"]').onclick = () =>
    start_(`/api/projects/${state.project.id}/prompts?index=${i}`);
  el.querySelector('[data-act="render"]').onclick = () =>
    start_(`/api/projects/${state.project.id}/render`, { indices: [i] });

  if (seg.status === "done" && seg.video) {
    const video = document.createElement("video");
    video.controls = true;
    video.src = `/api/projects/${state.project.id}/segments/${i}/video?t=${Date.now()}`;
    el.querySelector(".seg-body").appendChild(video);
  }
  return el;
}

/* ---------- runs ---------- */
async function start_(path, body, retried) {
  try {
    const res = body ? await json(path, "POST", body) : await api(path, { method: "POST" });
    logLine("▶ " + res.started);
  } catch (e) {
    // 409 means an earlier job still holds the slot. Offer to end it rather
    // than leaving the button apparently dead.
    if (!retried && /already running/i.test(e.message)) {
      if (confirm(`${e.message}\n\nStop it and start this instead?`)) {
        await api("/api/cancel", { method: "POST" });
        await new Promise((r) => setTimeout(r, 600));
        return start_(path, body, true);
      }
      logLine(e.message, "bad");
      return;
    }
    toast(e.message, true);
    logLine(e.message, "bad");
  }
}
const pid = () => state.project.id;
$("genScript").onclick = () => start_(`/api/projects/${pid()}/script`);
$("genOutline").onclick = () => start_(`/api/projects/${pid()}/outline`);
$("genChapters").onclick = () => start_(`/api/projects/${pid()}/chapters`);
$("genPrompts").onclick = () => start_(`/api/projects/${pid()}/prompts`);
$("genBeats").onclick = () => start_(`/api/projects/${pid()}/beats`);
$("joinNow").onclick = () => start_(`/api/projects/${pid()}/join`);
$("unloadNow").onclick = async () => {
  // Save first — otherwise a URL typed but not saved isn't what the server sees.
  await saveSettings(true);
  try { toast((await api("/api/upstream/unload", { method: "POST" })).detail); }
  catch (e) { toast(e.message, true); }
};
$("renderAll").onclick = () => start_(`/api/projects/${pid()}/render`, {});
$("runAll").onclick = () => start_(`/api/projects/${pid()}/run`);
$("cancelBtn").onclick = async () => {
  await api("/api/cancel", { method: "POST" });
  logLine("Stop requested.");
  setTimeout(pollStatus, 500);
};

function setBusy(busy, label, elapsed) {
  state.busy = busy;
  $("statusDot").className = "dot" + (busy ? " is-live" : "");
  let text = busy ? label || "Working" : "Idle";
  if (busy && elapsed > 5) text += ` · ${Math.round(elapsed)}s`;
  $("statusText").textContent = text;
  $("cancelBtn").hidden = !busy;
}

// The websocket only reports changes. If a job was already running before this
// tab connected — a page refresh mid-job, say — polling is what surfaces it.
async function pollStatus() {
  try {
    const st = await api("/api/status");
    if (st.busy !== state.busy || st.busy) setBusy(st.busy, st.label, st.elapsed);
  } catch { /* server down; the socket's reconnect will report it */ }
}
setInterval(pollStatus, 4000);

/* ---------- live events ---------- */
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (e) => handleEvent(JSON.parse(e.data));
  ws.onclose = () => { setBusy(false); setTimeout(connect, 2000); };
}

function handleEvent(ev) {
  if (ev.type === "hello") return setBusy(ev.busy, ev.label, ev.elapsed);
  if (ev.type === "stuck") return setBusy(true, ev.label);
  if (ev.type === "idle") { setBusy(false); refreshProject(); return; }
  if (ev.type === "failed") { setBusy(false); toast(ev.message, true); logLine(ev.message, "bad"); return; }
  if (ev.type === "cancelled") { setBusy(false); logLine("Stopped."); return; }
  if (ev.type === "warning") { logLine(ev.message, "bad"); return; }

  if (!state.project || ev.project_id !== state.project.id) return;

  if (ev.type === "stage" && ev.state === "running") setBusy(true, "Writing the script");
  if (ev.type === "script") {
    $("script").value = ev.text;
    state.project.script = ev.text;
    if (!state.project.settings.story_mode) logLine("Script written.", "good");
  }
  if (ev.type === "render_complete") logLine("All segments rendered.", "good");
  if (ev.type === "info") logLine(ev.message);
  if (ev.type === "joined") {
    logLine(`Joined ${ev.clips} clips${ev.crossfade ? ` with a ${ev.crossfade}s crossfade` : ""}.`, "good");
    refreshProject();
  }
  if (ev.type === "outline") {
    logLine(`Story planned in ${ev.chapters} chapters.`, "good");
    refreshProject();
  }
  if (ev.type === "assembled") {
    logLine(`Story assembled: ${ev.chapters} chapters, ${ev.characters} characters.`, "good");
  }
  if (ev.type === "chapter") {
    const c = (state.project.chapters || [])[ev.index];
    if (c) Object.assign(c, { status: ev.status, text: ev.text ?? c.text, error: ev.error || "" });
    if (ev.status === "writing") setBusy(true, `Writing chapter ${ev.index + 1}`);
    if (ev.status === "done") logLine(`Chapter ${ev.index + 1} written.`, "good");
    if (ev.status === "error") logLine(`Chapter ${ev.index + 1}: ${ev.error}`, "bad");
    refreshProject();
  }
  if (ev.type === "stage" && ev.stage === "outline" && ev.state === "running")
    setBusy(true, "Planning the story");
  if (ev.type === "beats") {
    logLine(`Script split into ${ev.beats.length} segments.`, "good");
    refreshProject();
  }
  if (ev.type === "stage" && ev.stage === "beats" && ev.state === "running")
    setBusy(true, "Splitting the script into segments");

  if (ev.type === "progress") {
    const bar = document.querySelector(`#seg-${ev.index} .bar span`);
    if (bar) bar.style.width = `${(ev.value / Math.max(1, ev.max)) * 100}%`;
    setBusy(true, `Segment ${ev.index + 1} · ${ev.value}/${ev.max}`);
  }

  if (ev.type === "segment") {
    const seg = (state.project.segments || [])[ev.index];
    if (!seg) return;
    Object.assign(seg, {
      status: ev.status,
      prompt: ev.prompt !== undefined ? ev.prompt : seg.prompt,
      error: ev.error || "",
    });
    if (ev.status === "prompting") setBusy(true, `Prompting segment ${ev.index + 1}`);
    if (ev.status === "rendering") setBusy(true, `Rendering segment ${ev.index + 1}`);
    if (ev.status === "error") logLine(`Segment ${ev.index + 1}: ${ev.error}`, "bad");
    if (ev.status === "done") logLine(`Segment ${ev.index + 1} rendered.`, "good");
    refreshProject();
  }
  if (ev.type === "frame") logLine(`Segment ${ev.index + 1}: last frame captured.`);
}

const refreshProject = debounce(async () => {
  if (!state.project) return;
  state.project = await api(`/api/projects/${state.project.id}`);
  renderDerived();
}, 250);

/* ---------- assets ---------- */
async function loadAssets() {
  state.assets = (await api("/api/assets")).assets;
  renderAssets("image", $("imageAssets"));
  renderAssets("audio", $("audioAssets"));
  renderSlots();
}

function renderAssets(kind, host) {
  host.innerHTML = "";
  const items = state.assets.filter((a) => a.kind === kind);
  if (!items.length) {
    host.innerHTML = `<p class="hint">Nothing added yet.</p>`;
    return;
  }
  items.forEach((a) => {
    const card = document.createElement("div");
    card.className = "asset";
    const media = kind === "image"
      ? `<img src="/api/assets/${a.id}/file" alt="">`
      : `<audio controls src="/api/assets/${a.id}/file"></audio>`;
    const describe = kind === "image"
      ? `<textarea class="describe" rows="2" placeholder="Describe this reference — sent to the prompt model."></textarea>`
      : "";
    card.innerHTML = `${media}<div class="meta"><span></span><button class="x" title="Remove">×</button></div>${describe}`;
    card.querySelector("span").textContent = a.label;
    card.querySelector(".x").onclick = async () => {
      await api(`/api/assets/${a.id}`, { method: "DELETE" });
      loadAssets();
    };
    const box = card.querySelector(".describe");
    if (box) {
      box.value = a.description || "";
      box.addEventListener("input", debounce(() => {
        json(`/api/assets/${a.id}`, "PATCH", { description: box.value })
          .then(() => { a.description = box.value; });
      }, 700));
    }
    host.appendChild(card);
  });
}

function slotOptions(kind, selected) {
  const opts = ['<option value="">— empty —</option>'];
  state.assets.filter((a) => a.kind === kind).forEach((a) => {
    opts.push(`<option value="${a.id}" ${String(a.id) === String(selected) ? "selected" : ""}>${a.label}</option>`);
  });
  return opts.join("");
}

function slotBudget() {
  const cap = state.capacity || {};
  const s = state.project ? state.project.settings : {};
  const imageSlots = cap.image_slots || 0;
  const audioSlots = cap.audio_slots || 0;
  const carries = !!s.continuation;
  // One image slot is held back for the carried frame while continuation is on.
  const maxImages = Math.max(0, imageSlots - (carries && imageSlots ? 1 : 0));
  return {
    imageSlots, audioSlots, maxImages, carries,
    maxAudios: audioSlots,
    images: Math.min(s.ref_image_count ?? 0, maxImages),
    audios: Math.min(s.ref_audio_count ?? 0, audioSlots),
  };
}

function renderSlots() {
  const b = slotBudget();
  const s = state.project ? state.project.settings : { ref_images: [], ref_audios: [] };

  $("imgCount").textContent = b.images;
  $("audCount").textContent = b.audios;
  $("imgMinus").disabled = b.images <= 0;
  $("imgPlus").disabled = b.images >= b.maxImages;
  $("audMinus").disabled = b.audios <= 0;
  $("audPlus").disabled = b.audios >= b.maxAudios;
  $("imgBudget").textContent = b.imageSlots
    ? `${b.imageSlots} slots in the workflow` +
      (b.carries ? ", 1 held for the carried frame" : "")
    : "";
  $("audBudget").textContent = b.audioSlots ? `${b.audioSlots} slots in the workflow` : "";

  const build = (host, used, total, kind, key, reservedIndex) => {
    host.innerHTML = "";
    if (!total) {
      host.innerHTML = `<p class="hint">Load a workflow to see its ${kind} slots.</p>`;
      return;
    }
    for (let i = 0; i < total; i++) {
      const reserved = i === reservedIndex;
      const off = !reserved && i >= used;
      const wrap = document.createElement("div");
      wrap.className = "slot" + (reserved ? " is-reserved" : "") + (off ? " off" : "");
      // Shown 1-based to match "Reference 1" in the prompt; the workflow's own
      // input name (ref_image_0) is kept in the tooltip so both are findable.
      const node = kind === "image" ? `ref_image_${i}` : `ref_audio_${i}`;
      const n = `<span class="n" title="${node}">${i + 1}</span>`;
      if (reserved) {
        wrap.innerHTML = n +
          `<select disabled><option>last frame of the previous clip</option></select>` +
          `<span class="reserved">held</span>`;
      } else if (off) {
        wrap.innerHTML = n + `<select disabled><option>bypassed</option></select>`;
      } else {
        wrap.innerHTML = n + `<select>${slotOptions(kind, (s[key] || [])[i])}</select>`;
        wrap.querySelector("select").onchange = () => {
          const ids = [...host.querySelectorAll("select:not([disabled])")]
            .map((sel) => (sel.value ? Number(sel.value) : null));
          saveProject({ settings: { [key]: ids.filter((v) => v !== null) } });
        };
      }
      host.appendChild(wrap);
    }
  };

  // With continuation on, the frame lands in the slot right after the static ones.
  build($("imageSlots"), b.images, b.imageSlots, "image", "ref_images",
        b.carries ? b.images : -1);
  build($("audioSlots"), b.audios, b.audioSlots, "audio", "ref_audios", -1);
}

function bumpSlots(key, delta, max) {
  if (!state.project) return;
  const current = state.project.settings[key] ?? 0;
  const next = Math.max(0, Math.min(max, current + delta));
  if (next === current) return;
  const listKey = key === "ref_image_count" ? "ref_images" : "ref_audios";
  const patch = { [key]: next };
  // Shedding a slot drops whatever was assigned to it.
  const assigned = state.project.settings[listKey] || [];
  if (next < assigned.length) patch[listKey] = assigned.slice(0, next);
  saveProject({ settings: patch });
}

$("imgPlus").onclick = () => bumpSlots("ref_image_count", 1, slotBudget().maxImages);
$("imgMinus").onclick = () => bumpSlots("ref_image_count", -1, slotBudget().maxImages);
$("audPlus").onclick = () => bumpSlots("ref_audio_count", 1, slotBudget().maxAudios);
$("audMinus").onclick = () => bumpSlots("ref_audio_count", -1, slotBudget().maxAudios);

async function upload(files, kind) {
  for (const file of files) {
    const form = new FormData();
    form.append("file", file);
    form.append("kind", kind);
    try {
      await api("/api/assets", { method: "POST", body: form });
      logLine(`Uploaded ${file.name}.`);
    } catch (e) { toast(e.message, true); }
  }
  loadAssets();
}
$("upImage").onchange = (e) => upload(e.target.files, "image");
$("upAudio").onchange = (e) => upload(e.target.files, "audio");

/* ---------- workflow ---------- */
async function loadWorkflow() {
  state.workflow = await api("/api/workflow");
  state.capacity = await api("/api/capacity");
  renderWorkflow();
  renderSlots();
}

function renderWorkflow() {
  const host = $("workflowSummary");
  if (!state.workflow || !state.workflow.loaded) {
    host.innerHTML = `<p class="hint">No workflow yet. Load the JSON you generate with.</p>`;
    return;
  }
  const s = state.workflow.summary;
  const rows = [
    ["Nodes", s.node_count],
    ["MiniMax H3", s.h3],
    ["Prompt goes to", s.prompt],
    ["Duration", s.duration],
    ["Seed", s.seed],
    ["Steps", s.steps],
    ["Resolution", s.resolution],
    ["Output", s.save],
    ["LoRA loader", s.loras],
    ["Reference image slots", s.ref_image_slots],
    ["Reference audio slots", s.ref_audio_slots],
  ];
  host.innerHTML = `<table>${rows.map(([k, v]) =>
    `<tr><td>${k}</td><td class="${v == null ? "miss" : ""}">${v == null ? "not found" : v}</td></tr>`
  ).join("")}</table>` +
    (s.warnings || []).map((w) => `<p class="warn">${w}</p>`).join("");
}

$("upWorkflow").onchange = async (e) => {
  const form = new FormData();
  form.append("file", e.target.files[0]);
  try {
    state.workflow = await api("/api/workflow", { method: "POST", body: form });
    state.capacity = await api("/api/capacity");
    renderWorkflow();
    renderSlots();
    toast(state.workflow.converted ? "Converted to API format and loaded." : "Workflow loaded.");
  } catch (err) { toast(err.message, true); }
};

/* ---------- settings ---------- */
async function loadSettings() {
  const cfg = await api("/api/settings");
  $("owUrl").value = cfg.openwebui_url;
  $("owKey").value = cfg.openwebui_key;
  $("comfyUrl").value = cfg.comfy_url;
  $("ffmpeg").value = cfg.ffmpeg;
  $("llmUnloadUrl").value = cfg.llm_unload_url || "";
  $("llmUnloadPath").value = cfg.llm_unload_path || "/api/models/unload";
  $("llmRunningPath").value = cfg.llm_running_path || "/running";
  $("scriptTemp").value = cfg.script_temperature;
  $("segmentTemp").value = cfg.segment_temperature;
  $("beatTemp").value = cfg.beat_temperature ?? 0.4;
  $("beatSystem").value = cfg.beat_system || "";
  $("scriptUser").value = cfg.script_user || "";
  $("beatUser").value = cfg.beat_user || "";
  $("segmentUser").value = cfg.segment_user || "";
  $("outlineSystem").value = cfg.outline_system || "";
  $("outlineUser").value = cfg.outline_user || "";
  $("chapterSystem").value = cfg.chapter_system || "";
  $("chapterUser").value = cfg.chapter_user || "";
  $("outlineTemp").value = cfg.outline_temperature ?? 0.6;
  $("chapterTemp").value = cfg.chapter_temperature ?? 0.9;
  $("llmTimeout").value = cfg.llm_timeout ?? 300;
  $("carriedDesc").value = cfg.carried_frame_description || "";
  $("scriptSystem").value = cfg.script_system;
  $("segmentSystem").value = cfg.segment_system;
  state.defaults = cfg.defaults;
  setTimeout(checkTokens, 0);
  state.selected = { script: cfg.script_model, segment: cfg.segment_model,
                     beat: cfg.beat_model, outline: cfg.outline_model,
                     chapter: cfg.chapter_model };
  fillModelSelects();
}

function fillModelSelects() {
  [["scriptModel", "script"], ["segmentModel", "segment"],
   ["beatModel", "beat"], ["outlineModel", "outline"],
   ["chapterModel", "chapter"]].forEach(([id, key]) => {
    const sel = $(id);
    const current = state.selected[key];
    const known = state.models.some((m) => m.id === current);
    const blank = key === "beat" ? "— same as the prompt model —"
      : (key === "outline" || key === "chapter") ? "— same as the script model —"
      : "— pick a model —";
    sel.innerHTML = [`<option value="">${blank}</option>`]
      .concat(!known && current ? [`<option value="${current}" selected>${current}</option>`] : [])
      .concat(state.models.map((m) =>
        `<option value="${m.id}" ${m.id === current ? "selected" : ""}>${m.name}</option>`)).join("");
  });
}

$("loadModels").onclick = async () => {
  try {
    state.models = (await api("/api/models")).models;
    fillModelSelects();
    toast(`${state.models.length} models available.`);
  } catch (e) { toast(e.message, true); }
};

$("testConn").onclick = async () => {
  await saveSettings(true);
  const health = await api("/api/health");
  $("connResult").innerHTML = ["openwebui", "comfy"].map((k) => {
    const r = health[k] || {};
    const label = k === "openwebui" ? "Open WebUI" : "ComfyUI";
    return `<div class="${r.ok ? "ok" : "no"}">${label}: ${r.ok ? "reachable" : "unreachable"} — ${r.detail}</div>`;
  }).join("");
};

$("fillDefaults").onclick = () => {
  $("scriptSystem").value = state.defaults.script_system;
  $("segmentSystem").value = state.defaults.segment_system;
  $("beatSystem").value = state.defaults.beat_system;
  $("scriptUser").value = state.defaults.script_user;
  $("beatUser").value = state.defaults.beat_user;
  $("segmentUser").value = state.defaults.segment_user;
  $("outlineSystem").value = state.defaults.outline_system;
  $("outlineUser").value = state.defaults.outline_user;
  $("chapterSystem").value = state.defaults.chapter_system;
  $("chapterUser").value = state.defaults.chapter_user;
  saveSettings(true);
};

// ---------- preview: exactly what leaves the machine ----------
async function loadPreview() {
  if (!state.project) return toast("Open a project first.", true);
  const stage = $("previewStage").value;
  const index = Math.max(1, Number($("previewIndex").value) || 1) - 1;
  $("previewIndexWrap").style.display =
    (stage === "segment" || stage === "chapter") ? "" : "none";
  try {
    const req = await api(
      `/api/projects/${state.project.id}/preview?stage=${stage}&index=${index}`);
    $("previewMeta").textContent =
      `POST ${req.url} · model ${req.model || "(none selected)"} · ` +
      `temperature ${req.temperature} · timeout ${req.timeout}s`;
    $("previewBody").innerHTML = req.messages.map((m) =>
      `<span class="role">${m.role}</span>${escapeHtml(m.content)}`).join("");
  } catch (e) {
    $("previewMeta").textContent = "";
    $("previewBody").textContent = e.message;
  }
}

function escapeHtml(t) {
  return String(t).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

$("showPreview").onclick = async () => {
  await saveSettings(true);
  $("previewModal").hidden = false;
  loadPreview();
};
$("previewClose").onclick = () => { $("previewModal").hidden = true; };
$("previewStage").onchange = loadPreview;
$("previewIndex").onchange = loadPreview;
$("previewModal").addEventListener("click", (e) => {
  if (e.target === $("previewModal")) $("previewModal").hidden = true;
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("previewModal").hidden = true;
});

//: Boxes whose contents get placeholder substitution, for the warning below.
const PROMPT_BOXES = [
  ["scriptSystem", "1 · Script — system"], ["scriptUser", "1 · Script — user"],
  ["outlineSystem", "1a · Outline — system"], ["outlineUser", "1a · Outline — user"],
  ["chapterSystem", "1b · Chapter — system"], ["chapterUser", "1b · Chapter — user"],
  ["beatSystem", "2 · Segmenter — system"], ["beatUser", "2 · Segmenter — user"],
  ["segmentSystem", "3 · Prompt — system"], ["segmentUser", "3 · Prompt — user"],
];

// Short names need braces, so "into x segments" silently stays "x". Rather than
// let that reach the model, point it out where it is written.
const BARE_SUSPECTS = /(?:^|[^{\w])(x|n|y|segments|clips|duration|seconds|chapters)(?=[^}\w]|$)/gi;
const NEAR_PLACEHOLDER = /\b(into|exactly|of|write|give me|produce|return|split \w+ into)\s+(x|n|y)\b/i;

function checkTokens() {
  const found = [];
  for (const [id, label] of PROMPT_BOXES) {
    const box = $(id);
    if (!box) continue;
    const text = box.value || "";
    if (!NEAR_PLACEHOLDER.test(text)) continue;
    const m = text.match(NEAR_PLACEHOLDER);
    found.push(`${label}: "…${m[0]}…" — write {${m[2]}}`);
  }
  const warn = $("tokenWarn");
  warn.hidden = !found.length;
  if (found.length) {
    warn.innerHTML = "Looks like a placeholder without braces, so it will be sent as-is:<br>"
      + found.map((f) => `<b>${escapeHtml(f)}</b>`).join("<br>");
  }
}

async function saveSettings(quiet) {
  const patch = {
    openwebui_url: $("owUrl").value.trim(),
    comfy_url: $("comfyUrl").value.trim(),
    ffmpeg: $("ffmpeg").value.trim() || "ffmpeg",
    llm_unload_url: $("llmUnloadUrl").value.trim(),
    llm_unload_path: $("llmUnloadPath").value.trim() || "/api/models/unload",
    llm_running_path: $("llmRunningPath").value.trim() || "/running",
    script_model: $("scriptModel").value,
    segment_model: $("segmentModel").value,
    beat_model: $("beatModel").value,
    script_temperature: Number($("scriptTemp").value),
    segment_temperature: Number($("segmentTemp").value),
    llm_timeout: Number($("llmTimeout").value) || 300,
    carried_frame_description: $("carriedDesc").value,
    beat_temperature: Number($("beatTemp").value),
    beat_system: $("beatSystem").value,
    script_user: $("scriptUser").value,
    beat_user: $("beatUser").value,
    segment_user: $("segmentUser").value,
    outline_system: $("outlineSystem").value,
    outline_user: $("outlineUser").value,
    chapter_system: $("chapterSystem").value,
    chapter_user: $("chapterUser").value,
    outline_model: $("outlineModel").value,
    chapter_model: $("chapterModel").value,
    outline_temperature: Number($("outlineTemp").value),
    chapter_temperature: Number($("chapterTemp").value),
    script_system: $("scriptSystem").value,
    segment_system: $("segmentSystem").value,
  };
  const key = $("owKey").value;
  if (key && key !== "••••") patch.openwebui_key = key;
  await json("/api/settings", "POST", patch);
  checkTokens();
  if (!quiet) toast("Settings saved.");
}
$("saveSettings").onclick = () => saveSettings();

// Autosave every Settings field on change, so a typed-but-unsaved value can
// never be the reason something "doesn't work".
["carriedDesc", "owUrl", "owKey", "comfyUrl", "ffmpeg", "llmUnloadUrl", "llmRunningPath",
 "llmUnloadPath", "scriptModel", "segmentModel", "scriptTemp", "segmentTemp",
 "llmTimeout", "scriptSystem", "segmentSystem",
 "beatModel", "beatTemp", "beatSystem",
 "scriptUser", "beatUser", "segmentUser", "outlineSystem", "outlineUser",
 "chapterSystem", "chapterUser", "outlineModel", "chapterModel",
 "outlineTemp", "chapterTemp"].forEach((id) => {
  $(id).addEventListener("change", () => saveSettings(true));
});

/* ---------- boot ---------- */
(async function boot() {
  connect();
  await loadSettings();
  await loadWorkflow();
  await loadAssets();
  await loadProjects();
  if (state.projects.length) await openProject(state.projects[0].id);
  try {
    state.models = (await api("/api/models")).models;
    fillModelSelects();
  } catch { /* configure the connection first */ }
})();
