---
name: smartclass-buct
description: 从北京化工大学 smartclass 平台扒取指定日期某门课的录播视频，自动做语音转写、课件 OCR，并归纳出「作业要求」与「课堂笔记」。当用户说"扒一下某天某课的录播/回放""这节课的视频""老师布置了什么作业""课堂笔记"且涉及北化 smartclass 时使用。
allowed-tools: Bash(scls:*) Bash(python:*) Bash(ffmpeg:*) Bash(ffprobe:*)
---

# 北化 smartclass 录播课扒取

底层是一个已安装的命令行工具 `scls`（源码在 `C:\Users\USER\Desktop\资料\tools\smartclass-buct`）。
**本 skill 不含业务逻辑，只负责按正确姿势调用 `scls` 并妥善处理登录问题。**

## 前置检查

```bash
scls --help          # 能打印则已安装；否则 `python -m pip install -e "C:\Users\USER\Desktop\资料\tools\smartclass-buct"`
```

## 标准流程

```bash
# 1) 先确认登录态 —— 直接 list 即可，会自己检测
scls list --date 2026-10-12
scls list --date 2026-10-12 --course 化工导论     # 课程名支持模糊匹配

# 2) 全流程（发现 → 下载课件视频 → 转写 → 课件 OCR → 作业要求 + 笔记）
scls run --date 2026-10-12 --course 化工导论

# 3) 分阶段重跑（幂等）
scls asr    --run "runs/化工导论/2026-10-12/0945-1030"
scls slides --run "..." --mode full      # 抄全部幻灯片，供笔记用
scls digest --run "..."
```

产物在 `runs/<课程>/<日期>/<时段>/`：`homework.md`（作业要求，含证据区）、`notes.md`（笔记）、
`transcript/full.txt`（逐字转写）、`slides.txt`（课件 OCR）、`videos/`。

## 登录失效怎么办（最常见的情况）

站点会话会过期。此时 `scls` 会报错并提示重新登录。**不要试图自动填账号密码/验证码**，改为：

1. 告诉用户：需要在弹出的浏览器里**手工登录**一次；
2. 建议用户在会话里用 `!` 前缀直接跑，这样输出会回到对话：
   `! scls login`
3. 用户回复"登录好了"之后，再继续原来的命令。

如果用户希望当场登录，也可以在 `list`/`run` 上加 `--headed`，工具会等待手工登录完成。

## 参数速查

| 需求 | 命令 |
|---|---|
| 先确认某天某课有没有录播（不下载） | `scls run --date <日期> --course <课名> --dry-run` |
| 只要课件机位（默认） | `scls run --date <日期> --course <课名>` |
| 连教室摄像机一起下 | `--tracks vga,video1`（每节多约 730MB） |
| 指定输出目录 | `--out "D:\某处"` |
| 换转写通道（网关不可用时） | `--asr whisper`（本机无 GPU，很慢） |
| 全部重跑 | `--force` |
| 只要视频不要归纳 | `--no-slides --no-digest` |

## 注意

- 对方站点接口带一次性 `csrkToken`，**不要**尝试用 curl/fetch 直连接口，交给 `scls` 处理。
- 下载体积大（课件机位约 700MB/节），跑之前跟用户确认课程和日期。
- 归纳结果仅供参考：`homework.md` 末尾的「证据区」是原文行，遇到存疑（如缩略图 OCR）要让用户
  以老师正式通知为准。
