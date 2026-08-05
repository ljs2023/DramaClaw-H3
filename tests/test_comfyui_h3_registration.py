from novelvideo.generators.comfyui_h3_generator import MiniMaxH3ComfyUIGenerator
from novelvideo.generators.video_generator import (
    VideoBackend,
    create_video_generator,
)


def test_comfyui_h3_backend_has_real_factory_registration():
    assert VideoBackend.COMFYUI_H3.value == "comfyui_h3"
    generator = create_video_generator("comfyui_h3")
    assert isinstance(generator, MiniMaxH3ComfyUIGenerator)


def test_h3_factory_reads_dedicated_environment_without_cloud_key(monkeypatch):
    monkeypatch.setenv("COMFYUI_H3_ADDRESS", "192.168.3.9:18189")
    monkeypatch.setenv("COMFYUI_H3_USE_SSL", "false")
    monkeypatch.setenv("COMFYUI_H3_TIMEOUT", "1800")
    monkeypatch.delenv("NEWAPI_API_KEY", raising=False)
    generator = create_video_generator("comfyui_h3")
    assert generator.server_address == "192.168.3.9:18189"
    assert generator.use_ssl is False
    assert generator.timeout == 1800
