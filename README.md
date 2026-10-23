# smartclass-buct（`scls`）

北京化工大学 smartclass 录播课处理工具：**给定日期 + 课程名，一条命令产出**
课件视频、逐字转写、课件 OCR、**作业要求汇总**、**课堂笔记**，并可按天定时自动跑。

> 仅用于处理本人在校平台上有权限访问的课程录播，供个人复习使用。
> 工具不会代填账号密码/验证码，登录一律由本人手工完成。

## 文档

- [项目文档](docs/项目文档.md)：功能、安装、命令、配置、产物、故障排查、FAQ。
- [技术开发文档](docs/技术开发文档.md)：架构、模块地图、平台接口、并发调度、测试、扩展指南。

## 仓库结构

```
smartclass-buct/
├── src/smartclass/          工具源码
├── tests/                   单元测试 + fixtures
├── docs/                    项目文档 + 技术开发文档
├── skills/smartclass-buct/  Claude Code skill 源文件
│                            （~/.claude/skills/smartclass-buct 是指向这里的 junction）
├── data/                    【不入库】全部产出
│   ├── <课程>/<日期>/        ← 整课包（扁平）
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
cd smartclass-buct
python -m pip install -e .
python -m playwright install chromium      # 首次
```

## 打包

```bash
python -m pip install build        # 首次
python -m build                    # 产出 dist/*.whl 与 dist/*.tar.gz
python -m pip install dist/smartclass_buct-0.2.0-py3-none-any.whl   # 安装 wheel
```

wheel 自带 Web 前端模板与静态文件；`scls serve` 开箱即用。

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

产物（整课包，直接放在日期目录下）：

```
data/<课程>/<日期>/
├── homework_<日期>_<时段>.md   作业要求汇总（含"证据区"原文行）
├── notes_<日期>_<时段>.md      课堂笔记
├── transcript.txt              整课转写（时间戳已按整课时间轴对齐）
├── slides.txt                  整课课件 OCR
├── lecture.json                整课元数据（成员小节、机位来源、耗时）
└── parts/<时段>/               各小节中间产物
    ├── meta.json  videos/  audio/  transcript/  frames/  slides.txt
```

`homework` / `notes` 始终带 `<日期>_<时段>` 唯一后缀（例如
`homework_2026-10-16_0800-1030.md`），单独拷出来也不会混淆；小节级的
`parts/<时段>/homework_<日期>_<时段>.md` / `notes_...` 同样带后缀。
同一天同一门课有**两节**整课时，第一节占用无后缀的 `transcript.txt` /
`slides.txt` / `lecture.json`，后续的自动改用 `_<日期>_<时段>` 后缀，不会互相覆盖。

旧布局（`<日期>/<时段>/`）用 `scls migrate-layout` 一次性扁平化；先加 `--dry-run`
可以预览要移动的文件，迁移是幂等的。

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
| `scls resume` | **不联网**：对已下载的整课包续跑转写/OCR/归纳/压缩 |
| `scls bench` | 用已缓存的音频块/抽帧测网关并发与模型速度 |
| `scls daily` | **无人值守**：处理当天监控清单里所有课的整课 |
| `scls regroup` | 把旧的小节目录原地合并成整课包（不重新下载） |
| `scls migrate-layout` | 把 `<日期>/<时段>/` 扁平化到 `<日期>/`（homework/notes 带唯一后缀） |
| `scls prune` | 对已有数据补做视频压缩/删除 |
| `scls schedule install\|remove\|show` | Windows 计划任务管理 |
| `scls asr` / `slides` / `digest --run <目录>` | 分阶段重跑（幂等） |

### 下载完成但处理中断怎么办

```
scls resume --dir "data/物理化学(Ⅰ)"              # 只读本地文件，不登录、不下载
scls resume --dir "data/物理化学(Ⅰ)/2026-09-04"
```

`resume` 直接读 `lecture.json` 与 `parts/*/videos/*.mp4`，转写/OCR 按缓存续跑，
已完成的块不会重做。比重新 `run` 省掉发现、取流、下载全部 15 个视频的时间。

## 筛选与切片粒度

```bash
# 日期：单日 或 区间（二者互斥，混用会直接报错而不是静默只查一天）
scls list --date 2026-10-12
scls list --from 2026-09-01 --to 2026-10-16 --course "物理化学(Ⅰ)"

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

> **区间查询会翻页拉全**：平台列表接口默认一页 12 条，`scls` 会按日期区间
> 把 `GetMyVideoList` 翻到 `TotalCount` 为止（实测 09-01~10-16 共 115 条），
> 不再只看第一页。日志里会打印「翻页完成：… 共 N 条，实收 N 条」。

> 注意：时段/星期筛选作用在**小节**上，再做合并。所以 `--time-to 10:00` 会把
> 化工导论的 10:40 那节切掉，它的整课就只剩 09:45-10:30——这正是"只要某段时间的课"。

## 视频瘦身

平台原件是 1080p/约 2 Mbps，**741MB/节**，绝大部分码率浪费在静态课件上。

| 策略 | 说明 |
|---|---|
| `compressed`（默认） | 1080p 保分辨率重编码，实测 **5~6 倍**压缩（737MB → 128MB），课件小字仍清晰 |
| `none` | 处理完删原件，只留 mp3 + 关键帧 + 文本（一节 ~20MB） |
| `original` | 不动原件 |

压缩默认走 **GPU 硬件编码**：`[retention] video_encoder = "auto"` 依次探测
Intel QSV → NVIDIA NVENC → AMD AMF，都不可用才退回 CPU 的 libx264，探测结果进程内缓存。
实测（Intel Arc B390，45 分钟 1080p 小节）：**47~51 秒**压完（x264 要 104 秒），
CPU 占用几乎归零。QSV 用 ICQ 模式（`global_quality = crf - 3`），体积/画质与 x264 crf 对齐。
硬件编码失败会在本进程内自动降级回 CPU，不会中断整批任务。

```bash
scls run   --course 化工导论 --keep-video none
scls prune --keep-video compressed      # 对历史数据补做
```

## 提速（并发与流水线）

原来一批 5 节整课（15 个小节）约要 **100 分钟**，时间几乎全花在「串行等网关」和
「把压缩堆在每节最后」上。现在默认开启：

| 配置 | 默认 | 作用 |
|---|---|---|
| `[concurrency] gateway` | 4 | ASR 块 / OCR 批同时发 4 个请求 |
| `[concurrency] downloads` | 3 | 同一小节的多个机位并发下载 |
| `[concurrency] compress` | 1 | 压缩放后台线程，和下一小节的转写重叠 |
| `[concurrency] pipeline` | true | 处理第 N 节时同时下载第 N+1 节 |
| `[vision] dedupe` | true | 去掉几乎没变化的相邻帧（阈值 3/256） |
| `[retention] video_encoder` | auto | GPU 硬件编码（QSV/NVENC/AMF），压缩快 2 倍且不吃 CPU |
另外每个小节内部，**ASR 与「抽帧 + OCR」并行**，抽帧的几十秒解码藏在转写等待里。

实测（2026-10-16 物理化学，3 个小节，1080p）：整课 **146 秒**（不含压缩）；
原来同一节要 **16~18 分钟**。加上 GPU 压缩约 4~5 分钟/节，5 节整课约 20 分钟。
转写默认模型换成了 `gemini-3-flash`：实测 10 分钟音频块 **7~13 秒**，
`gemini-3.8-flash-high` 要 **49 秒**；flash 的措辞会略有差异（例如个别同音字），
但作业/笔记归纳的关键信息完整。要最高保真度就把 `[asr] model` 换回
`gemini-3.8-flash-high`（仍保留在 `fallback_models` 里兜底）。

如果网关只允许串行（`gateway=1`），仍有约 2 倍（主要靠阶段重叠和去重）。

### 先测一下你的网关能吃多少并发

```bash
scls bench --run "data/物理化学(Ⅰ)/2026-10-16/0800-1030/parts/0800-0845" \
           --stage asr,ocr --concurrency 1,2,4,6
```

它复用已经缓存好的 `audio/chunks/*.mp3` 和 `frames/*.jpg`，不重新抽、不覆盖缓存；
输出每个「模型 × 并发度」的墙钟时间、单请求均值、字数、与现有转写的相似度。
据此把 `[concurrency] gateway` 调到「延迟没有明显膨胀」的那一档。
相似度只作参考：换模型后措辞和时间戳粒度会变，分数偏低不代表内容缺失，
重点看输出字数是否与基线同量级，再抽查一段正文。

### 想再快 / 想退回串行

```toml
# 更激进：视频降帧率（课堂录屏通常够用），编码快 2~4 倍
[retention]
video_fps = 10        # 0 = 保持源帧率；5 更快但动作会明显卡顿

# 完全退回原来的串行行为
[concurrency]
gateway = 1
downloads = 1
compress = 1
pipeline = false
```

> 去重只是去掉「几乎一模一样」的相邻帧，原始帧序号会保留，时间戳不受影响。
> 如果某门课课件变化很细（例如连续滚动 PDF），可以设 `[vision] dedupe = false`。

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

- **不直连接口**：列表数据来自 `/Webapi/V1/Video/GetMyVideoList?StartDate=&EndDate=&PageSize=&PageNumber=`，
  URL 里带一次性 `csrkToken`（页面 JS 生成）。第一次请求**拦截页面自身的 XHR**拿到带 token 的
  URL，随后在页面上下文里用 `fetch` 复用该 token **按日期区间翻页到 `TotalCount`**
  （服务端 `PageSize` 上限 50；实测要 200 只回 50）。截不到时退回解析 DOM 与日期控件。
- **机位识别（双路）**：优先读点播页 `<video><source src>`；播放器并非每次都初始化成功
  （页面里 8 个空 `<video>`），此时退回**从页面自身的 VOD 请求反推**——页面必然去取
  `.../<scheduleId>/1/000.jpg`、`Caption.json`、`srt.js`，从这些 URL 取出目录前缀
  `https://buctvodNNN.smartclass.cn:8010/vod4/YYYY/MM/DD/<scheduleId>/1/`，
  再拼出 `VGA.mp4` / `Video1.mp4` 并用 HEAD 验证存在。
- **转写**：平台自带语音识别对多数录播为空，故自建——ffmpeg 抽 16k 单声道 mp3，
  按 10 分钟切块送本地网关（Gemini 多模态），用 system 提示词 + 防复述重试；
  每块单独缓存，重跑只补失败块。
- **转写降级链**：网关对**某些音频**会返回 `HTTP 200` + `"choices": []`
  （`completion_tokens=0`）——不是报错，但拿不到任何文本。故每块按
  `主模型（重试一次）→ asr.fallback_models 逐个 → 对半切开重试（最多 3 层）`
  的顺序处理，避免一整节课因为 10 分钟片段而整体失败。
- **并发调度**：`scheduler.gateway_map` 是一个有界线程池，ASR 块 / OCR 批都提交到
  同一个池，结果按 index 回填后再落盘，所以并发不影响 `full.txt` / `slides.txt` 的顺序。
  遇到 429/503 会随机退避重试同一模型，而不是立刻换模型。
- **抽帧去重**：Pillow 16×16 dHash，和上一张保留帧比汉明距离；返回原始帧序号，
  OCR 的时间戳标签仍按「原始序号 × interval」计算。Pillow 缺失时自动跳过。
- **合并**：小节内 `[mm:ss]` 按小节在整课中的起点平移，避免整课转写出现两个 `[02:13]`。
- **归纳**：模型只依据转写 + 课件 OCR，结论要附原文引用；另附**规则命中的原文行**作为证据区。

## 已知限制

- 登录会过期，需重新 `scls login`（或加 `--headed` 现场登录）。定时任务因此不是完全无人值守。
- 列表接口若改版（字段名或翻页参数变化），`discover.ListPager` 可能拉不全；
  日志会出现「警告：翻页后仍未取到 … 课时」，此时用 `--date` 单日核对。
- 并发上限取决于本地网关与上游账号：日志出现「被限流」时把
  `[concurrency] gateway` 降到 2。`scls bench` 可以先测出安全档位。
- 抽帧去重会合并「停留 <45 秒且画面几乎相同」的相邻幻灯片；需要绝对完整时设
  `[vision] dedupe = false`。
- `--asr whisper` 在本机（无 GPU）非常慢，仅作断网兜底。
- 硬件编码需 ffmpeg 带对应编码器且驱动正常，不可用时自动回退 libx264（不影响任务）。
  NPU 不参与：ffmpeg 没有 NPU 编码后端，本工具的 ASR/OCR 又是远程网关模型，硬件加速点在 GPU。
