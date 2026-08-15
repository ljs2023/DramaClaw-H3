# DramaClaw × MiniMax H3 Local 部署与评测报告

首次部署：2026-08-05
本次复核与升级：2026-08-15
目标服务器：`192.168.3.9`

## 一、结论

DramaClaw 导演台已经升级到 v1.3.2，并在 Docker 中稳定运行；服务器原有的 MiniMax H3
继续作为独立本地视频后端工作。首帧、首尾帧、多参考图、静音输出和项目结果回传均已
经过真实任务验证。

这套导演台有实用性，最适合短剧分镜预演、角色/场景素材组织、5 秒镜头试拍、首尾帧
续拍和本地批量排队。它不是“一键生成成片”的替代品：12GB 显卡一次只能跑一个 H3
任务，长片仍需拆镜头，重要镜头仍需人工筛选、补帧、配音和剪辑。

本次也验证了 2026-08 的两条较新 H3 Turbo 社区方案。它们都能生成有效音视频，但在
RTX 4070 SUPER + 当前量化底模上没有达到接入门槛，因此暂不进入 C（生产接入）阶段。

## 二、当前生产状态

- DramaClaw Web：`http://192.168.3.9:8080`
- DramaClaw API：`http://192.168.3.9:8780`
- MiniMax H3/ComfyUI：`http://192.168.3.9:18189`
- DramaClaw 目录：`/data/DramaClaw`
- H3 目录：`/data/MiniMax-H3-ComfyUI`
- H3 Backend ID：`comfyui_h3`
- Docker 容器：`dramaclaw-web-1`、`dramaclaw-api-1`
- Docker 数据卷：`dramaclaw_ce-data`
- 生产分支：`codex/dramaclaw-h3-upgrade`
- 生产提交：`5144f5f2f58159e24356cc6d5caaacf616c95b41`
- Web 版本：DramaClaw v1.3.2

成功标志：Web 返回 HTTP 200，API `/healthz` 返回 HTTP 200，API 容器状态为
`healthy`，H3 `/system_stats` 返回 HTTP 200。

## 三、A 阶段完成内容

### 1. v1.3.2 升级

- 合并 DramaClaw v1.3.2，并保留本地 MiniMax H3 适配。
- 后端、前端和 Docker 镜像重新构建并部署。
- 原有 H3 模型、生产 ComfyUI 和 systemd 服务没有被覆盖。
- 两个 DramaClaw 容器继续使用 `unless-stopped` 自动恢复策略。

### 2. 本地 H3 回归修复

升级后发现并修复了两个真实问题：

1. v1.3.2 的视频模型目录配置会把本地 `comfyui_h3` 从导演台列表中隐藏。
2. 用户关闭“生成音频”时，H3 任务仍可能保留 AAC 音轨。

两个问题都先增加失败测试，再实现修复。最终真实静音任务输出只有 H.264 视频流，
没有音频流。

### 3. 自动测试

- Python 完整测试：`2266 passed, 16 skipped, 2 deselected`
- 前端完整测试：324 个测试文件，`2178 passed`
- H3 Turbo 基准工具测试：`5 passed`
- Ruff：通过
- `git diff --check`：通过

### 4. ComfyUI v0.33.1 隔离验证

实验副本位于 `/data/MiniMax-H3-ComfyUI-v0.33.1`，只在本机端口 18190 临时启动，
与生产 18189 分开。验证结果：

- ComfyUI 0.33.1 正常启动；Sage Attention 正常识别。
- 5 秒、640×640、16 步、带音频真实任务成功。
- 耗时 181.72 秒；EasyCache 跳过 9/16 步。
- 实验结束后 18190 已停止，生产 18189 保持在线。

### 5. 生产真实冒烟

最终任务 ID：`3ab3a0ae07d7415a`。

- 480×480，5 秒，TURBO 12 步，固定种子 `20260815`
- H3 实际执行 96.33 秒
- EasyCache 跳过 5/12 步
- 输出 480×480、24fps、约 5.17 秒
- 关闭音频时，输出只有 H.264 视频流
- SHA256：`65734eaff3f360a1ae66b25499c304d01443c56026a62f89f01f72350c41f71d`

## 四、备份与回滚保障

升级前备份位于：

```text
/data/backups/dramaclaw/20260815-pre-v132/
```

其中包含代码 bundle、数据卷备份、服务器内部配置副本和 systemd 单元。旧镜像保留为：

```text
dramaclaw-api:rollback-ec51283
dramaclaw-web:rollback-ec51283
```

配置文件和 `.env` 含隐私信息，只保存在服务器内部，未写入本报告或 Git。

## 五、B 阶段：最新 H3 加速查验

### 1. 查验的方案

| 方案 | 固定版本 | 说明 |
|---|---|---|
| ModelTC Minimax-H3-Turbo | Git `a7e148b8dc7db8ad976966060dcc022adf11fc8d`；HF `5d1d4829fe614c1b93fcfd9cc7718e9ba71f73e1` | 较新的 4/8 步 FL2VA、T2VA、Ref2VA LoRA 与原生 ComfyUI 工作流 |
| Larry ComfyUI-MiniMax-H3-Turbo | Git `4274783a23afcfdbea3b4876cb79effd6c510785`；HF `43a74557ac3f6539db8e0f2a959d03feb7a81480` | 面向量化/裁剪底模的专用 LoRA 加载器与采样器 |

两者均为 Apache-2.0 社区项目，不是 MiniMax 官方生产加速发布。

已校验权重：

- ModelTC 8 步 ComfyUI 权重：1,956,193,000 字节；SHA256
  `2339acdf19bfe123f46b971ea35d367a84adb85de43627e1eceafa5a5b2b111e`
- Larry v4 step600 EMA：779,849,816 字节；SHA256
  `5f3a626cd72c93a8b9318d6760c510bc5092d2ab13aaba1f932c5bab07a416d3`

### 2. 固定条件

- GPU：NVIDIA RTX 4070 SUPER 12GB
- 驱动：570.133.20
- PyTorch：2.11.0+cu128
- ComfyUI：0.33.1
- 画面：864×480
- 时长：124 帧，约 5.17 秒
- 相同提示词、相同首帧、固定种子组 `20260815/816/817`
- 基线：现有 12 步 TURBO + EasyCache 0.28

### 3. 实测结果

| 路线 | 有效次数 | 中位耗时 | 相对现有基线 | 峰值显存 | 结论 |
|---|---:|---:|---:|---:|---|
| 现有 12 步 EasyCache | 3 | 159.74 秒 | 基线 | 8608MB | 稳定 |
| ModelTC 4 步 | 3 | 115.13 秒 | 快 27.9% | 8544MB | 可用，但未达 35% 门槛 |
| ModelTC 6 步 | 3 | 159.92 秒 | 慢 0.1% | 8544MB | 无速度收益 |
| ModelTC 8 步 | 3 | 205.25 秒 | 慢 28.5% | 8544MB；冷启动 10366MB | 不适合当前机器 |
| Larry v4 6 步 | 1 | 172.81 秒 | 慢 8.2% | 10352MB | 交叉验证未获收益 |

所有有效样本均为 864×480、约 5.17 秒，并同时包含 H.264 视频流和 AAC 双声道音频流。
4 步抽帧可见人物和手部，但镜头起始构图与基线差异较大；音轨存在不等于对白一定
完美同步，正式采用前仍需人工试听。

最初两条 0.04 秒“结果”被确认是 ComfyUI 缓存复用，已从统计中剔除；基准工具随后
增加固定多种子机制和自动测试，避免再次误判。

### 4. CUDA 13 加速条件

ComfyUI 0.33.1 提示：部分 comfy-kitchen 优化 CUDA 操作需要 PyTorch cu130。NVIDIA
CUDA 13.0 文档要求 Linux 驱动至少 580.65.06，而服务器当前为 570.133.20。

因此，CUDA 13 测试不是简单换一个 Python 包，它需要系统级显卡驱动升级并通常需要
重启服务器，会同时影响 H3、ComfyUI、Llama 等显卡服务。本次没有擅自升级驱动。

## 六、导演台实用性与推荐用法

### 适合

- 把剧本拆成 5 秒左右的镜头卡，快速验证构图、动作和镜头方向。
- 用角色、场景、道具参考图集中管理视觉资产。
- 使用首尾帧约束转场；用上一镜头末帧续拍下一镜头。
- 先批量生成候选，再人工挑选少量镜头进入正式剪辑。
- 本地运行，避免把未公开素材发送到云端视频服务。

### 不适合直接承担

- 一次生成数分钟连续成片。
- 不经人工挑选就保证角色脸、手指、口型和道具完全稳定。
- H3、ComfyUI、Llama 等多个大模型同时抢占同一张 12GB 显卡。
- 用 480P 原片直接作为最终发行母版。

### 推荐生产流程

```text
剧本/分镜
  → 角色与场景定稿
  → TURBO 5秒试构图
  → FAST 重做入选镜头
  → 重要镜头用 QUALITY 单独终稿
  → 末帧续拍
  → 补帧/放大/配音/字幕/剪辑
```

推荐把 70% 的算力用于 TURBO 候选、25% 用于 FAST 入选镜头、5% 用于 QUALITY
关键镜头。这样比所有镜头都跑最高质量更实用。

## 七、是否进入 C

建议：**现在不进入 C，不把社区 Turbo 接到生产导演台。**

理由：4 步只快 27.9%，没有达到预设的 35% 门槛，且镜头轨迹变化较明显；6/8 步和
Larry v4 没有端到端速度收益。现有 12 步 EasyCache 更稳妥。

以后进入 C 有两个可选触发条件：

1. 上游发布新的量化底模专用 LoRA，在相同测试中达到至少 35% 加速且人工试听通过。
2. 安排独立维护窗口，先备份并升级 NVIDIA 驱动，再在隔离环境测试 cu130；通过后才
   考虑生产迁移。

## 八、成功标志与排查

### 日常成功标志

- 网页可打开，模型列表出现 `MiniMax H3 Local`。
- 任务从“排队”进入“生成”，最后返回视频和末帧。
- 服务器 `docker ps` 显示 API 为 `healthy`。
- `systemctl is-active minimax-h3-comfyui.service` 返回 `active`。

### 常见排查

```bash
cd /data/DramaClaw
/usr/local/bin/docker-compose -p dramaclaw ps
curl -f http://127.0.0.1:8780/healthz
systemctl is-active minimax-h3-comfyui.service
curl -f http://127.0.0.1:18189/system_stats
```

若显存不足：等待其他 GPU 任务结束，先用 480P、5 秒、TURBO；不要同时在生产
ComfyUI 手动运行大型工作流。若网页正常但模型列表没有 H3，刷新网页并检查 API 日志，
不要直接重装 H3。

## 九、相关文档与来源

- 本地使用说明：`MiniMax-H3-Local-使用说明.md`
- ModelTC：<https://github.com/ModelTC/Minimax-H3-Turbo>
- Larry：<https://github.com/Larryvrh/ComfyUI-MiniMax-H3-Turbo>
- Comfy Kitchen：<https://github.com/Comfy-Org/comfy-kitchen>
- PyTorch cu130 安装信息：<https://pytorch.org/get-started/previous-versions/>
- NVIDIA CUDA 13.0 发布说明：<https://docs.nvidia.com/cuda/archive/13.0.0/cuda-toolkit-release-notes/index.html>
