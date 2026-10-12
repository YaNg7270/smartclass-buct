# smartclass-buct（`scls`）

北京化工大学 smartclass 录播课处理工具：**给定日期 + 课程名，一条命令产出**
课件视频、逐字转写、课件 OCR、**作业要求汇总**、**课堂笔记**。

> 仅用于处理本人在校平台上有权限访问的课程录播，供个人复习使用。
> 工具不会代填账号密码/验证码，登录一律由本人手工完成。

## 仓库结构

所有东西都在这一个目录下，不再散落各处：

```
smartclass-buct/
├── src/smartclass/          工具源码（CLI 主体）
├── tests/                   单元测试 + fixtures
├── skills/smartclass-buct/  Claude Code skill 源文件
│                            （~/.claude/skills/smartclass-buct 是指向这里的 junction）
├── data/                    【不入库】全部产出
│   ├── <课程>/<日期>/<时段>/
│   ├── _手工初版/            早期手工整理的转写/帧/音频/汇总（留档）
│   └── _profile/            浏览器登录态（Chrome user-data-dir）
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

## 快速开始

```bash
# 1) 首次：有头打开浏览器，手工登录一次（登录态存进 data/_profile）
scls login

# 2) 看看今天有什么课
scls list --date 2026-10-12
scls list --date 2026-10-12 --course 化工导论

# 3) 全流程
scls run --date 2026-10-12 --course 化工导论
```

产物落在 `data/<课程>/<日期>/<时段>/`：

```
meta.json        # NewID、scheduleId、教师、教室、机位 URL
videos/          # 课件视频（默认只下 VGA=屏幕机位）
audio/           # 抽出的音频 + 切块
transcript/      # 每块 JSON + full.txt（逐字转写）
frames/          # 课件抽帧
slides.txt       # 课件 OCR
homework.md      # 作业要求汇总（含"证据区"原文行）
notes.md         # 课堂笔记
```

## 常用参数

| 参数 | 说明 |
|---|---|
| `--tracks vga,video1` | 默认只下 `vga`（课件/屏幕）。加 `video1` 连教室摄像机一起下（每节多约 730MB） |
| `--out DIR` | 指定输出根目录 |
| `--asr gateway\|whisper` | 转写通道，默认 `gateway`（本地 cc-switch 网关）；`whisper` 为离线兜底（CPU 很慢） |
| `--headed` | 有头运行；登录失效时可现场登录 |
| `--dry-run` | 只发现课时 + 取流并打印机位地址和体积，**不下载**。用来快速确认某天某课有没有录播 |
| `--force` | 忽略缓存全部重跑 |
| `--no-slides` / `--no-digest` | 跳过课件 OCR / 跳过归纳 |

分阶段重跑（都幂等，已完成的会跳过）：

```bash
scls asr    --run "runs/化工导论/2026-10-12/0945-1030"
scls slides --run "..." --mode assignment   # 或 --mode full 抄全部幻灯片
scls digest --run "..."
```

## 配置

查找顺序：`./smartclass.toml` → **仓库根目录 `smartclass.toml`** → `~/.smartclass-buct.toml`；
也可用 `--config <路径>`（放前后都行）或环境变量 `SCLS_CONFIG` 指定。
仓库里已带一份配好的 `smartclass.toml`（不入库），模板见 `config.example.toml`。主要项：

```toml
[site]
base_url = "https://buct.smartclass.cn"
profile_dir = "<仓库>/data/_profile"

[paths]
runs_root = "<仓库>/data"
ffmpeg = "ffmpeg"

[asr]
provider = "gateway"
gateway_url = "http://127.0.0.1:8317/v1/chat/completions"
gateway_key = "123456"          # 本地代理，勿提交到仓库
model = "gemini-3.8-flash-high"
chunk_seconds = 600
```

## 实现要点

- **不直连接口**：列表数据来自 `/Webapi/V1/Video/GetPublicVideos`，URL 里带一次性
  `csrkToken`（页面 JS 生成），直接请求返回 `验证不通过`。因此改为**拦截页面自身的 XHR**
  （截不到时退回解析 DOM）。
- **机位识别（双路）**：优先读点播页 `<video><source src>`；
  但播放器并非每次都初始化成功（页面里 8 个空 `<video>`，src 始终为空），
  此时退回**从页面自身的 VOD 请求反推**——页面必然去取
  `.../<scheduleId>/1/000.jpg`、`Caption.json`、`srt.js`，从这些 URL 里取出目录前缀
  `https://buctvodNNN.smartclass.cn:8010/vod4/YYYY/MM/DD/<scheduleId>/1/`，
  再拼出 `VGA.mp4` / `Video1.mp4` 并用 HEAD 验证存在。
  `VGA`=课件，`Video1`=教室摄像机。
- **转写**：平台自带语音识别对多数录播为空，故自建——ffmpeg 抽 16k 单声道 mp3，
  按 10 分钟切块送本地网关（Gemini 多模态），每块单独缓存，重跑只补失败块。
- **归纳**：模型只依据转写+课件 OCR，结论要附原文引用；另附**规则命中的原文行**作为证据区，
  便于人工核对（课件缩略图 OCR 不可靠时会在"存疑"一节点出）。

## 已知限制

- 登录会过期，需重新 `scls login`（或加 `--headed` 现场登录）。
- 平台日期筛选控件若改版，`discover.set_date_range()` 可能失效；默认近 7 天可用，
  查更早日期请留意日志提示。
- `--asr whisper` 在本机（无 GPU）非常慢，仅作断网兜底。
