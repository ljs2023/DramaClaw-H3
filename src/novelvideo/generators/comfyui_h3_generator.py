"""MiniMax H3 local ComfyUI video backend.

The adapter is intentionally separate from :class:`ComfyUIVideoGenerator` so
the existing Wan/LTX workflows keep their established behaviour.
"""

from __future__ import annotations

import copy
import asyncio
import fcntl
import json
import mimetypes
import os
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import aiohttp
import websockets

from novelvideo.generators.video_generator import (
    VideoGenResult,
    VideoGenStatus,
    VideoGeneratorBase,
)


@dataclass(frozen=True)
class H3Preset:
    steps: int
    easycache: float
    ref_image_size: str


H3_PRESETS: dict[str, H3Preset] = {
    "QUALITY": H3Preset(steps=20, easycache=0.0, ref_image_size="max"),
    "FAST": H3Preset(steps=16, easycache=0.20, ref_image_size="max"),
    "TURBO": H3Preset(steps=12, easycache=0.28, ref_image_size="match"),
}

H3_DIMENSIONS: dict[str, tuple[int, int]] = {
    "9:16": (480, 864),
    "16:9": (864, 480),
    "1:1": (480, 480),
}

H3_DURATIONS = (5, 8, 10, 15)
H3_WORKFLOW_DIR = Path(__file__).parent / "h3_workflows"
H3_FL2VA_WORKFLOW = H3_WORKFLOW_DIR / "minimax_h3_fl2va_api.json"
H3_REF2VA_WORKFLOW = H3_WORKFLOW_DIR / "minimax_h3_ref2va_api.json"


def h3_dimensions(aspect_ratio: str) -> tuple[int, int]:
    """Return the only 480p geometries verified on the target H3 service."""
    normalized = str(aspect_ratio or "").strip()
    try:
        return H3_DIMENSIONS[normalized]
    except KeyError:
        raise ValueError("MiniMax H3 Local 目前仅支持 9:16、16:9、1:1 的 480P 画幅。") from None


def h3_frame_count(duration: float | int) -> int:
    """Convert seconds to H3's required 17k+5 frame grid at 24 FPS."""
    seconds = int(duration)
    if float(duration) != seconds or seconds not in H3_DURATIONS:
        raise ValueError("MiniMax H3 Local 目前仅支持 5、8、10、15 秒。")
    raw_frames = max(5, round(seconds * 24))
    return raw_frames + (5 - raw_frames % 17) % 17


def _image_references(references: Iterable[Any] | None) -> list[Any]:
    return [
        item
        for item in (references or [])
        if str(getattr(item, "type", "image") or "image").lower() == "image"
        and str(getattr(item, "path", "") or "").strip()
    ]


def select_h3_mode(
    image_path: str | None,
    last_frame_path: str | None,
    references: Iterable[Any] | None,
) -> str:
    """Select the real H3 workflow without exposing FL2VA/REF2VA to users."""
    if _image_references(references):
        return "reference"
    if str(image_path or "").strip() and str(last_frame_path or "").strip():
        return "first_last"
    if str(image_path or "").strip():
        return "first_frame"
    raise ValueError("MiniMax H3 Local 需要首帧或参考图片。")


def _reference_category(role: str) -> str:
    folded = role.casefold()
    for needle, category in (
        ("角色", "CHARACTER"),
        ("character", "CHARACTER"),
        ("场景", "SCENE"),
        ("scene", "SCENE"),
        ("道具", "PROP"),
        ("prop", "PROP"),
        ("服装", "COSTUME"),
        ("costume", "COSTUME"),
        ("风格", "STYLE"),
        ("style", "STYLE"),
    ):
        if needle in folded:
            return category
    return "OTHER"


def _reference_name(item: Any) -> str:
    role = str(getattr(item, "role", "") or "").strip()
    for separator in ("：", ":"):
        if separator in role:
            suffix = role.split(separator, 1)[1].strip()
            if suffix:
                return suffix
    return Path(str(getattr(item, "path", "") or "")).stem or "未命名素材"


def build_h3_reference_prompt(
    prompt: str,
    references: Iterable[Any] | None,
) -> tuple[str, list[Any]]:
    """Compact image references and build matching ``<Picture N>`` labels."""
    compact = _image_references(references)
    if len(compact) > 9:
        raise ValueError("MiniMax H3最多允许9张参考图片，请移除多余素材。")
    if not compact:
        raise ValueError("MiniMax H3 Reference 模式至少需要1张参考图片。")

    lines = ["REFERENCE MAP:"]
    for index, item in enumerate(compact, start=1):
        category = _reference_category(str(getattr(item, "role", "") or ""))
        name = _reference_name(item)
        lines.append(f"<Picture {index}> {category}: {name}")
        if category == "CHARACTER":
            lines.append(f"Use it as the identity reference for {name}.")
        elif category == "SCENE":
            lines.append("Use it as the environment and layout reference.")
        elif category == "PROP":
            lines.append("Preserve the prop appearance.")
        else:
            lines.append(f"Preserve the {category.lower()} appearance and identity.")
        lines.append("")
    lines.append(str(prompt or "").strip())
    return "\n".join(lines).strip(), compact


def _load_workflow(path: Path) -> dict[str, dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _workflow_node(
    workflow: dict[str, dict[str, Any]], class_type: str
) -> tuple[str, dict[str, Any]]:
    for node_id, node in workflow.items():
        if node.get("class_type") == class_type:
            return node_id, node
    raise ValueError(f"MiniMax H3 API工作流缺少节点: {class_type}")


def _apply_h3_preset(
    workflow: dict[str, dict[str, Any]], preset: H3Preset
) -> None:
    _scheduler_id, scheduler = _workflow_node(workflow, "BasicScheduler")
    scheduler["inputs"]["steps"] = preset.steps
    unet_id, _unet = _workflow_node(workflow, "UNETLoader")
    _guider_id, guider = _workflow_node(workflow, "BasicGuider")

    cache_entries = [
        (node_id, node)
        for node_id, node in workflow.items()
        if node.get("class_type") == "EasyCache"
    ]
    if preset.easycache <= 0:
        for cache_id, _cache in cache_entries:
            workflow.pop(cache_id, None)
        scheduler["inputs"]["model"] = [unet_id, 0]
        guider["inputs"]["model"] = [unet_id, 0]
        return

    if not cache_entries:
        raise ValueError("MiniMax H3 API工作流缺少EasyCache节点。")
    cache_id, cache = cache_entries[0]
    cache["inputs"]["reuse_threshold"] = preset.easycache
    cache["inputs"]["model"] = [unet_id, 0]
    scheduler["inputs"]["model"] = [cache_id, 0]
    guider["inputs"]["model"] = [cache_id, 0]


def build_h3_workflow(
    *,
    mode: str,
    prompt: str,
    first_frame_filename: str | None = None,
    last_frame_filename: str | None = None,
    reference_filenames: list[str] | None = None,
    aspect_ratio: str = "9:16",
    duration: float | int = 5,
    preset: str = "FAST",
    seed: int = 0,
) -> dict[str, dict[str, Any]]:
    """Build a minimal ComfyUI API prompt from the bundled verified templates."""
    preset_name = str(preset or "FAST").strip().upper()
    try:
        preset_config = H3_PRESETS[preset_name]
    except KeyError:
        raise ValueError("MiniMax H3质量仅支持 QUALITY、FAST、TURBO。") from None
    width, height = h3_dimensions(aspect_ratio)
    frames = h3_frame_count(duration)

    if mode in {"first_frame", "first_last"}:
        if not first_frame_filename:
            raise ValueError("MiniMax H3首帧模式缺少首帧图片。")
        if mode == "first_last" and not last_frame_filename:
            raise ValueError("MiniMax H3首尾帧模式缺少尾帧图片。")
        workflow = copy.deepcopy(_load_workflow(H3_FL2VA_WORKFLOW))
        h3_id, h3_node = _workflow_node(workflow, "MiniMaxH3ImageToVideo")
        h3_node["inputs"].update(
            {"prompt": str(prompt or ""), "width": width, "height": height, "length": frames}
        )
        workflow["114"]["inputs"]["image"] = first_frame_filename
        h3_node["inputs"]["first_frame"] = ["114", 0]
        if mode == "first_last":
            workflow["116"]["inputs"]["image"] = str(last_frame_filename)
            h3_node["inputs"]["last_frame"] = ["116", 0]
        else:
            workflow.pop("116", None)
            h3_node["inputs"].pop("last_frame", None)
        for node_id in list(workflow):
            if workflow[node_id].get("class_type") in {
                "ResolutionSelector",
                "ComfyMathExpression",
                "PrimitiveFloat",
            }:
                workflow.pop(node_id)
    elif mode == "reference":
        filenames = [str(value) for value in (reference_filenames or []) if str(value).strip()]
        if not filenames:
            raise ValueError("MiniMax H3 Reference 模式至少需要1张参考图片。")
        if len(filenames) > 9:
            raise ValueError("MiniMax H3最多允许9张参考图片，请移除多余素材。")
        workflow = copy.deepcopy(_load_workflow(H3_REF2VA_WORKFLOW))
        h3_id, h3_node = _workflow_node(workflow, "MiniMaxH3ReferenceToVideo")
        h3_node["inputs"].update(
            {
                "prompt": str(prompt or ""),
                "width": width,
                "height": height,
                "length": frames,
                "ref_image_size": preset_config.ref_image_size,
            }
        )
        for index in range(9):
            node_id = str(200 + index)
            input_name = f"ref_images.ref_image_{index}"
            if index < len(filenames):
                workflow[node_id]["inputs"]["image"] = filenames[index]
                h3_node["inputs"][input_name] = [node_id, 0]
            else:
                workflow.pop(node_id, None)
                h3_node["inputs"].pop(input_name, None)
        for node_id in list(workflow):
            if workflow[node_id].get("class_type") in {
                "ResolutionSelector",
                "ComfyMathExpression",
                "PrimitiveFloat",
                "PrimitiveStringMultiline",
            }:
                workflow.pop(node_id)
    else:
        raise ValueError(f"MiniMax H3不支持的生成模式: {mode}")

    _noise_id, noise = _workflow_node(workflow, "RandomNoise")
    noise["inputs"]["noise_seed"] = int(seed)
    _apply_h3_preset(workflow, preset_config)
    return workflow


def parse_h3_video_output(history: dict[str, Any], prompt_id: str) -> dict[str, str]:
    """Find a saved video across ComfyUI output schema versions."""
    prompt_history = history.get(prompt_id, history)
    outputs = prompt_history.get("outputs", {}) if isinstance(prompt_history, dict) else {}
    for output in outputs.values() if isinstance(outputs, dict) else []:
        if not isinstance(output, dict):
            continue
        for field in ("video", "videos", "gifs", "images"):
            items = output.get(field)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict) or not str(item.get("filename") or "").strip():
                    continue
                return {
                    "filename": str(item["filename"]),
                    "subfolder": str(item.get("subfolder") or ""),
                    "type": str(item.get("type") or "output"),
                }
    raise ValueError("MiniMax H3工作流执行完成，但未找到视频输出。")


class _AsyncFileLock:
    """Cross-process lock used to keep H3 at one inference on a 12GB GPU."""

    def __init__(self, path: Path):
        self.path = path
        self._handle = None

    async def __aenter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+")
        await asyncio.to_thread(fcntl.flock, self._handle.fileno(), fcntl.LOCK_EX)
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        if self._handle is not None:
            await asyncio.to_thread(fcntl.flock, self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
        return False


class MiniMaxH3ComfyUIGenerator(VideoGeneratorBase):
    """Local MiniMax H3 adapter for a dedicated ComfyUI render service."""

    DEFAULT_ADDRESS = "192.168.3.9:18189"

    def __init__(
        self,
        server_address: str | None = None,
        *,
        use_ssl: bool | None = None,
        timeout: float | None = None,
        lock_path: str | Path | None = None,
        **_ignored: Any,
    ):
        self.server_address = str(
            server_address or os.environ.get("COMFYUI_H3_ADDRESS", self.DEFAULT_ADDRESS)
        ).removeprefix("http://").removeprefix("https://").rstrip("/")
        if use_ssl is None:
            use_ssl = os.environ.get("COMFYUI_H3_USE_SSL", "false").lower() in {
                "1",
                "true",
                "yes",
            }
        self.use_ssl = bool(use_ssl)
        self.timeout = float(timeout or os.environ.get("COMFYUI_H3_TIMEOUT", "1800"))
        self.max_concurrent = max(
            1, int(os.environ.get("COMFYUI_H3_MAX_CONCURRENT", "1"))
        )
        http_scheme = "https" if self.use_ssl else "http"
        ws_scheme = "wss" if self.use_ssl else "ws"
        self.http_url = f"{http_scheme}://{self.server_address}"
        self.ws_url = f"{ws_scheme}://{self.server_address}"
        self.lock_path = Path(
            lock_path
            or os.environ.get("COMFYUI_H3_LOCK_PATH", "/tmp/dramaclaw-comfyui-h3.lock")
        )

    @staticmethod
    def friendly_error(error: Exception) -> str:
        raw = str(error)
        folded = raw.casefold()
        if any(token in folded for token in ("cannot connect", "connection refused", "connect call failed")):
            return (
                "MiniMax H3 本地渲染服务当前不可访问。"
                "请检查 192.168.3.9:18189。"
            )
        if any(token in folded for token in ("out of memory", "cuda error", "allocation on device")):
            return (
                "H3生成失败：GPU显存不足。"
                "请确保没有其他大型GPU任务同时运行。"
            )
        if any(token in folded for token in ("execution_error", "invalid prompt", "workflow")):
            return "MiniMax H3工作流执行失败。查看任务详情获取ComfyUI错误日志。"
        if "timed out" in folded or "timeout" in folded:
            return "MiniMax H3生成超时，请在任务中心重试。"
        return f"MiniMax H3生成失败：{raw}"

    async def _get_json(self, path: str) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=min(self.timeout, 30))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{self.http_url}{path}") as response:
                if response.status != 200:
                    raise RuntimeError(f"ComfyUI HTTP {response.status}: {await response.text()}")
                payload = await response.json()
                return payload if isinstance(payload, dict) else {}

    async def health_check(self) -> dict[str, Any]:
        """Check only ComfyUI system stats; this never loads H3 models."""
        try:
            stats = await self._get_json("/system_stats")
            return {"online": True, "address": self.server_address, "stats": stats}
        except Exception as exc:  # noqa: BLE001
            return {
                "online": False,
                "address": self.server_address,
                "error": self.friendly_error(exc),
            }

    async def _upload_path(self, path: str | Path, filename: str) -> str:
        source = Path(path)
        if not source.exists():
            raise ValueError(f"参考图片不存在：{source}")
        form = aiohttp.FormData()
        form.add_field(
            "image",
            source.read_bytes(),
            filename=filename,
            content_type=mimetypes.guess_type(source.name)[0] or "application/octet-stream",
        )
        timeout = aiohttp.ClientTimeout(total=min(self.timeout, 120))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{self.http_url}/upload/image", data=form) as response:
                if response.status != 200:
                    raise RuntimeError(f"上传图片失败: {await response.text()}")
                payload = await response.json()
        return str(payload.get("name") or filename)

    async def _queue_prompt(self, workflow: dict[str, Any], client_id: str) -> str:
        timeout = aiohttp.ClientTimeout(total=min(self.timeout, 120))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self.http_url}/prompt",
                json={"prompt": workflow, "client_id": client_id},
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"MiniMax H3工作流提交失败: {await response.text()}")
                payload = await response.json()
        prompt_id = str(payload.get("prompt_id") or "")
        if not prompt_id:
            raise RuntimeError("MiniMax H3工作流未返回 prompt_id")
        return prompt_id

    async def _history(self, prompt_id: str) -> dict[str, Any]:
        return await self._get_json(f"/history/{urllib.parse.quote(prompt_id)}")

    async def _download_output(self, info: dict[str, str]) -> bytes:
        query = urllib.parse.urlencode(
            {
                "filename": info["filename"],
                "subfolder": info.get("subfolder", ""),
                "type": info.get("type", "output"),
            }
        )
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{self.http_url}/view?{query}") as response:
                if response.status != 200:
                    raise RuntimeError(f"下载H3视频失败: {await response.text()}")
                return await response.read()

    async def _execute_workflow(
        self,
        workflow: dict[str, Any],
        *,
        on_log: Callable[[str], None] | None = None,
        on_progress: Callable[[float], None] | None = None,
    ) -> tuple[bytes, str]:
        client_id = str(uuid.uuid4())
        ws_url = f"{self.ws_url}/ws?clientId={client_id}"
        if on_log:
            on_log("MiniMax H3 Local：提交渲染任务")
        async with websockets.connect(
            ws_url,
            max_size=500 * 1024 * 1024,
            ping_interval=None,
            ping_timeout=None,
            proxy=None,
        ) as websocket:
            prompt_id = await self._queue_prompt(workflow, client_id)
            async with asyncio.timeout(self.timeout):
                while True:
                    raw_message = await websocket.recv()
                    if not isinstance(raw_message, str):
                        continue
                    message = json.loads(raw_message)
                    data = message.get("data") or {}
                    if data.get("prompt_id") not in (None, prompt_id):
                        continue
                    message_type = message.get("type")
                    if message_type == "progress":
                        value = float(data.get("value") or 0)
                        maximum = float(data.get("max") or 0)
                        if maximum > 0 and on_progress:
                            on_progress(value / maximum)
                        if on_log and maximum > 0:
                            on_log(f"MiniMax H3采样：{int(value)} / {int(maximum)}")
                    elif message_type == "executing":
                        node_id = data.get("node")
                        if node_id is None:
                            break
                        if on_log:
                            title = workflow.get(str(node_id), {}).get("_meta", {}).get("title")
                            on_log(f"MiniMax H3执行：{title or node_id}")
                    elif message_type == "execution_error":
                        raise RuntimeError(f"execution_error: {data}")

        history: dict[str, Any] = {}
        for _attempt in range(10):
            history = await self._history(prompt_id)
            try:
                output_info = parse_h3_video_output(history, prompt_id)
                return await self._download_output(output_info), prompt_id
            except ValueError:
                await asyncio.sleep(1)
        raise ValueError("MiniMax H3工作流完成，但历史记录中未找到视频输出。")

    @staticmethod
    async def _run_ffmpeg(*args: str) -> None:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="replace").strip())

    async def _remove_audio(self, video_path: str | Path) -> None:
        video = Path(video_path)
        muted = video.with_name(f"{video.stem}.muted{video.suffix}")
        await self._run_ffmpeg("-y", "-i", str(video), "-c:v", "copy", "-an", str(muted))
        os.replace(muted, video)

    async def _extract_last_frame(
        self, video_path: str | Path, last_frame_path: str | Path
    ) -> None:
        await self._run_ffmpeg(
            "-y",
            "-sseof",
            "-0.1",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-update",
            "1",
            str(last_frame_path),
        )

    async def generate(
        self,
        image_path: str | None,
        prompt: str,
        output_path: str,
        aspect_ratio: str = "9:16",
        duration: float = 5.0,
        on_log: Callable[[str], None] | None = None,
        on_progress: Callable[[float], None] | None = None,
        last_frame_path: str | None = None,
        references: Iterable[Any] | None = None,
        h3_preset: str | None = None,
        seed: int | None = None,
        audio_setting: str | None = None,
        **_kwargs: Any,
    ) -> VideoGenResult:
        task_token = uuid.uuid4().hex
        try:
            reference_items = _image_references(references)
            mode = select_h3_mode(image_path, last_frame_path, reference_items)
            preset = str(
                h3_preset or os.environ.get("COMFYUI_H3_DEFAULT_PRESET", "FAST")
            ).upper()
            actual_seed = int(seed) if seed is not None else uuid.uuid4().int & ((1 << 48) - 1)

            first_filename = None
            last_filename = None
            reference_filenames: list[str] = []
            final_prompt = str(prompt or "")
            if mode == "reference":
                final_prompt, reference_items = build_h3_reference_prompt(
                    final_prompt, reference_items
                )
                for index, item in enumerate(reference_items, start=1):
                    suffix = Path(str(item.path)).suffix.lower() or ".png"
                    remote_name = f"h3_{task_token}_ref_{index:02d}{suffix}"
                    reference_filenames.append(
                        await self._upload_path(str(item.path), remote_name)
                    )
            else:
                first_path = Path(str(image_path))
                first_remote = f"h3_{task_token}_first{first_path.suffix.lower() or '.png'}"
                first_filename = await self._upload_path(first_path, first_remote)
                if mode == "first_last":
                    last_path = Path(str(last_frame_path))
                    last_remote = f"h3_{task_token}_last{last_path.suffix.lower() or '.png'}"
                    last_filename = await self._upload_path(last_path, last_remote)

            workflow = build_h3_workflow(
                mode=mode,
                prompt=final_prompt,
                first_frame_filename=first_filename,
                last_frame_filename=last_filename,
                reference_filenames=reference_filenames,
                aspect_ratio=aspect_ratio,
                duration=duration,
                preset=preset,
                seed=actual_seed,
            )
            if on_log:
                on_log("MiniMax H3 Local：排队等待唯一GPU渲染位")
            async with _AsyncFileLock(self.lock_path):
                video_bytes, prompt_id = await self._execute_workflow(
                    workflow,
                    on_log=on_log,
                    on_progress=on_progress,
                )

            destination = Path(output_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(video_bytes)
            if str(audio_setting or "").strip().lower() in {"mute", "silent", "静音"}:
                await self._remove_audio(destination)
            generated_last_frame = destination.with_name(f"{destination.stem}_last.png")
            await self._extract_last_frame(destination, generated_last_frame)
            if on_progress:
                on_progress(1.0)
            if on_log:
                on_log("MiniMax H3 Local：视频和末帧已返回DramaClaw")
            return VideoGenResult(
                status=VideoGenStatus.DONE,
                video_path=str(destination),
                last_frame_path=str(generated_last_frame),
                task_id=prompt_id,
                provider_task_id=prompt_id,
                duration_seconds=float(duration),
            )
        except Exception as exc:  # noqa: BLE001
            message = self.friendly_error(exc)
            if on_log:
                on_log(message)
            return VideoGenResult(status=VideoGenStatus.FAILED, error=message)
