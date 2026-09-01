"""Turn the saved ComfyUI graph into something we can patch per segment.

Two jobs:
  1. ui_to_api()  - convert an editor-format workflow into API format, using the
                    live /object_info schema so widget values land on the right keys.
  2. Patcher      - find the nodes that matter (prompt, refs, audio, length, seed,
                    resolution, steps, loras, save) by walking links from the
                    MiniMax H3 node outward, then rewrite them for one segment.

Node IDs are never hard-coded, so re-saving the workflow in ComfyUI won't break it.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Iterator

H3_CLASS_HINT = "MiniMaxH3ReferenceToVideo"
LINK_TYPES_SKIP_WIDGET = {"MODEL", "CLIP", "VAE", "CONDITIONING", "LATENT", "IMAGE",
                          "MASK", "AUDIO", "NOISE", "GUIDER", "SAMPLER", "SIGMAS",
                          "VIDEO", "CONTROL_NET", "STYLE_MODEL", "CLIP_VISION"}


# --------------------------------------------------------------------------
# editor format -> API format
# --------------------------------------------------------------------------

def is_api_format(doc: dict) -> bool:
    if "nodes" in doc and "links" in doc:
        return False
    return any(isinstance(v, dict) and "class_type" in v for v in doc.values())


def _widget_input_names(spec: dict) -> list[tuple[str, dict]]:
    """Ordered (name, options) for inputs the editor renders as widgets."""
    out = []
    for section in ("required", "optional"):
        for name, definition in (spec.get("input", {}).get(section) or {}).items():
            if not isinstance(definition, (list, tuple)) or not definition:
                continue
            type_ = definition[0]
            opts = definition[1] if len(definition) > 1 and isinstance(definition[1], dict) else {}
            if isinstance(type_, list):  # combo
                out.append((name, opts))
            elif type_ in ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"):
                if opts.get("forceInput"):
                    continue
                out.append((name, opts))
    return out


def ui_to_api(ui: dict, object_info: dict | None = None) -> dict:
    """Best-effort conversion. Pass object_info from a running ComfyUI for accuracy."""
    object_info = object_info or {}
    links: dict[int, tuple[str, int]] = {}
    for link in ui.get("links", []):
        if isinstance(link, dict):
            links[link["id"]] = (str(link["origin_id"]), link["origin_slot"])
        elif isinstance(link, (list, tuple)) and len(link) >= 5:
            links[link[0]] = (str(link[1]), link[2])

    # Muted (2) / bypassed (4) nodes are invisible to the backend. We keep the
    # ones that are leaves — a disabled LoadImage is a spare reference slot, and
    # dropping it would throw away capacity the workflow author set up on purpose.
    # Disabled nodes mid-chain are still dropped: reviving those would need the
    # bypass rerouting the editor does, which is not worth guessing at.
    live: dict[str, dict] = {}
    disabled: set[str] = set()
    for node in ui.get("nodes", []):
        nid, mode = str(node["id"]), node.get("mode", 0)
        if mode in (0, None):
            live[nid] = node
        elif not any(slot.get("link") is not None for slot in (node.get("inputs") or [])):
            live[nid] = node
            disabled.add(nid)

    api: dict[str, dict] = {}
    for nid, node in live.items():
        cls = node.get("type")
        if cls in ("Note", "MarkdownNote", "Reroute", "PrimitiveNode"):
            continue
        inputs: dict[str, Any] = {}

        # 1. connected inputs
        widget_backed = set()
        for slot in node.get("inputs") or []:
            name = slot.get("name")
            link_id = slot.get("link")
            if slot.get("widget"):
                widget_backed.add(slot["widget"].get("name", name))
            if link_id is None:
                continue
            src = links.get(link_id)
            if src and src[0] in live:
                inputs[name] = [src[0], src[1]]

        # 2. widget values
        wv = node.get("widgets_values")
        spec = object_info.get(cls, {})
        if isinstance(wv, dict):
            # VHS-style nodes serialise widgets as a dict already
            for k, v in wv.items():
                if k.endswith("preview") or isinstance(v, dict):
                    continue
                inputs.setdefault(k, v)
        elif isinstance(wv, list) and wv:
            if "Power Lora Loader" in (cls or ""):
                n = 0
                for item in wv:
                    if isinstance(item, dict) and "lora" in item:
                        n += 1
                        inputs[f"lora_{n}"] = {
                            "on": item.get("on", True),
                            "lora": item.get("lora", "None"),
                            "strength": item.get("strength", 1.0),
                            "strengthTwo": item.get("strengthTwo"),
                        }
            elif spec:
                names = _widget_input_names(spec)
                i = 0
                for name, opts in names:
                    if i >= len(wv):
                        break
                    value = wv[i]
                    i += 1
                    if name not in widget_backed or name not in inputs:
                        inputs.setdefault(name, value)
                    if opts.get("control_after_generate"):
                        i += 1  # skip the 'randomize'/'increment' companion widget
            else:
                # No schema available: keep positional values under a stub so the
                # user can still see them, but flag the graph as unverified.
                inputs.setdefault("_widgets_values", wv)
        meta = {"title": node.get("title") or cls}
        if nid in disabled:
            meta["disabled"] = True
        api[nid] = {"class_type": cls, "inputs": inputs, "_meta": meta}
    return api


# --------------------------------------------------------------------------
# graph helpers
# --------------------------------------------------------------------------

def _is_link(v: Any) -> bool:
    return (
        isinstance(v, (list, tuple))
        and len(v) == 2
        and isinstance(v[0], (str, int))
        and isinstance(v[1], int)
        and not isinstance(v[0], bool)
    )


def walk_inputs(inputs: dict, prefix: tuple = ()) -> Iterator[tuple[tuple, Any]]:
    """Yield ((path...), value) for every input, flattening nested dict groups."""
    for key, value in inputs.items():
        path = prefix + (key,)
        if isinstance(value, dict) and not _is_link(value):
            yield from walk_inputs(value, path)
        else:
            yield path, value


def get_path(inputs: dict, path: tuple) -> Any:
    cur: Any = inputs
    for key in path:
        cur = cur[key]
    return cur


def set_path(inputs: dict, path: tuple, value: Any) -> None:
    cur = inputs
    for key in path[:-1]:
        cur = cur.setdefault(key, {})
    cur[path[-1]] = value


def del_path(inputs: dict, path: tuple) -> None:
    cur = inputs
    for key in path[:-1]:
        cur = cur.get(key)
        if not isinstance(cur, dict):
            return
    cur.pop(path[-1], None)


def find_class(graph: dict, needle: str) -> list[str]:
    return [nid for nid, n in graph.items() if needle.lower() in (n.get("class_type") or "").lower()]


def upstream(graph: dict, node_id: str, predicate, max_depth: int = 6) -> str | None:
    """Breadth-first search back through the graph for a node matching predicate."""
    seen, queue = {node_id}, [(node_id, 0)]
    while queue:
        nid, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        for _, value in walk_inputs(graph.get(nid, {}).get("inputs", {})):
            if not _is_link(value):
                continue
            src = str(value[0])
            if src in seen or src not in graph:
                continue
            seen.add(src)
            if predicate(graph[src]):
                return src
            queue.append((src, depth + 1))
    return None


#: Nodes that write the finished clip. Order matters only for tie-breaking.
SAVE_CLASSES = (
    "SaveVideo", "VHS_VideoCombine", "SaveWEBM", "SaveAnimatedWEBP",
    "SaveAnimatedPNG", "VideoCombine", "SaveImage",
)


def _find_output(graph: dict) -> dict | None:
    """The node that writes the clip, and which input holds its filename prefix.

    Found by class, then by input name — so a workflow that swaps SaveVideo for
    VHS_VideoCombine keeps working without anything being renamed here.
    """
    candidates = []
    for cls in SAVE_CLASSES:
        candidates.extend(find_class(graph, cls))
    if not candidates:
        # Anything with a filename_prefix input is almost certainly the output.
        candidates = [nid for nid, n in graph.items()
                      if any(p[-1] == "filename_prefix"
                             for p, _ in walk_inputs(n.get("inputs", {})))]
    for nid in candidates:
        inputs = graph[nid].get("inputs", {})
        for path, value in walk_inputs(inputs):
            if path[-1] in ("filename_prefix", "filename", "output_path"):
                if not _is_link(value):
                    return {"node": nid, "key": path[-1], "path": list(path)}
        return {"node": nid, "key": None}
    return None


def _string_key(node: dict) -> str | None:
    for path, value in walk_inputs(node.get("inputs", {})):
        if isinstance(value, str):
            return path[-1]
    return None


# --------------------------------------------------------------------------
# role detection
# --------------------------------------------------------------------------

def detect_roles(graph: dict) -> dict:
    """Map friendly roles to node ids / input paths. Everything is optional."""
    roles: dict[str, Any] = {"warnings": []}

    h3_ids = find_class(graph, H3_CLASS_HINT)
    if not h3_ids:
        roles["warnings"].append(
            f"No {H3_CLASS_HINT} node found. Prompt, refs and length can't be wired up."
        )
        return roles
    h3 = h3_ids[0]
    roles["h3"] = h3
    h3_inputs = graph[h3]["inputs"]

    # prompt: either a literal on the H3 node or a text node feeding it
    prompt_path = next(
        (p for p, _ in walk_inputs(h3_inputs) if p[-1] == "prompt"), None
    )
    if prompt_path:
        value = get_path(h3_inputs, prompt_path)
        if _is_link(value):
            src = str(value[0])
            key = _string_key(graph.get(src, {}))
            if key:
                roles["prompt"] = {"node": src, "key": key}
            else:
                roles["warnings"].append(
                    f"Node {src} feeds the prompt but has no text field to write into."
                )
        else:
            roles["prompt"] = {"node": h3, "key": "prompt", "path": list(prompt_path)}
    else:
        roles["warnings"].append("The H3 node has no 'prompt' input.")

    # ref image / audio slots
    def slots(pattern: str, expect_class: str) -> list[dict]:
        out = []
        for path, value in walk_inputs(h3_inputs):
            m = re.search(pattern, path[-1])
            if not m or not _is_link(value):
                continue
            src = str(value[0])
            node = graph.get(src, {})
            key = None
            if expect_class.lower() in (node.get("class_type") or "").lower():
                key = "image" if expect_class == "LoadImage" else "audio"
            else:
                key = _string_key(node)
            out.append({
                "index": int(m.group(1)),
                "path": list(path),
                "node": src,
                "key": key,
                "class": node.get("class_type"),
                "disabled": bool((node.get("_meta") or {}).get("disabled")),
            })
        out.sort(key=lambda s: s["index"])
        return out

    roles["ref_images"] = slots(r"ref_image_(\d+)$", "LoadImage")
    roles["ref_audios"] = slots(r"ref_audio_(\d+)$", "LoadAudio")

    # length: H3 wants frames; a duration primitive usually sits upstream
    length_path = next((p for p, _ in walk_inputs(h3_inputs) if p[-1] == "length"), None)
    if length_path:
        value = get_path(h3_inputs, length_path)
        if _is_link(value):
            dur = upstream(
                graph, h3,
                lambda n: "primitivefloat" in (n.get("class_type") or "").lower(),
            )
            if dur:
                roles["duration"] = {"node": dur, "key": "value"}
            else:
                roles["warnings"].append(
                    "Couldn't find a duration node upstream of 'length' — "
                    "segment length will use whatever the workflow has."
                )
        else:
            roles["length"] = {"node": h3, "path": list(length_path)}

    # width / height
    for dim in ("width", "height"):
        path = next((p for p, _ in walk_inputs(h3_inputs) if p[-1] == dim), None)
        if path and not _is_link(get_path(h3_inputs, path)):
            roles.setdefault("size", {})[dim] = {"node": h3, "path": list(path)}
    res = find_class(graph, "ResolutionSelector")
    if res:
        roles["resolution"] = {"node": res[0]}

    for role, cls, key in (
        ("seed", "RandomNoise", "noise_seed"),
        ("steps", "BasicScheduler", "steps"),
        ("loras", "Power Lora Loader", None),
    ):
        ids = find_class(graph, cls)
        if ids:
            roles[role] = {"node": ids[0], "key": key}

    save = _find_output(graph)
    if save:
        roles["save"] = save
    else:
        roles["warnings"].append(
            "No output node found, so the filename prefix won't be set. Rendering still "
            "works — the finished file is located from ComfyUI's history, not by name."
        )
    if "seed" not in roles:
        for cls in ("KSampler", "SamplerCustom"):
            ids = find_class(graph, cls)
            if ids:
                roles["seed"] = {"node": ids[0], "key": "seed"}
                break
    return roles


def summarize(graph: dict, roles: dict) -> dict:
    def label(role_key):
        role = roles.get(role_key)
        if not role:
            return None
        nid = role if isinstance(role, str) else role.get("node")
        return f"{nid} · {graph.get(nid, {}).get('class_type', '?')}"

    return {
        "node_count": len(graph),
        "h3": label("h3"),
        "prompt": label("prompt"),
        "duration": label("duration"),
        "seed": label("seed"),
        "steps": label("steps"),
        "resolution": label("resolution"),
        "save": label("save"),
        "loras": label("loras"),
        "ref_image_slots": len(roles.get("ref_images") or []),
        "ref_audio_slots": len(roles.get("ref_audios") or []),
        "ref_image_slots_disabled": sum(
            1 for s_ in (roles.get("ref_images") or []) if s_.get("disabled")),
        "ref_audio_slots_disabled": sum(
            1 for s_ in (roles.get("ref_audios") or []) if s_.get("disabled")),
        "warnings": roles.get("warnings", []),
    }


# --------------------------------------------------------------------------
# patching
# --------------------------------------------------------------------------

class Patcher:
    def __init__(self, graph: dict, roles: dict | None = None):
        self.graph = graph
        self.roles = roles or detect_roles(graph)

    def build(
        self,
        prompt: str,
        *,
        images: list[str | None] | None = None,
        audios: list[str | None] | None = None,
        duration: float | None = None,
        seed: int | None = None,
        steps: int | None = None,
        aspect_ratio: str | None = None,
        megapixels: float | None = None,
        filename_prefix: str | None = None,
        loras: list[dict] | None = None,
        output_fps: float | None = None,
    ) -> dict:
        g = copy.deepcopy(self.graph)
        r = self.roles

        def node_inputs(role_key) -> dict | None:
            role = r.get(role_key)
            if not role:
                return None
            return g.get(role["node"], {}).get("inputs")

        # prompt
        role = r.get("prompt")
        if role and (ins := node_inputs("prompt")) is not None:
            path = tuple(role["path"]) if role.get("path") else (role["key"],)
            set_path(ins, path, prompt)

        # ref image slots: None means "unused" -> drop the slot and its loader
        if images is not None:
            self._fill_slots(g, r.get("ref_images") or [], images)
        if audios is not None:
            self._fill_slots(g, r.get("ref_audios") or [], audios, duration=duration)

        if duration is not None:
            if (ins := node_inputs("duration")) is not None:
                ins[r["duration"]["key"]] = float(duration)
            elif r.get("length"):
                frames = max(5, round(duration * 24))
                frames += (5 - (frames % 17)) % 17
                set_path(g[r["length"]["node"]]["inputs"], tuple(r["length"]["path"]), frames)

        if seed is not None and (ins := node_inputs("seed")) is not None:
            ins[r["seed"]["key"]] = int(seed)
        if steps is not None and (ins := node_inputs("steps")) is not None:
            ins[r["steps"]["key"]] = int(steps)
        if (ins := node_inputs("save")) is not None:
            role_save = r["save"]
            if filename_prefix and role_save.get("key"):
                path = tuple(role_save["path"]) if role_save.get("path") else (role_save["key"],)
                set_path(ins, path, filename_prefix)
            # VHS_VideoCombine previews unless this is on; without it the run
            # produces nothing to download.
            if "save_output" in ins:
                ins["save_output"] = True
            if output_fps:
                for key in ("frame_rate", "fps"):
                    if key in ins and not _is_link(ins[key]):
                        ins[key] = float(output_fps)

        if (ins := node_inputs("resolution")) is not None:
            if aspect_ratio:
                ins["aspect_ratio"] = aspect_ratio
            if megapixels:
                ins["megapixels"] = float(megapixels)

        if loras is not None and (ins := node_inputs("loras")) is not None:
            for key in [k for k in ins if k.startswith("lora_")]:
                ins.pop(key)
            for i, lora in enumerate(loras, start=1):
                ins[f"lora_{i}"] = {
                    "on": bool(lora.get("on", True)),
                    "lora": lora.get("lora"),
                    "strength": float(lora.get("strength", 1.0)),
                    "strengthTwo": lora.get("strengthTwo"),
                }

        for node in g.values():
            (node.get("_meta") or {}).pop("disabled", None)
        prune(g)
        return g

    def _fill_slots(self, g, slots, values, duration=None):
        h3 = self.roles.get("h3")
        for i, slot in enumerate(slots):
            value = values[i] if i < len(values) else None
            if value:
                node = g.get(slot["node"])
                if node and slot.get("key"):
                    node["inputs"][slot["key"]] = value
                    if duration and "duration" in node["inputs"]:
                        node["inputs"]["duration"] = float(duration)
                    # a slot the workflow had bypassed comes back on for this run
                    (node.get("_meta") or {}).pop("disabled", None)
            else:
                if h3 in g:
                    del_path(g[h3]["inputs"], tuple(slot["path"]))
                g.pop(slot["node"], None)


def prune(graph: dict) -> dict:
    """Drop nodes nothing consumes, so removed slots don't leave dangling loaders."""
    consumed: set[str] = set()
    for node in graph.values():
        for _, value in walk_inputs(node.get("inputs", {})):
            if _is_link(value):
                consumed.add(str(value[0]))
    roots = [nid for nid in graph if nid not in consumed]
    keep: set[str] = set()
    queue = list(roots)
    while queue:
        nid = queue.pop()
        if nid in keep or nid not in graph:
            continue
        keep.add(nid)
        for _, value in walk_inputs(graph[nid].get("inputs", {})):
            if _is_link(value):
                queue.append(str(value[0]))
    for nid in list(graph):
        if nid not in keep:
            graph.pop(nid)
    return graph
