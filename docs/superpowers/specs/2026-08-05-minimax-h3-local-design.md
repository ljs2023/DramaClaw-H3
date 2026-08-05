# MiniMax H3 Local 后端设计

## 目标

在不修改 `/data/MiniMax-H3-ComfyUI` 服务和既有 `ComfyUIVideoGenerator` 的前提下，为 DramaClaw 增加独立的 `comfyui_h3` 视频后端。用户只在 DramaClaw 中选择“MiniMax H3 Local”，即可完成首帧、首尾帧、多图参考、续拍、进度查看、视频回传和视频池入库。

## 架构

`MiniMaxH3ComfyUIGenerator` 是独立适配器，复用 DramaClaw 已有的 ComfyUI HTTP/WebSocket 通信模式，但使用两份从服务器已验证工作流导出的最小 API 模板：FL2VA 和 REF2VA。适配器负责素材上传、模式选择、参数注入、单并发控制、进度监听、输出解析、下载、静音处理和末帧提取。

主剧情 runner 和 Freezone 都继续调用统一的 `create_video_generator()`。新增后端只消费现有 `ShotReference` 数据，不把 H3 伪装成 Seedance，也不改变旧 ComfyUI/Wan/LTX 行为。

## 参数与规则

- 后端 ID：`comfyui_h3`；显示名：`MiniMax H3 Local`。
- 地址：`COMFYUI_H3_ADDRESS=192.168.3.9:18189`，默认 HTTP，超时 1800 秒，最大并发 1。
- 模式自动判断：有 references 用 REF2VA；否则首帧+尾帧用 FL2VA；仅首帧用 FL2VA；无图片拒绝。
- 参考图保持输入顺序，过滤空项后重新连续编号，最多 9 张；三视图文件作为一张图。
- 画幅仅支持 `9:16=480x864`、`16:9=864x480`、`1:1=480x480`。
- 时长仅支持 5/8/10/15 秒；24 FPS 后按 `17k+5` 规则向上对齐帧数。
- Preset：QUALITY=20步/无EasyCache/MAX；FAST=16步/0.20/MAX；TURBO=12步/0.28/MATCH。
- 默认保留 H3 原生音频；静音在视频下载后用 ffmpeg 移除音轨。
- 每个成功视频用 ffmpeg 提取末帧并写入 `VideoGenResult.last_frame_path`。

## 前端与 Freezone

Freezone 模型目录增加本地 provider 和 H3 条目，声明 `first_frame`、`first_last_frame`、`image_reference` 三种能力、1–9 张图片、480p 和 5–15 秒。前端能力判断优先使用后端下发的 `supportedModes`，H3 不显示文生、视频编辑、视频/音频参考。

主剧情沿用已有 video backend 字段，默认设置为 `comfyui_h3`；runner 透传 references、preset、seed、audio_setting，并将输出加入现有视频池。

## 错误与健康检查

网络不可达、参考图超限、ComfyUI 工作流错误和显存不足均转换为普通用户能理解的中文错误。健康检查只调用 `/system_stats`，不加载模型、不重启服务、不停止其他 GPU 服务。

## 测试

自动测试覆盖后端注册、Preset、画幅、帧数、模式判断、1–9 图及超限、Picture 编号、工作流注入、输出解析、末帧提取和前端能力。集成验收依次执行 UI/API 健康、首帧 TURBO、首尾帧 TURBO、两图 Reference TURBO、续拍，最后只执行一次 FAST 正式质量测试。

## 回滚

DramaClaw 所有变化只在 `feature/minimax-h3-local` 分支。回滚时停止本项目 Compose，切回部署前提交或 main 后重新构建；H3 systemd 服务和目录不受影响。
