"""Run the real uploaded workflow through conversion, detection and patching."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import workflow  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "workflow.ui.json"
UI = Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURE
if not UI.exists():
    sys.exit(f"No workflow to test against. Pass one: python3 {sys.argv[0]} my_workflow.json")

# A stand-in for /object_info covering the classes this graph uses.
OI = {
    "CLIPLoader": {"input": {"required": {"clip_name": [["a.safetensors"]], "type": [["minimax"]], "device": [["default"]]}}},
    "RandomNoise": {"input": {"required": {"noise_seed": ["INT", {"control_after_generate": True}]}}},
    "VAELoader": {"input": {"required": {"vae_name": [["a.safetensors"]]}}},
    "PrimitiveStringMultiline": {"input": {"required": {"value": ["STRING", {"multiline": True}]}}},
    "ComfyMathExpression": {"input": {"required": {"expression": ["STRING", {}]}, "optional": {"values": ["DICT", {}]}}},
    "BasicGuider": {"input": {"required": {"model": ["MODEL"], "conditioning": ["CONDITIONING"]}}},
    "ModelAttentionBackend": {"input": {"required": {"model": ["MODEL"], "backend": [["comfy kitchen attention"]]}}},
    "MiniMaxH3ReferenceToVideo": {"input": {"required": {
        "clip": ["CLIP"], "vae": ["VAE"], "audio_vae": ["VAE"],
        "prompt": ["STRING", {"multiline": True}],
        "width": ["INT", {}], "height": ["INT", {}], "length": ["INT", {}],
        "reference_mode": [["max", "min"]],
    }}},
    "PrimitiveFloat": {"input": {"required": {"value": ["FLOAT", {}]}}},
    "VAEDecodeAudio": {"input": {"required": {"samples": ["LATENT"], "vae": ["VAE"]}}},
    "KSamplerSelect": {"input": {"required": {"sampler_name": [["er_sde"]]}}},
    "UNETLoader": {"input": {"required": {"unet_name": [["a.safetensors"]], "weight_dtype": [["default"]]}}},
    "MiniMaxH3SigmaShift": {"input": {"required": {"model": ["MODEL"], "shift": ["FLOAT", {}], "b": ["FLOAT", {}]}}},
    "easy cleanGpuUsed": {"input": {"required": {"anything": ["*"]}}},
    "SamplerCustomAdvanced": {"input": {"required": {}}},
    "VAEDecode": {"input": {"required": {"samples": ["LATENT"], "vae": ["VAE"]}}},
    "CreateVideo": {"input": {"required": {"images": ["IMAGE"], "fps": ["FLOAT", {}], "bit_depth": ["INT", {}]}}},
    "SaveVideo": {"input": {"required": {"video": ["VIDEO"], "filename_prefix": ["STRING", {}],
                                         "format": [["auto"]], "codec": [["auto"]]}}},
    "BasicScheduler": {"input": {"required": {"model": ["MODEL"], "scheduler": [["beta57"]],
                                              "steps": ["INT", {}], "denoise": ["FLOAT", {}]}}},
    "ResolutionSelector": {"input": {"required": {"aspect_ratio": [["16:9 (Widescreen)"]],
                                                  "megapixels": ["FLOAT", {}], "multiple": ["INT", {}]}}},
    "Power Lora Loader (rgthree)": {"input": {"required": {"model": ["MODEL"], "clip": ["CLIP"]}}},
    "LoadImage": {"input": {"required": {"image": [["example.png"]], "upload": [["image"]]}}},
    "VHS_LoadAudioUpload": {"input": {"required": {"audio": [["output.wav"]], "start_time": ["FLOAT", {}],
                                                   "duration": ["FLOAT", {}]}}},
    "TextBox1": {"input": {"required": {"text1": ["STRING", {"multiline": True}]}}},
}


def main():
    ui = json.loads(UI.read_text())
    api = workflow.ui_to_api(ui, OI)
    print(f"converted: {len(ui['nodes'])} editor nodes -> {len(api)} api nodes")

    unverified = [n for n, v in api.items() if "_widgets_values" in v["inputs"]]
    assert not unverified, f"nodes without schema: {unverified}"

    # Bypassed leaf loaders are kept as spare slots, flagged rather than dropped.
    bypassed = {"230", "231", "216"}
    assert bypassed <= set(api), "bypassed leaf loaders should be kept as spares"
    for nid in bypassed:
        assert api[nid]["_meta"]["disabled"] is True, nid

    roles = workflow.detect_roles(api)
    summary = workflow.summarize(api, roles)
    print(json.dumps(summary, indent=2))

    assert roles.get("h3"), "H3 node not found"
    assert roles["prompt"]["node"] == "212", roles["prompt"]
    assert roles["duration"]["node"] == "202", roles.get("duration")
    assert len(roles["ref_images"]) == 6, roles["ref_images"]
    assert len(roles["ref_audios"]) == 3, roles["ref_audios"]
    assert [x["disabled"] for x in roles["ref_images"]] == [False] * 4 + [True] * 2
    assert [x["disabled"] for x in roles["ref_audios"]] == [False, False, True]
    assert roles["seed"]["node"] == "193"
    assert roles["steps"]["node"] == "195"
    assert roles["save"]["node"] == "200"
    assert roles["resolution"]["node"] == "204"

    patcher = workflow.Patcher(api, roles)
    graph = patcher.build(
        "SEGMENT PROMPT TEXT",
        images=["vidpipe/last_frame.png", "vidpipe/ref_a.png", None, None, None, None],
        audios=["vidpipe/voice.wav", None, None],
        duration=15.0, seed=4242, steps=8,
        aspect_ratio="9:16 (Vertical)", megapixels=1.0,
        filename_prefix="video/test_p1_s00",
        loras=[{"on": True, "lora": "turbo.safetensors", "strength": 0.75}],
    )

    assert graph["212"]["inputs"]["text1"] == "SEGMENT PROMPT TEXT"
    assert graph["226"]["inputs"]["image"] == "vidpipe/last_frame.png"
    assert graph["227"]["inputs"]["image"] == "vidpipe/ref_a.png"
    assert "228" not in graph and "229" not in graph, "unused loaders should be pruned"
    assert "230" not in graph and "231" not in graph, "unused spares should be pruned"
    assert "216" not in graph, "unused spare audio should be pruned"
    # Keys arrive dotted ("ref_images.ref_image_2"), so match on the suffix —
    # a plain `in` test against the leaf name silently passes and checks nothing.
    def slots_present(g_):
        keys = [p[-1] for p, _ in workflow.walk_inputs(g_[roles["h3"]]["inputs"])]
        return {k.rsplit(".", 1)[-1] for k in keys}

    live = slots_present(graph)
    assert {"ref_image_0", "ref_image_1", "ref_audio_0"} <= live, live
    assert not ({"ref_image_2", "ref_image_3", "ref_image_4", "ref_image_5",
                 "ref_audio_1", "ref_audio_2"} & live), live
    assert graph["210"]["inputs"]["audio"] == "vidpipe/voice.wav"
    assert graph["210"]["inputs"]["duration"] == 15.0
    assert graph["202"]["inputs"]["value"] == 15.0
    assert graph["193"]["inputs"]["noise_seed"] == 4242
    assert graph["195"]["inputs"]["steps"] == 8
    assert graph["204"]["inputs"]["aspect_ratio"] == "9:16 (Vertical)"
    assert graph["204"]["inputs"]["megapixels"] == 1.0
    assert graph["200"]["inputs"]["filename_prefix"] == "video/test_p1_s00"
    assert graph["214"]["inputs"]["lora_1"]["lora"] == "turbo.safetensors"
    assert "lora_2" not in graph["214"]["inputs"]
    assert graph["187"]["inputs"]["clip_name"].endswith(".safetensors")
    assert graph["186"]["inputs"]["unet_name"].startswith("minimax_h3")

    # filling a spare slot switches its bypassed loader back on
    six = patcher.build(
        "SIX REFERENCES",
        images=[f"vidpipe/r{i}.png" for i in range(5)] + ["vidpipe/carried_frame.png"],
        audios=["vidpipe/a0.wav", "vidpipe/a1.wav", "vidpipe/a2.wav"],
    )
    for nid in ("226", "227", "228", "229", "230", "231", "210", "215", "216"):
        assert nid in six, f"{nid} should be live when its slot is filled"
        assert "disabled" not in (six[nid].get("_meta") or {}), f"{nid} still flagged"
    assert six["231"]["inputs"]["image"] == "vidpipe/carried_frame.png"
    assert six["216"]["inputs"]["audio"] == "vidpipe/a2.wav"
    six_live = slots_present(six)
    assert {f"ref_image_{i}" for i in range(6)} <= six_live, six_live
    assert {f"ref_audio_{i}" for i in range(3)} <= six_live, six_live

    # no _meta disabled markers survive into anything sent to ComfyUI
    for g_ in (graph, six):
        for nid, node in g_.items():
            assert "disabled" not in (node.get("_meta") or {}), nid

    # untouched graph must survive a second build (no shared mutation)
    again = patcher.build("SECOND", images=[None] * 6, audios=[None] * 3)
    assert again["212"]["inputs"]["text1"] == "SECOND"
    assert not (slots_present(again) & {f"ref_image_{i}" for i in range(6)}), \
        "empty slots should all be removed"
    assert graph["212"]["inputs"]["text1"] == "SEGMENT PROMPT TEXT"

    # every remaining link must point at a node that still exists
    for nid, node in graph.items():
        for path, value in workflow.walk_inputs(node["inputs"]):
            if workflow._is_link(value):
                assert str(value[0]) in graph, f"{nid}.{path} dangles to {value[0]}"

    print("\nall workflow assertions passed")


if __name__ == "__main__":
    main()
