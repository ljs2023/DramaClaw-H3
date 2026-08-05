# MiniMax H3 Local 使用说明

## 1. 访问地址

- DramaClaw 网页：`http://192.168.3.9:8080`
- DramaClaw API：`http://192.168.3.9:8780`
- H3/ComfyUI：`http://192.168.3.9:18189`

成功标志：网页能打开，进入自由画布的视频节点后，模型列表中能看到
`MiniMax H3 Local`。

## 2. 查看 H3 状态

在服务器终端运行：

```bash
systemctl is-active minimax-h3-comfyui.service
curl -f http://127.0.0.1:18189/system_stats
```

成功标志：第一条显示 `active`，第二条返回系统和显卡信息。

## 3. 如何选择 H3

在自由画布添加“视频生成”节点，在模型选择器中选择：

```text
本地 / MiniMax H3 Local
```

当前只开放已验收配置：

- 清晰度：480P
- 画幅：9:16、16:9、1:1
- 时长：5、8、10、15 秒
- 输入：首帧、首尾帧、1–9 张参考图

## 4. 首帧生成

1. 给视频节点连接一张图片作为首帧。
2. 输入动作、镜头和环境描述。
3. 选择时长、画幅及质量档。
4. 点击生成。

成功标志：任务进度从排队进入 H3 采样，最终视频回到 DramaClaw，并同时产生末帧。

## 5. 首尾帧生成

给节点连接首帧和尾帧两张图片。H3 会从第一张过渡到第二张。两张图的主体、
画幅和光线越接近，过渡越稳定。

## 6. 多参考图

选择“参考图”模式，可使用 1–9 张图片。只添加真正需要保持一致的素材；
图片越多不一定越好。系统会把有效图片连续编号为 `Picture 1`、`Picture 2`，
不会留下跳号。

## 7. 人物三视图

将正面、侧面、背面图都标为“角色：人物名”，再描述人物要执行的动作。
建议先用 5 秒 TURBO 测构图，再用 FAST 做正式片段。部署验收只使用了公开测试图，
请用你自己的角色三视图再做一次人脸一致性验收。

## 8. 场景和道具

- 场景图标为“场景：名称”，用于环境、布局和光线参考。
- 道具图标为“道具：名称”，提示 H3 保持道具外观。
- 角色、场景、道具可以混合，但总数最多 9 张。

## 9. 三个质量档

| 质量档 | 适合用途 | 速度 |
|---|---|---|
| TURBO | 构图试跑、快速预览 | 最快 |
| FAST | 日常正式生成，推荐默认使用 | 中等 |
| QUALITY | 重要镜头最终输出 | 最慢 |

QUALITY 使用 20 步并关闭 EasyCache。不要一次排很多 QUALITY 任务；这台服务器只有
一张 12GB 显卡，DramaClaw 会自动让 H3 任务排队，避免抢显存。

## 10. 续拍

第一段成功后，把它自动返回的末帧连接为下一段的首帧，再生成下一段：

```text
Shot01 → 自动末帧 → Shot02
```

成功标志：Shot02 的第一帧与 Shot01 的最后一帧连续。

## 11. 视频输出位置

视频会回到 DramaClaw 的数据卷，容器内路径为：

```text
/data/output
```

本次公开素材验收文件位于：

```text
/data/output/h3-smoke
```

## 12. 常见错误与排查

### 网页打不开

```bash
cd /data/DramaClaw
/usr/local/bin/docker-compose -p dramaclaw ps
```

确认 `api` 和 `web` 都是 `Up`，并检查 8080、8780 端口是否被占用。

### 提示 H3 不可访问

```bash
systemctl status minimax-h3-comfyui.service --no-pager
curl -f http://127.0.0.1:18189/system_stats
```

### 显存不足

等待其他显卡任务结束后重试，优先使用 TURBO、480P、5 秒。不要同时在 ComfyUI
手动运行大型任务。

### 任务一直排队

DramaClaw 默认只允许一个 H3 GPU 任务运行。前一个任务结束后会自动继续。

### 参考图被拒绝

确认图片数量为 1–9 张，且没有连接视频或音频作为参考素材。

## 13. 重启 DramaClaw

```bash
cd /data/DramaClaw
/usr/local/bin/docker-compose -p dramaclaw restart
```

成功标志：`docker ps` 中 `dramaclaw-api-1` 和 `dramaclaw-web-1` 均为 `Up`。
两个容器已设置 `unless-stopped`，服务器重启后会自动恢复。

## 14. 查看日志

```bash
cd /data/DramaClaw
/usr/local/bin/docker-compose -p dramaclaw logs --tail=100 api web
```

H3 日志：

```bash
journalctl -u minimax-h3-comfyui.service -n 100 --no-pager
```

## 15. 更新 DramaClaw

更新前先备份 `.env`，并确认当前分支：

```bash
cd /data/DramaClaw
git status
git branch --show-current
```

本次版本没有自动推送到远程仓库。今后更新时不要直接覆盖 `.env`，也不要在 H3
正在生成时重建容器。

## 16. 回滚本次 H3 适配

安全做法是先停止 DramaClaw，再切回部署前提交，保留数据卷：

```bash
cd /data/DramaClaw
/usr/local/bin/docker-compose -p dramaclaw down
git switch -c rollback-before-h3 a506de7421905ca55d3ed4f9ae5d5d9648105d71
/usr/local/bin/docker-compose -p dramaclaw up -d --build
```

这不会删除 `dramaclaw_ce-data` 数据卷。不要运行带 `-v` 的 `down`，否则会删除
DramaClaw 数据。H3 服务是独立服务，回滚 DramaClaw 不会改动 H3。

