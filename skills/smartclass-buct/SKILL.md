---
name: smartclass-buct
description: 从北京化工大学 smartclass 平台扒取指定日期/某门课的录播视频，自动按「整课」打包，做语音转写、课件 OCR，归纳出「作业要求」与「课堂笔记」；支持按日期区间/时段/星期筛选、视频压缩瘦身、以及每晚定时批量处理。当用户说"扒一下某天某课的录播/回放""这节课的视频""老师布置了什么作业""课堂笔记""今晚把今天的课总结出来"且涉及北化 smartclass 时使用。
allowed-tools: Bash(scls:*) Bash(python:*) Bash(ffmpeg:*) Bash(ffprobe:*) Bash(schtasks:*)
---

# 北化 smartclass 录播课扒取

底层是已安装的命令行工具 `scls`（源码在本仓库根目录，该目录同时是一个 git 仓库）。
**本 skill 不含业务逻辑，只负责按正确姿势调用 `scls`。**
详细说明见仓库里的 `docs/项目文档.md`（用法）与 `docs/技术开发文档.md`（实现）。

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

产物直接放在 `data/<课程>/<日期>/`（扁平）：整课一份
`homework_<日期>_<时段>.md`（作业要求，含证据区）、`notes_<日期>_<时段>.md`（笔记）、
`transcript.txt`（整课逐字转写）、`slides.txt`（课件 OCR）；小节中间产物在 `parts/<时段>/`。

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
| "目录太深/想扁平化" | `scls migrate-layout`（先加 `--dry-run` 看要移动什么） |
| "跑得太慢/能不能快点" | 先 `scls bench --run <已缓存的小节>` 测网关并发，再调 `[concurrency] gateway`；`--jobs N` 可临时覆盖 |

## 登录失效怎么办（最常见）

站点会话会过期。`scls` 会报错并提示重新登录。**不要试图自动填账号密码/验证码**：

1. 告诉用户需要在弹出的浏览器里**手工登录**一次；
2. 建议用户在会话里用 `!` 前缀直接跑：`! scls login`
3. 用户回复"登录好了"后再继续原命令。

`list`/`run` 加 `--headed` 也可以当场登录（工具会等待，**期间不会刷新页面**）。
定时任务遇到登录失效会写 `data/_state/login-needed.txt` 并返回退出码 2。

## 注意

- **课程名带括号必须加引号**：PowerShell 会把 `()` 当表达式运算符。
  `scls run --course "物理化学(Ⅰ)"` 对；不加引号会报 CommandNotFoundException。
  （匹配本身有容错：罗马数字 Ⅰ/I、全角括号、空格都能对上。）
- **`--date` 与 `--from/--to` 互斥**：同时给会直接报错（历史行为是 `--date` 静默
  覆盖掉 `--to`，导致只查一天、课时数偏少）。区间查询写 `--from A --to B` 就行。
- 区间查询会自动翻页拉全（日志里有「翻页完成：… 共 N 条，实收 N 条」）；
  若出现「警告：翻页后仍未取到 … 课时」，用 `--date` 单日复核一次。
- 站点接口带一次性 `csrkToken`，**不要**用 curl/fetch 直连接口，交给 `scls`。
- 体积：课件机位原件约 700MB/节，默认处理后压到约 130MB（GPU 编码 45 分钟小节约 50 秒）；
  跑之前跟用户确认课程和日期。
- 默认并发：`[concurrency] gateway=4 / downloads=3 / compress=1 / pipeline=true`，
  日志出现「被限流」就把 `gateway` 降到 2；完全退回串行则全设 1、`pipeline=false`。
- 归纳结果仅供参考：`homework_*.md` 末尾「证据区」是原文行；遇到存疑（如课件缩略图 OCR）
  要提醒用户以老师正式通知为准。
