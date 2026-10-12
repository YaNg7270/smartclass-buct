# smartclass-buct（`scls`）

北京化工大学 smartclass 录播课处理工具：**给定日期 + 课程名，一条命令产出**
课件视频、逐字转写、课件 OCR、**作业要求汇总**、**课堂笔记**，并可按天定时自动跑。

> 仅用于处理本人在校平台上有权限访问的课程录播，供个人复习使用。
> 工具不会代填账号密码/验证码，登录一律由本人手工完成。

## 仓库结构

```
smartclass-buct/
├── src/smartclass/          工具源码
├── tests/                   单元测试 + fixtures
├── skills/smartclass-buct/  Claude Code skill 源文件
│                            （~/.claude/skills/smartclass-buct 是指向这里的 junction）
├── data/                    【不入库】全部产出
│   ├── <课程>/<日期>/<首段>-<末段>/     ← 整课包
│   ├── _手工初版/            早期手工整理的留档
│   ├── _profile/            浏览器登录态
│   ├── _logs/               每日运行日志
│   └── _state/              锁 / 登录失效留痕 / 计划任务包装脚本
├── smartclass.toml          【不入库】本地配置
├── config.example.toml      配置模板（入库）
├── pyproject.toml
└── README.md
```

## 安装

```bash
# 依赖：Python ≥3.11、ffmpeg（需在 PATH）
cd "C:\Users\USER\Desktop\资料\smartclass-buct"
python -m pip install -e .
python -m playwright install chromium      # 首次
```

## 核心概念：整课

平台上一次课常被切成 2~3 个 45 分钟的**小节**（如线性代数A = 08:00-08:45 + 08:50-09:35）。
工具按「**同一门课、同一天、相邻小节间隔 ≤ 30 分钟**」把它们合并成一节**整课**，
整课才是产出单位——转写、作业要求、笔记都按整课出，不会再被小节切断。

阈值可配 `[grouping] max_gap_minutes`；`scls list` 会把分组结果直接打出来供核对。

## 快速开始

```bash
scls login                       # 首次：有头打开浏览器，手工登录一次
scls list --date 2026-10-12      # 看当天有哪些整课
scls run --course 化工导论 --date 2026-10-12   # 抓取 + 处理
```

产物（整课包）：

```
data/<课程>/<日期>/<首段>-<末段>/
├── lecture.json     整课元数据（成员小节、机位来源）
├── transcript.txt   整课转写（时间戳已按整课时间轴对齐）
├── slides.txt       整课课件 OCR
├── homework.md      作业要求汇总（含"证据区"原文行）
├── notes.md         课堂笔记
└── parts/<时段>/    各小节中间产物
    ├── meta.json  videos/  audio/  transcript/  frames/  slides.txt
```

## Windows 下的引号（重要）

课程名里带括号时**必须加引号**——PowerShell 会把 `(` `)` 当成表达式运算符：

```powershell
scls run --date 2026-10-09 --course "物理化学(Ⅰ)"     # 正确
scls run --date 2026-10-09 --course 物理化学(Ⅰ)       # ✗ PowerShell 报 CommandNotFoundException
```

课程名匹配本身有容错：`物理化学(Ⅰ)` / `物理化学(I)` / `物理化学（Ⅰ）` / `物理化学`
都能对上（罗马数字、全角括号、空格、大小写都会被折叠）。目录名一律取平台给的名字，
所以你用哪种写法查询都不会生成出两个目录。

## 命令

| 命令 | 用途 |
|---|---|
| `scls login` | 有头登录并保存登录态 |
| `scls list` | 列出课时（按整课分组） |
| `scls run` | 抓取 + 处理 |
| `scls daily` | **无人值守**：处理当天监控清单里所有课的整课 |
| `scls regroup` | 把旧的小节目录原地合并成整课包（不重新下载） |
| `scls prune` | 对已有数据补做视频压缩/删除 |
| `scls schedule install\|remove\|show` | Windows 计划任务管理 |
| `scls asr` / `slides` / `digest --run <目录>` | 分阶段重跑（幂等） |

## 筛选与切片粒度

```bash
# 日期：单日 或 区间
scls list --date 2026-10-12
scls list --from 2026-10-08 --to 2026-10-12

# 时段：只要上午的课
scls list --date 2026-10-12 --time-from 08:00 --time-to 12:00

# 星期
scls list --date 2026-10-12 --weekday mon,wed,fri

# 粒度：整课一份（默认） / 每小节各一份 / 两者都要
scls run  --course 化工导论 --unit lecture
scls run  --course 化工导论 --unit session
scls run  --course 化工导论 --unit both

# 调整合并阈值（分钟）
scls list --date 2026-10-12 --gap 20
```

> 注意：时段/星期筛选作用在**小节**上，再做合并。所以 `--time-to 10:00` 会把
> 化工导论的 10:40 那节切掉，它的整课就只剩 09:45-10:30——这正是"只要某段时间的课"。

## 视频瘦身

平台原件是 1080p/约 2 Mbps，**741MB/节**，绝大部分码率浪费在静态课件上。

| 策略 | 说明 |
|---|---|
| `compressed`（默认） | 1080p 保分辨率、CRF 33 重编码，实测 **10~17 倍**压缩（741MB → 42~95MB） |
| `none` | 处理完删原件，只留 mp3 + 关键帧 + 文本（一节 ~20MB） |
| `original` | 不动原件 |

```bash
scls run   --course 化工导论 --keep-video none
scls prune --keep-video compressed      # 对历史数据补做
```

**安全闸**：压缩后先 `ffprobe` 校验时长，**校验不过一律保留原文件**并回报原因；
码率已低于 600 kbps 的不再压，避免二次损失画质。

## 定时批处理

```toml
[watch]
courses = ["化工导论", "线性代数A"]   # 留空 = 处理当天全部
[schedule]
time = "22:30"
```

```bash
scls daily                      # 手动跑一次（也是计划任务实际执行的命令）
scls schedule install           # 装成 Windows 计划任务，每天 22:30 跑
scls schedule show              # 查看任务状态
scls schedule remove            # 卸载
```

- 日志：`data/_logs/<日期>_daily.log`（计划任务另有一份 `schtasks.log`）
- **登录失效**：退出码 `2`，写 `data/_state/login-needed.txt`，并弹一个 Windows 通知——
  不会静默失败。失效后手工 `scls login` 即可。
- 文件锁 `data/_state/<name>.lock`：同一时刻只允许一个抓取进程，避免和手动运行抢浏览器 profile。
- 计划任务**只在已登录的桌面会话里触发**，睡眠/关机时不跑。

## 常用参数

| 参数 | 说明 |
|---|---|
| `--tracks vga,video1` | 默认只下 `vga`（课件/屏幕）；`video1` 是教室摄像机（每节多约 730MB） |
| `--out DIR` | 输出根目录 |
| `--asr gateway\|whisper` | 转写通道，默认 `gateway`（本地 cc-switch 网关）；`whisper` 离线兜底（CPU 很慢） |
| `--dry-run` | 只发现 + 取流并打印机位地址和体积，不下载 |
| `--force` | 忽略缓存全部重跑 |
| `--no-slides` / `--no-digest` | 跳过课件 OCR / 跳过归纳 |
| `--headed` | 有头运行；登录失效时可现场登录 |

## 实现要点

- **不直连接口**：列表数据来自 `/Webapi/V1/Video/GetPublicVideos`，URL 里带一次性
  `csrkToken`（页面 JS 生成），直接请求返回 `验证不通过`。改为**拦截页面自身的 XHR**
  （截不到时退回解析 DOM）。
- **机位识别（双路）**：优先读点播页 `<video><source src>`；播放器并非每次都初始化成功
  （页面里 8 个空 `<video>`），此时退回**从页面自身的 VOD 请求反推**——页面必然去取
  `.../<scheduleId>/1/000.jpg`、`Caption.json`、`srt.js`，从这些 URL 取出目录前缀
  `https://buctvodNNN.smartclass.cn:8010/vod4/YYYY/MM/DD/<scheduleId>/1/`，
  再拼出 `VGA.mp4` / `Video1.mp4` 并用 HEAD 验证存在。
- **转写**：平台自带语音识别对多数录播为空，故自建——ffmpeg 抽 16k 单声道 mp3，
  按 10 分钟切块送本地网关（Gemini 多模态），用 system 提示词 + 防复述重试；
  每块单独缓存，重跑只补失败块。
- **合并**：小节内 `[mm:ss]` 按小节在整课中的起点平移，避免整课转写出现两个 `[02:13]`。
- **归纳**：模型只依据转写 + 课件 OCR，结论要附原文引用；另附**规则命中的原文行**作为证据区。

## 已知限制

- 登录会过期，需重新 `scls login`（或加 `--headed` 现场登录）。定时任务因此不是完全无人值守。
- 平台日期筛选控件若改版，`discover.set_date_range()` 可能失效；此时会退化为
  "在已拉取到的范围内过滤"并在日志里警告。
- `--asr whisper` 在本机（无 GPU）非常慢，仅作断网兜底。
- `libx265` 体积更小但编码慢约 3 倍，默认用 `libx264`。
