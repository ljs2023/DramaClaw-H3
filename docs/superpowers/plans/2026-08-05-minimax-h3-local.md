# MiniMax H3 Local Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production-ready local MiniMax H3 ComfyUI video backend to DramaClaw and deploy it with Docker on 192.168.3.9.

**Architecture:** A focused `MiniMaxH3ComfyUIGenerator` owns H3 workflow mutation and ComfyUI communication. Existing runners and Freezone feed it through the current `VideoGeneratorBase` and `ShotReference` contracts; frontend capabilities come from the backend model catalog.

**Tech Stack:** Python 3.11, aiohttp, websockets, FastAPI, React/TypeScript, Vitest, pytest, Docker Compose, ComfyUI API.

## Global Constraints

- Do not modify, restart, replace, or redownload the working MiniMax H3 service or models.
- Do not change the existing `ComfyUIVideoGenerator`; use backend id `comfyui_h3`.
- Keep H3 concurrency at exactly one task and never stop other GPU services.
- Support only first frame, first+last frame, image reference, 480p, 5/8/10/15 seconds, and native-audio/mute.
- Use test-first RED/GREEN cycles for production behavior.

---

### Task 1: H3 domain mapping and workflow templates

**Files:**
- Create: `src/novelvideo/generators/comfyui_h3_generator.py`
- Create: `src/novelvideo/generators/h3_workflows/minimax_h3_fl2va_api.json`
- Create: `src/novelvideo/generators/h3_workflows/minimax_h3_ref2va_api.json`
- Test: `tests/test_comfyui_h3_generator.py`

**Interfaces:**
- Produces: `H3Preset`, `H3Reference`, `h3_frame_count()`, `h3_dimensions()`, `build_h3_reference_prompt()`, `MiniMaxH3ComfyUIGenerator`.

- [ ] Write failing table tests for exact preset, aspect ratio, frame count, mode and Picture mapping behavior.
- [ ] Run `uv run pytest tests/test_comfyui_h3_generator.py -q` and verify failures are missing H3 symbols.
- [ ] Add minimal pure mapping functions and load the two API JSON templates exported from the verified server workflows.
- [ ] Re-run the focused test and verify it passes.

### Task 2: ComfyUI transport and result lifecycle

**Files:**
- Modify: `src/novelvideo/generators/comfyui_h3_generator.py`
- Test: `tests/test_comfyui_h3_generator.py`

**Interfaces:**
- Consumes: two workflow templates and H3 mapping functions.
- Produces: async `generate(...) -> VideoGenResult` and `health_check() -> dict`.

- [ ] Add failing tests for unique uploads, prompt injection, one-task semaphore, output fields `video/videos/gifs/images`, friendly errors, mute handling and last-frame result.
- [ ] Verify each test fails because the transport behavior is absent.
- [ ] Implement upload, queue, WebSocket progress, history parsing, download, ffmpeg mute, ffmpeg last-frame extraction and friendly error mapping.
- [ ] Re-run focused tests and keep all passing.

### Task 3: Backend registration and mainline propagation

**Files:**
- Modify: `src/novelvideo/generators/video_generator.py`
- Modify: `src/novelvideo/generators/__init__.py`
- Modify: `src/novelvideo/config.py`
- Modify: `src/novelvideo/task_backend/runners/video.py`
- Test: `tests/test_comfyui_h3_registration.py`

**Interfaces:**
- Produces: `VideoBackend.COMFYUI_H3`, `create_video_generator("comfyui_h3")`, H3 environment configuration and runner passthrough.

- [ ] Write failing registration/config/runner tests.
- [ ] Verify RED.
- [ ] Register the new enum/factory branch and pass `references`, `h3_preset`, `seed`, `audio_setting` through existing task payloads.
- [ ] Verify focused and existing video runner tests pass.

### Task 4: Freezone catalog and capability contract

**Files:**
- Modify: `src/novelvideo/freezone/video_node.py`
- Modify: `src/novelvideo/api/ops.ts` not applicable; frontend API type is in `frontend/src/api/ops.ts`
- Modify: `frontend/src/api/ops.ts`
- Modify: `frontend/src/features/canvas/ui/ProviderModelPicker.tsx`
- Modify: `frontend/src/features/canvas/nodes/shared/videoModelCapabilities.ts`
- Test: `tests/test_freezone_h3_video_backend.py`
- Test: `frontend/src/__tests__/features/canvas/video-model-capabilities.test.ts`

**Interfaces:**
- Produces: model catalog entry `comfyui_h3` with local provider, supported mode list and reference/duration/resolution limits.

- [ ] Write failing backend catalog/resolver and frontend capability tests.
- [ ] Verify pytest and Vitest RED results.
- [ ] Add H3 model catalog entry, resolver acceptance, provider type and H3-specific media guards.
- [ ] Verify focused backend/frontend tests pass.

### Task 5: Deployment configuration and documentation

**Files:**
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Create: `MiniMax-H3-Local-使用说明.md`
- Create: `DramaClaw-H3-部署报告.md`

**Interfaces:**
- Produces: source-build Compose deployment on ports 8080/8780 with H3 at host `192.168.3.9:18189`.

- [ ] Add non-secret H3 environment defaults and set `VIDEO_BACKEND=comfyui_h3` in the server-only `.env`.
- [ ] Run Python tests, frontend tests, lint and secret scan; fix only regressions caused by this feature.
- [ ] Build and start with `docker-compose -p dramaclaw up -d --build`.
- [ ] Verify `curl http://127.0.0.1:8780` and `curl http://127.0.0.1:8080` respond and H3 `/system_stats` remains healthy.
- [ ] Run TURBO first-frame, first+last and two-reference tests with public sample assets; verify output MP4, native audio, video-pool entry and last frame.
- [ ] Run one FAST 5-second quality test, record timing and known issues in the deployment report.
- [ ] Commit final changes as `feat(video): add local MiniMax H3 ComfyUI backend` without push or merge.
