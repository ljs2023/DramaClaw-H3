#!/usr/bin/env python3
"""Reproducible MiniMax H3 benchmark client for an isolated ComfyUI service."""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


def _node(workflow: dict[str, dict[str, Any]], class_type: str) -> tuple[str, dict[str, Any]]:
    for node_id, node in workflow.items():
        if node.get("class_type") == class_type:
            return node_id, node
    raise ValueError(f"workflow is missing {class_type}")


def build_workflow(
    base: dict[str, dict[str, Any]],
    *,
    image_name: str,
    prompt: str,
    lora_name: str,
    seed: int,
    width: int,
    height: int,
    frames: int,
    steps: int,
) -> dict[str, dict[str, Any]]:
    """Convert the verified DramaClaw FL2VA prompt into a core-node Turbo prompt."""
    workflow = copy.deepcopy(base)
    workflow["114"]["inputs"]["image"] = image_name
    workflow.pop("116", None)
    h3_id, h3 = _node(workflow, "MiniMaxH3ImageToVideo")
    h3["inputs"].update(
        {
            "prompt": prompt,
            "width": int(width),
            "height": int(height),
            "length": int(frames),
            "first_frame": ["114", 0],
        }
    )
    h3["inputs"].pop("last_frame", None)
    for node_id in list(workflow):
        if workflow[node_id].get("class_type") in {
            "ResolutionSelector",
            "ComfyMathExpression",
            "PrimitiveFloat",
            "EasyCache",
        }:
            workflow.pop(node_id)

    unet_id, _ = _node(workflow, "UNETLoader")
    _, scheduler = _node(workflow, "BasicScheduler")
    _, guider = _node(workflow, "BasicGuider")
    _, sampler = _node(workflow, "KSamplerSelect")
    _, noise = _node(workflow, "RandomNoise")
    _, save = _node(workflow, "SaveVideo")
    workflow["benchmark_lora"] = {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {"model": [unet_id, 0], "lora_name": lora_name, "strength_model": 1.0},
        "_meta": {"title": "H3 benchmark Turbo LoRA"},
    }
    workflow["benchmark_shift"] = {
        "class_type": "MiniMaxH3SigmaShift",
        "inputs": {
            "model": ["benchmark_lora", 0],
            "shift_video": 12.0,
            "shift_audio": 3.0,
        },
        "_meta": {"title": "H3 benchmark sigma shift"},
    }
    scheduler["inputs"].update(
        {"model": ["benchmark_shift", 0], "scheduler": "simple", "steps": int(steps)}
    )
    guider["inputs"]["model"] = ["benchmark_shift", 0]
    sampler["inputs"]["sampler_name"] = "euler"
    noise["inputs"]["noise_seed"] = int(seed)
    save["inputs"]["filename_prefix"] = f"h3_turbo_benchmark/{steps}step"
    return workflow


def _json_request(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def read_peak_vram_mb() -> int | None:
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-compute-apps=used_memory",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    values = [int(line.strip()) for line in output.splitlines() if line.strip().isdigit()]
    return max(values, default=0)


def _output_path(history: dict[str, Any], prompt_id: str) -> str:
    item = history.get(prompt_id, history)
    for output in item.get("outputs", {}).values():
        for field in ("video", "videos", "gifs", "images"):
            for media in output.get(field, []) if isinstance(output, dict) else []:
                if media.get("filename"):
                    parts = [str(media.get("subfolder") or "").strip("/"), media["filename"]]
                    return "/".join(part for part in parts if part)
    raise RuntimeError("ComfyUI completed but returned no saved video")


def execute_prompt(*, server_url: str, workflow: dict[str, Any], timeout: float = 1800) -> dict[str, Any]:
    started = time.monotonic()
    queued = _json_request(
        f"{server_url.rstrip('/')}/prompt",
        {"prompt": workflow, "client_id": f"h3-benchmark-{uuid.uuid4().hex}"},
    )
    prompt_id = str(queued["prompt_id"])
    peak_vram = 0
    while time.monotonic() - started < timeout:
        peak_vram = max(peak_vram, read_peak_vram_mb() or 0)
        history = _json_request(f"{server_url.rstrip('/')}/history/{prompt_id}")
        if prompt_id in history:
            status = history[prompt_id].get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(json.dumps(status, ensure_ascii=False))
            try:
                output_path = _output_path(history, prompt_id)
            except RuntimeError:
                pass
            else:
                return {
                    "prompt_id": prompt_id,
                    "wall_seconds": round(time.monotonic() - started, 3),
                    "output_path": output_path,
                    "peak_vram_mb": peak_vram or None,
                }
        time.sleep(1)
    raise TimeoutError(f"ComfyUI prompt {prompt_id} timed out after {timeout}s")


def run_benchmark(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    workflow_path = Path(config["workflow_path"])
    if not workflow_path.is_absolute():
        workflow_path = Path.cwd() / workflow_path
    base = json.loads(workflow_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    runs = []
    for steps in config["steps"]:
        workflow = build_workflow(
            base,
            image_name=config["image_name"],
            prompt=config["prompt"],
            lora_name=config["lora_name"],
            seed=config["seed"],
            width=config["width"],
            height=config["height"],
            frames=config["frames"],
            steps=steps,
        )
        core = {
            "steps": int(steps),
            "seed": int(config["seed"]),
            "width": int(config["width"]),
            "height": int(config["height"]),
            "frames": int(config["frames"]),
        }
        try:
            result = execute_prompt(server_url=config["server_url"], workflow=workflow)
            record = {
                **core,
                "wall_seconds": result["wall_seconds"],
                "status": "succeeded",
                "output_path": result["output_path"],
                "peak_vram_mb": result.get("peak_vram_mb") or read_peak_vram_mb(),
                "prompt_id": result["prompt_id"],
            }
        except Exception as exc:  # Preserve failed trials in the benchmark record.
            record = {
                **core,
                "wall_seconds": None,
                "status": "failed",
                "output_path": None,
                "peak_vram_mb": read_peak_vram_mb(),
                "prompt_id": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
        runs.append(record)
    report = {"config": config, "runs": runs}
    (output_dir / "benchmark-results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(run_benchmark(args.config, args.output_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
