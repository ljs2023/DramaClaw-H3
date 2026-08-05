# DramaClaw × MiniMax H3 Local 部署报告

部署日期：2026-08-05  
目标服务器：`192.168.3.9`

## 部署结果

- DramaClaw 已使用 Docker 部署，Web 与 API 均正常。
- 现有 MiniMax H3/ComfyUI 未重装、未更新、未修改模型，服务保持独立运行。
- DramaClaw 已增加本地后端 `comfyui_h3`，可自动选择首帧/首尾帧与参考图工作流。
- GPU 任务使用跨进程文件锁串行执行，默认并发数为 1。
- 站点配置文件权限为 600，报告和 Git 中不含账号、密码或站点密钥。

## 版本和目录

- Git 分支：`feature/minimax-h3-local`
- 部署前提交：`a506de7421905ca55d3ed4f9ae5d5d9648105d71`
- H3 后端主体提交：`8b7c0ad1`（`feat(video): add local MiniMax H3 ComfyUI backend`）
- DramaClaw 目录：`/data/DramaClaw`
- H3 目录：`/data/MiniMax-H3-ComfyUI`

## 端口和容器

- Web：`8080`
- API：`8780`
- H3/ComfyUI：`192.168.3.9:18189`
- H3 Backend ID：`comfyui_h3`
- Docker 容器：`dramaclaw-web-1`、`dramaclaw-api-1`
- Docker 项目名：`dramaclaw`
- 数据卷：`dramaclaw_ce-data`

两个 DramaClaw 容器的重启策略均为 `unless-stopped`。

## H3 API 工作流

- `src/novelvideo/generators/h3_workflows/minimax_h3_fl2va_api.json`
- `src/novelvideo/generators/h3_workflows/minimax_h3_ref2va_api.json`

工作流来自服务器现有 H3 官方工作流的 API 格式导出。H3 健康检查只访问
`/system_stats`，不会为了探活而加载模型。

## 主要新增文件

- `src/novelvideo/generators/comfyui_h3_generator.py`
- `src/novelvideo/generators/h3_workflows/minimax_h3_fl2va_api.json`
- `src/novelvideo/generators/h3_workflows/minimax_h3_ref2va_api.json`
- `tests/test_comfyui_h3_generator.py`
- `tests/test_comfyui_h3_registration.py`
- `tests/test_freezone_h3_video_backend.py`
- `frontend/Dockerfile.prebuilt`
- `MiniMax-H3-Local-使用说明.md`
- `DramaClaw-H3-部署报告.md`

## 主要修改范围

- 视频后端注册、配置和时长限制
- 自由画布的视频模型目录、参数、输入能力和任务执行
- 前端模型选择器和能力判断
- `.env.example` 的 H3 默认配置
- Docker 网页构建网络容错与容器自动恢复策略

没有修改服务器上的 H3 工作流、模型权重、ComfyUI 插件或 H3 systemd 服务。

## 自动测试

- Python H3/Freezone/Runner 相关测试：`63 passed`
- 前端 H3 能力测试：`60 passed`
- Ruff：通过
- TypeScript 类型检查：通过
- `git diff --check`：通过

## 真实生成测试

测试素材来自公开图片服务，不含用户私人素材。

| 测试 | 配置 | 结果 | 观察耗时 |
|---|---|---|---|
| 首帧横屏 | 864×480，5 秒，TURBO | 成功，视频和末帧回传 | 约 2 分钟 |
| 首尾帧横屏 | 864×480，5 秒，TURBO | 成功 | 约 2–3 分钟 |
| 双参考横屏 | 2 张图，864×480，5 秒，TURBO | 成功 | 约 2–3 分钟 |
| 高质量路径 | 864×480，5 秒，QUALITY 20 步 | 成功，EasyCache 关闭 | 约 5 分钟 |
| 正式竖屏首帧 | 480×864，5 秒，FAST 16 步 | 成功 | 约 3 分钟 |
| 竖屏首尾帧续拍 | Shot01 末帧→Shot02，480×864，5 秒，TURBO | 成功 | 约 2–3 分钟 |
| 双参考竖屏 | 2 张图，480×864，5 秒，TURBO/MATCH | 成功 | 约 2–3 分钟 |

参考图提示词映射验证为：

```text
<Picture 1> CHARACTER: 测试人物
<Picture 2> SCENE: 测试环境
```

输出文件位于容器数据卷的 `/data/output/h3-smoke`。视频经 `ffprobe` 验证只有
视频流、约 5.17 秒；静音设置生效，每个视频均生成对应 `_last.png`。

## 服务验收

- `http://127.0.0.1:8080/`：HTTP 200
- `http://127.0.0.1:8780/api/v1/config`：HTTP 200
- H3 systemd 状态：`active`
- H3 `/system_stats`：成功
- 容器内 H3 健康检查：`online=true`
- 前端构建成品包含 `MiniMax H3 Local`

## 部署期间处理的问题

1. 服务器的 `docker0` 网桥缺失。确认现有容器使用主机网络且有自动恢复策略后，
   短暂重启 Docker；`mihomo`、`xingban` 自动恢复，H3 未受影响。
2. 服务器外网下载 npm 大包超时。后端镜像在服务器正常构建；前端在本机完成同一
   Vite 生产构建，再使用 `frontend/Dockerfile.prebuilt` 封装为 Nginx Docker 镜像。
3. Dockerfile 增加 pnpm 下载缓存、延长超时和重试，供后续服务器原生重建使用。

## 已知限制

- 只开放实际验收过的 480P；没有开放 720P/1080P。
- 只支持 9:16、16:9、1:1 和 5/8/10/15 秒。
- 参考图片最多 9 张；不接受视频或音频参考。
- 单张 12GB GPU 默认一次只执行一个 H3 任务，多个任务会排队。
- 本次使用公开素材完成技术验收；人物脸部一致性仍需用户自己的三视图做最终验收。
- 未对上游仓库执行 push、merge 或 rebase。

## 回滚

```bash
cd /data/DramaClaw
/usr/local/bin/docker-compose -p dramaclaw down
git switch -c rollback-before-h3 a506de7421905ca55d3ed4f9ae5d5d9648105d71
/usr/local/bin/docker-compose -p dramaclaw up -d --build
```

不要给 `down` 增加 `-v`，以免删除 DramaClaw 数据卷。回滚只影响 DramaClaw，
不会修改或停止 H3。

