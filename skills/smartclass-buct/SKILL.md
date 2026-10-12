---
name: smartclass-buct
description: 从北京化工大学 smartclass 平台扒取指定日期/某门课的录播视频，自动按「整课」打包，做语音转写、课件 OCR，归纳出「作业要求」与「课堂笔记」；支持按日期区间/时段/星期筛选、视频压缩瘦身、以及每晚定时批量处理。当用户说"扒一下某天某课的录播/回放""这节课的视频""老师布置了什么作业""课堂笔记""今晚把今天的课总结出来"且涉及北化 smartclass 时使用。
allowed-tools: Bash(scls:*) Bash(python:*) Bash(ffmpeg:*) Bash(ffprobe:*) Bash(schtasks:*)
---

# 北化 smartclass 录播课扒取

底层是已安装的命令行工具 `scls`（源码在 `C:\Users\USER\Desktop\资料\smartclass-buct`，
该目录同时是一个 git 仓库）。**本 skill 不含业务逻辑，只负责按正确姿势调用 `scls`。**

## 前置检查

```bash
scls --help    # 能打印即已安装；否则 python -m pip install -e "C:\Users\USER\Desktop\资料\smartclass-buct"
```

## 标准流程

```bash
# 1) 先看有没有课（会按「整课」分组展示）
scls list --date 2026-10-12
scls list --date 2026-10-12 --course 化工导论

# 2) 抓取 + 处理
scls run --date 2026-10-12 --course 化工导论

# 3) 只要确认有没有录播、不想下载
scls run --date 2026-10-12 --course 化工导论 --dry-run
```

产物在 `data/<课程>/<日期>/<首段>-<末段>/`：整课一份 `homework.md`（作业要求，含证据区）、
`notes.md`（笔记）、`transcript.txt`（整课逐字转写）、`slides.txt`（课件 OCR）；
小节中间产物在 `parts/<时段>/`。

## 关键词对应

| 用户说 | 命令 |
|---|---|
| "某天某课的录播/回放" | `scls run --date <日期> --course <课名>` |
| "这段时间的课" | 加 `--from <起> --to <止>` |
| "只要上午的课" | 加 `--time-from 08:00 --time-to 12:00` |
| "每周一三五的课" | 加 `--weekday mon,wed,fri` |
| "每小节分别总结" | 加 `--unit session`（默认是按整课 `lecture`） |
| "视频太占地方" | `--keep-video none`（只留音频+帧）或默认 `compressed`；历史数据用 `scls prune` |
| "每天晚上自动总结" | `scls schedule install --time 22:30`（先配 `[watch] courses`） |
| "把之前的整理成整课包" | `scls regroup`（原地合并，不重新下载） |

## 登录失效怎么办（最常见）

站点会话会过期。`scls` 会报错并提示重新登录。**不要试图自动填账号密码/验证码**：

1. 告诉用户需要在弹出的浏览器里**手工登录**一次；
2. 建议用户在会话里用 `!` 前缀直接跑：`! scls login`
3. 用户回复"登录好了"后再继续原命令。

`list`/`run` 加 `--headed` 也可以当场登录（工具会等待，**期间不会刷新页面**）。
定时任务遇到登录失效会写 `data/_state/login-needed.txt` 并返回退出码 2。

## 注意

- 站点接口带一次性 `csrkToken`，**不要**用 curl/fetch 直连接口，交给 `scls`。
- 体积：课件机位原件约 700MB/节，默认处理后压到约 50MB；跑之前跟用户确认课程和日期。
- 归纳结果仅供参考：`homework.md` 末尾「证据区」是原文行；遇到存疑（如课件缩略图 OCR）
  要提醒用户以老师正式通知为准。
