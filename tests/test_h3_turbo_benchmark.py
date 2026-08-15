from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "h3_turbo_benchmark.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("h3_turbo_benchmark", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_benchmark_writes_required_record(tmp_path, monkeypatch):
    benchmark = _load_module()
    config_path = ROOT / "tests" / "fixtures" / "h3_turbo_benchmark.json"

    monkeypatch.setattr(
        benchmark,
        "execute_prompt",
        lambda **kwargs: {
            "prompt_id": "fixed-prompt",
            "wall_seconds": 12.5,
            "output_path": "video/h3_benchmark.mp4",
        },
    )
    monkeypatch.setattr(benchmark, "read_peak_vram_mb", lambda: 9876)

    report = benchmark.run_benchmark(config_path, tmp_path)

    assert report["runs"][0] == {
        "steps": 8,
        "seed": 20260815,
        "width": 864,
        "height": 480,
        "frames": 124,
        "wall_seconds": 12.5,
        "status": "succeeded",
        "output_path": "video/h3_benchmark.mp4",
        "peak_vram_mb": 9876,
        "prompt_id": "fixed-prompt",
    }
    written = json.loads((tmp_path / "benchmark-results.json").read_text())
    assert written == report


def test_turbo_workflow_uses_lora_shift_and_euler():
    benchmark = _load_module()
    base = json.loads(
        (ROOT / "src/novelvideo/generators/h3_workflows/minimax_h3_fl2va_api.json").read_text()
    )

    workflow = benchmark.build_workflow(
        base,
        image_name="canary_first.jpg",
        prompt="fixed prompt",
        lora_name="turbo.safetensors",
        seed=42,
        width=864,
        height=480,
        frames=124,
        steps=8,
    )

    assert workflow["benchmark_lora"]["class_type"] == "LoraLoaderModelOnly"
    assert workflow["benchmark_shift"]["inputs"]["shift_video"] == 12.0
    assert workflow["105:17"]["inputs"]["sampler_name"] == "euler"
    assert workflow["105:9"]["inputs"]["steps"] == 8
    assert "105:150" not in workflow


def test_baseline_workflow_keeps_easycache_and_production_sampler():
    benchmark = _load_module()
    base = json.loads(
        (ROOT / "src/novelvideo/generators/h3_workflows/minimax_h3_fl2va_api.json").read_text()
    )

    workflow = benchmark.build_workflow(
        base,
        image_name="canary_first.jpg",
        prompt="fixed prompt",
        lora_name="",
        seed=42,
        width=864,
        height=480,
        frames=124,
        steps=12,
    )

    assert "benchmark_lora" not in workflow
    assert workflow["105:150"]["inputs"]["reuse_threshold"] == 0.28
    assert workflow["105:17"]["inputs"]["sampler_name"] == "res_multistep"
    assert workflow["105:9"]["inputs"]["steps"] == 12


def test_run_benchmark_uses_configured_seed_per_trial(tmp_path, monkeypatch):
    benchmark = _load_module()
    config = {
        "server_url": "http://127.0.0.1:18190",
        "workflow_path": str(
            ROOT / "src/novelvideo/generators/h3_workflows/minimax_h3_fl2va_api.json"
        ),
        "image_name": "canary_first.jpg",
        "prompt": "fixed",
        "lora_name": "turbo.safetensors",
        "seed": 1,
        "seeds": [101, 102],
        "width": 864,
        "height": 480,
        "frames": 124,
        "steps": [8, 8],
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    seen = []

    def fake_execute(**kwargs):
        seen.append(kwargs["workflow"]["105:15"]["inputs"]["noise_seed"])
        return {"prompt_id": str(len(seen)), "wall_seconds": 1.0, "output_path": "x.mp4"}

    monkeypatch.setattr(benchmark, "execute_prompt", fake_execute)
    monkeypatch.setattr(benchmark, "read_peak_vram_mb", lambda: 1)

    report = benchmark.run_benchmark(config_path, tmp_path / "out")

    assert seen == [101, 102]
    assert [item["seed"] for item in report["runs"]] == [101, 102]
