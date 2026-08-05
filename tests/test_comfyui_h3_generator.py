from pathlib import Path
from types import SimpleNamespace
import asyncio

import pytest

from novelvideo.generators.comfyui_h3_generator import (
    H3_PRESETS,
    MiniMaxH3ComfyUIGenerator,
    build_h3_workflow,
    build_h3_reference_prompt,
    h3_dimensions,
    h3_frame_count,
    parse_h3_video_output,
    select_h3_mode,
)


@pytest.mark.parametrize(
    ("name", "steps", "easycache", "ref_image_size"),
    [
        ("QUALITY", 20, 0.0, "max"),
        ("FAST", 16, 0.20, "max"),
        ("TURBO", 12, 0.28, "match"),
    ],
)
def test_h3_presets_match_verified_server_settings(name, steps, easycache, ref_image_size):
    preset = H3_PRESETS[name]
    assert (preset.steps, preset.easycache, preset.ref_image_size) == (
        steps,
        easycache,
        ref_image_size,
    )


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [("9:16", (480, 864)), ("16:9", (864, 480)), ("1:1", (480, 480))],
)
def test_h3_dimensions_only_expose_verified_480p_ratios(ratio, expected):
    assert h3_dimensions(ratio) == expected


def test_h3_dimensions_reject_unverified_resolution():
    with pytest.raises(ValueError, match="仅支持"):
        h3_dimensions("1920x1080")


@pytest.mark.parametrize(
    ("seconds", "frames"),
    [(5, 124), (8, 192), (10, 243), (15, 362)],
)
def test_h3_frame_count_uses_17k_plus_5_alignment(seconds, frames):
    assert h3_frame_count(seconds) == frames
    assert (frames - 5) % 17 == 0


def test_h3_frame_count_rejects_unverified_duration():
    with pytest.raises(ValueError, match="5、8、10、15"):
        h3_frame_count(6)


def test_h3_mode_prefers_references_then_first_last_then_first_frame():
    reference = SimpleNamespace(type="image", path="character.png", role="角色参考")
    assert select_h3_mode("first.png", "last.png", [reference]) == "reference"
    assert select_h3_mode("first.png", "last.png", []) == "first_last"
    assert select_h3_mode("first.png", None, []) == "first_frame"


def test_h3_mode_rejects_missing_images():
    with pytest.raises(ValueError, match="需要首帧或参考图片"):
        select_h3_mode(None, None, [])


def test_reference_prompt_compacts_picture_numbers_and_preserves_roles():
    references = [
        SimpleNamespace(type="image", path="/assets/李大根三视图.png", role="角色参考：李大根"),
        SimpleNamespace(type="image", path="", role="关闭的槽位"),
        SimpleNamespace(type="image", path="/assets/18号房.png", role="场景参考：18号房"),
        SimpleNamespace(type="image", path="/assets/手机.png", role="道具参考：手机"),
    ]

    prompt, compact = build_h3_reference_prompt("两人进入房间", references)

    assert [Path(item.path).name for item in compact] == [
        "李大根三视图.png",
        "18号房.png",
        "手机.png",
    ]
    assert "<Picture 1> CHARACTER: 李大根" in prompt
    assert "<Picture 2> SCENE: 18号房" in prompt
    assert "<Picture 3> PROP: 手机" in prompt
    assert "Picture 4" not in prompt
    assert prompt.endswith("两人进入房间")


def test_reference_prompt_rejects_more_than_nine_images():
    references = [
        SimpleNamespace(type="image", path=f"/tmp/ref-{index}.png", role="角色参考")
        for index in range(10)
    ]
    with pytest.raises(ValueError, match="最多允许9张参考图片"):
        build_h3_reference_prompt("prompt", references)


@pytest.mark.parametrize("field", ["video", "videos", "gifs", "images"])
def test_h3_output_parser_accepts_current_and_legacy_comfyui_fields(field):
    history = {
        "prompt-1": {
            "outputs": {
                "save": {
                    field: [
                        {
                            "filename": "video/MiniMax_H3/result.mp4",
                            "subfolder": "video/MiniMax_H3",
                            "type": "output",
                        }
                    ]
                }
            }
        }
    }
    assert parse_h3_video_output(history, "prompt-1") == {
        "filename": "video/MiniMax_H3/result.mp4",
        "subfolder": "video/MiniMax_H3",
        "type": "output",
    }


def test_h3_output_parser_rejects_history_without_video():
    with pytest.raises(ValueError, match="未找到视频输出"):
        parse_h3_video_output({"prompt-1": {"outputs": {}}}, "prompt-1")


def _node(workflow, class_type):
    return next(node for node in workflow.values() if node["class_type"] == class_type)


def test_fl2va_workflow_injects_first_last_and_turbo_parameters():
    workflow = build_h3_workflow(
        mode="first_last",
        prompt="镜头缓慢推进，保留原生环境声",
        first_frame_filename="h3_task_first.png",
        last_frame_filename="h3_task_last.png",
        aspect_ratio="9:16",
        duration=5,
        preset="TURBO",
        seed=42,
    )

    h3_node = _node(workflow, "MiniMaxH3ImageToVideo")
    assert h3_node["inputs"]["prompt"] == "镜头缓慢推进，保留原生环境声"
    assert (h3_node["inputs"]["width"], h3_node["inputs"]["height"]) == (480, 864)
    assert h3_node["inputs"]["length"] == 124
    assert h3_node["inputs"]["first_frame"] == ["114", 0]
    assert h3_node["inputs"]["last_frame"] == ["116", 0]
    assert workflow["114"]["inputs"]["image"] == "h3_task_first.png"
    assert workflow["116"]["inputs"]["image"] == "h3_task_last.png"
    assert _node(workflow, "BasicScheduler")["inputs"]["steps"] == 12
    assert _node(workflow, "EasyCache")["inputs"]["reuse_threshold"] == 0.28
    assert _node(workflow, "RandomNoise")["inputs"]["noise_seed"] == 42


def test_first_frame_workflow_removes_optional_last_frame_node():
    workflow = build_h3_workflow(
        mode="first_frame",
        prompt="自然转身",
        first_frame_filename="first.png",
        aspect_ratio="16:9",
        duration=8,
        preset="QUALITY",
        seed=7,
    )
    h3_node = _node(workflow, "MiniMaxH3ImageToVideo")
    assert "last_frame" not in h3_node["inputs"]
    assert "116" not in workflow
    assert not any(node["class_type"] == "EasyCache" for node in workflow.values())
    assert _node(workflow, "BasicScheduler")["inputs"]["model"] == ["105:6", 0]


def test_ref2va_workflow_keeps_exact_reference_order_and_removes_unused_slots():
    workflow = build_h3_workflow(
        mode="reference",
        prompt="<Picture 1> CHARACTER: 甲\n<Picture 2> SCENE: 房间",
        reference_filenames=["ref-a.png", "ref-room.png"],
        aspect_ratio="1:1",
        duration=10,
        preset="FAST",
        seed=99,
    )
    h3_node = _node(workflow, "MiniMaxH3ReferenceToVideo")
    assert h3_node["inputs"]["prompt"].startswith("<Picture 1>")
    assert (h3_node["inputs"]["width"], h3_node["inputs"]["height"]) == (480, 480)
    assert h3_node["inputs"]["length"] == 243
    assert h3_node["inputs"]["ref_image_size"] == "max"
    assert h3_node["inputs"]["ref_images.ref_image_0"] == ["200", 0]
    assert h3_node["inputs"]["ref_images.ref_image_1"] == ["201", 0]
    assert "ref_images.ref_image_2" not in h3_node["inputs"]
    assert workflow["200"]["inputs"]["image"] == "ref-a.png"
    assert workflow["201"]["inputs"]["image"] == "ref-room.png"
    assert not any(node.get("inputs", {}).get("image") == "__REFERENCE_3__.png" for node in workflow.values())


class _FakeH3Generator(MiniMaxH3ComfyUIGenerator):
    active = 0
    max_active = 0

    def __init__(self, tmp_path):
        super().__init__(server_address="127.0.0.1:18189", lock_path=tmp_path / "h3.lock")
        self.uploaded = []
        self.workflow = None
        self.muted = False

    async def _upload_path(self, path, filename):
        self.uploaded.append((Path(path).name, filename))
        return filename

    async def _execute_workflow(self, workflow, *, on_log=None, on_progress=None):
        type(self).active += 1
        type(self).max_active = max(type(self).max_active, type(self).active)
        self.workflow = workflow
        await asyncio.sleep(0.03)
        type(self).active -= 1
        return b"fake-mp4", "prompt-test"

    async def _remove_audio(self, video_path):
        self.muted = True

    async def _extract_last_frame(self, video_path, last_frame_path):
        Path(last_frame_path).write_bytes(b"fake-png")


@pytest.mark.asyncio
async def test_h3_generate_uploads_compact_references_and_returns_last_frame(tmp_path):
    first = tmp_path / "character.png"
    scene = tmp_path / "scene.png"
    first.write_bytes(b"image-a")
    scene.write_bytes(b"image-b")
    output = tmp_path / "result.mp4"
    generator = _FakeH3Generator(tmp_path)
    references = [
        SimpleNamespace(type="image", path=str(first), role="角色参考：小明"),
        SimpleNamespace(type="image", path="", role="关闭"),
        SimpleNamespace(type="image", path=str(scene), role="场景参考：教室"),
    ]

    result = await generator.generate(
        image_path=None,
        prompt="小明走进教室",
        output_path=str(output),
        references=references,
        aspect_ratio="9:16",
        duration=5,
        h3_preset="TURBO",
        seed=123,
        audio_setting="mute",
    )

    assert result.status.value == "done"
    assert output.read_bytes() == b"fake-mp4"
    assert result.last_frame_path == str(tmp_path / "result_last.png")
    assert Path(result.last_frame_path).read_bytes() == b"fake-png"
    assert generator.muted is True
    assert [name for name, _remote in generator.uploaded] == ["character.png", "scene.png"]
    assert [remote.split("_ref_")[-1] for _name, remote in generator.uploaded] == [
        "01.png",
        "02.png",
    ]
    h3_node = _node(generator.workflow, "MiniMaxH3ReferenceToVideo")
    assert "<Picture 1> CHARACTER: 小明" in h3_node["inputs"]["prompt"]
    assert "<Picture 2> SCENE: 教室" in h3_node["inputs"]["prompt"]


@pytest.mark.asyncio
async def test_h3_file_lock_serializes_generators_for_12gb_gpu(tmp_path):
    _FakeH3Generator.active = 0
    _FakeH3Generator.max_active = 0
    image = tmp_path / "first.png"
    image.write_bytes(b"image")
    first_generator = _FakeH3Generator(tmp_path)
    second_generator = _FakeH3Generator(tmp_path)

    await asyncio.gather(
        first_generator.generate(
            image_path=str(image), prompt="A", output_path=str(tmp_path / "a.mp4")
        ),
        second_generator.generate(
            image_path=str(image), prompt="B", output_path=str(tmp_path / "b.mp4")
        ),
    )

    assert _FakeH3Generator.max_active == 1


@pytest.mark.parametrize(
    ("raw", "friendly"),
    [
        ("Cannot connect to host", "本地渲染服务当前不可访问"),
        ("CUDA out of memory", "GPU显存不足"),
        ("execution_error: invalid prompt", "工作流执行失败"),
    ],
)
def test_h3_errors_are_friendly_for_non_programmers(raw, friendly):
    assert friendly in MiniMaxH3ComfyUIGenerator.friendly_error(RuntimeError(raw))
