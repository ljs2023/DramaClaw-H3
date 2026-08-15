from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.freezone.jobs import run_freezone_video_gen
from novelvideo.api.routes.freezone import _resolve_catalog_request
from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.freezone.video_node import (
    get_freezone_video_model_options,
    normalize_video_resolution_for_backend,
    resolve_freezone_video_backend,
)
from novelvideo.generators.video_generator import VideoGenResult, VideoGenStatus


def test_freezone_catalog_exposes_real_minimax_h3_capabilities():
    h3 = next(
        item
        for item in get_freezone_video_model_options()
        if item["id"] == "comfyui_h3"
    )
    expected = {
        "id": "comfyui_h3",
        "providerId": "local",
        "provider": "local",
        "apiModel": "comfyui_h3",
        "api_model": "comfyui_h3",
        "label": "MiniMax H3 Local",
        "backend": "comfyui_h3",
        "resolutionOptions": ["480p"],
        "resolution_options": ["480p"],
        "minDuration": 5,
        "min_duration": 5,
        "maxDuration": 15,
        "max_duration": 15,
        "ratioOptions": ["9:16", "16:9", "1:1"],
        "ratio_options": ["9:16", "16:9", "1:1"],
        "supportedModes": [
            "first_frame",
            "image_to_video",
            "first_last_frame",
            "image_reference",
        ],
        "supported_modes": [
            "first_frame",
            "image_to_video",
            "first_last_frame",
            "image_reference",
        ],
        "referenceImageMax": 9,
        "reference_image_max": 9,
        "referenceVideoMax": 0,
        "reference_video_max": 0,
        "referenceAudioMax": 0,
        "reference_audio_max": 0,
    }
    assert {key: h3[key] for key in expected} == expected
    assert [item["key"] for item in h3["request"]["parameters"]] == [
        "h3_preset",
        "seed",
    ]


def test_freezone_resolves_h3_without_seedance_alias_and_locks_480p():
    assert resolve_freezone_video_backend("comfyui_h3") == "comfyui_h3"
    assert resolve_freezone_video_backend("MiniMax H3 Local") == "comfyui_h3"
    assert normalize_video_resolution_for_backend("comfyui_h3", "1080p") == "480p"


@pytest.mark.asyncio
async def test_freezone_video_models_keeps_local_h3_with_service_catalog(monkeypatch):
    async def resolve_project(*_args, **_kwargs):
        return SimpleNamespace()

    async def service_catalog(media_type):
        assert media_type == "video"
        return [{"id": "cloud-video", "apiModel": "cloud-video"}]

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", resolve_project)
    monkeypatch.setattr(freezone_routes, "_ee_media_model_catalog", service_catalog)

    result = await freezone_routes.freezone_video_models(
        project="project-h3-local",
        user={"username": "local"},
    )

    assert [item["id"] for item in result["data"]] == [
        "cloud-video",
        "comfyui_h3",
    ]


@pytest.mark.asyncio
async def test_h3_request_resolves_with_service_catalog_present(monkeypatch):
    async def service_catalog(media_type):
        assert media_type == "video"
        return [{"id": "cloud-video", "apiModel": "cloud-video"}]

    monkeypatch.setattr(freezone_routes, "_ee_media_model_catalog", service_catalog)

    schema, values, capabilities = await _resolve_catalog_request(
        "video",
        "comfyui_h3",
        {"h3_preset": "TURBO", "seed": 88},
        mode="first_frame",
    )

    assert schema["endpoint"] == "video/generations"
    assert values == {"h3_preset": "TURBO", "seed": 88}
    assert capabilities["id"] == "comfyui_h3"


@pytest.mark.asyncio
async def test_freezone_h3_first_last_uses_fl2va_inputs_not_reference_mode(monkeypatch, tmp_path):
    captured = {}

    class FakeGenerator:
        async def generate(self, **kwargs):
            captured.update(kwargs)
            Path(kwargs["output_path"]).write_bytes(b"mp4")
            return VideoGenResult(status=VideoGenStatus.DONE, video_path=kwargs["output_path"])

    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda **_kwargs: FakeGenerator(),
    )
    first = tmp_path / "first.png"
    last = tmp_path / "last.png"
    first.write_bytes(b"first")
    last.write_bytes(b"last")

    await run_freezone_video_gen(
        project_dir=tmp_path,
        job_id="h3-first-last",
        prompt="过渡镜头",
        reference_items=[
            {"type": "image", "path": str(first), "role": "首帧"},
            {"type": "image", "path": str(last), "role": "尾帧"},
        ],
        backend="comfyui_h3",
        last_frame_path=str(last),
        gen_mode="first_last_frame",
        model_params={"h3_preset": "TURBO", "seed": 88},
    )

    assert captured["image_path"] == str(first)
    assert captured["last_frame_path"] == str(last)
    assert captured["references"] == []
    assert captured["h3_preset"] == "TURBO"
    assert captured["seed"] == 88
    assert captured["audio_setting"] == "mute"


@pytest.mark.asyncio
async def test_ce_h3_model_params_accept_quality_and_fixed_seed(monkeypatch):
    async def no_enterprise_catalog(_media_type):
        return None

    monkeypatch.setattr(
        "novelvideo.api.routes.freezone._ee_media_model_catalog",
        no_enterprise_catalog,
    )
    schema, values, capabilities = await _resolve_catalog_request(
        "video",
        "comfyui_h3",
        {"h3_preset": "TURBO", "seed": 88},
        mode="first_frame",
    )
    assert schema["endpoint"] == "video/generations"
    assert values == {"h3_preset": "TURBO", "seed": 88}
    assert capabilities["supportedModes"] == [
        "first_frame",
        "image_to_video",
        "first_last_frame",
        "image_reference",
    ]


@pytest.mark.asyncio
async def test_local_h3_project_task_skips_cloud_credit_model_resolution(
    monkeypatch, tmp_path
):
    from novelvideo.api.routes import freezone as freezone_routes

    captured = {}

    def unexpected_cloud_billing(_params):
        raise AssertionError("local H3 must not resolve a cloud credit model")

    class FakeTaskBackend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                task_state=SimpleNamespace(task_id="task-h3-local"),
                backend="inline",
                queue=None,
            )

    monkeypatch.setattr(
        "novelvideo.api.routes.model_credits.freezone_video_generate_task_billing",
        unexpected_cloud_billing,
    )
    monkeypatch.setattr(
        "novelvideo.api.routes.freezone.get_task_backend",
        lambda: FakeTaskBackend(),
    )

    response = await freezone_routes._start_or_enqueue_freezone_video_gen(
        ctx=SimpleNamespace(project_id="project-h3-local"),
        username="local",
        project="H3_Public_Smoke",
        project_dir=tmp_path,
        output_dir=str(tmp_path / "output"),
        job_id="job-h3-local",
        prompt="test",
        reference_items=[],
        aspect_ratio="9:16",
        resolution="480p",
        duration_seconds=5,
        generate_audio=False,
        human_review=False,
        scene_optimize=None,
        backend="comfyui_h3",
    )

    assert response["ok"] is True
    assert captured["payload"]["backend"] == "comfyui_h3"
    assert "billing" not in captured["payload"]
