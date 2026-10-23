"""配置加载：内置默认值 < TOML 配置文件 < 命令行覆盖。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
import tomllib

CONFIG_ENV = "SCLS_CONFIG"

# 仓库根目录（本文件位于 <root>/src/smartclass/config.py）
REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_CONFIG_PATHS = [
    Path.cwd() / "smartclass.toml",   # 当前目录
    REPO_ROOT / "smartclass.toml",    # 仓库根目录（主要方式）
    Path.home() / ".smartclass-buct.toml",  # 旧的全局位置，兼容
]


@dataclass
class SiteConfig:
    base_url: str = "https://buct.smartclass.cn"
    profile_dir: str = str(Path.home() / ".smartclass-buct" / "profile")
    # 登录等待时长（秒），超时视为失败
    login_timeout: int = 300
    # 可选：Playwright storage state 文件（导出自其它已登录会话），先注入再启动
    storage_state: str = ""


@dataclass
class PathsConfig:
    runs_root: str = str(Path.home() / "smartclass-buct" / "runs")
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"


@dataclass
class AsrConfig:
    provider: str = "gateway"  # gateway | whisper
    gateway_url: str = "http://127.0.0.1:8317/v1/chat/completions"
    gateway_key: str = "123456"
    # 实测（10 分钟块）：gemini-3-flash 约 7~13s，gemini-3.8-flash-high 约 49s；
    # 多并发下差距同样明显，转写质量对作业归纳够用，故默认用 flash。
    model: str = "gemini-3-flash"
    # 主模型对某些音频会返回空 choices（completion_tokens=0），逐级回退到这些模型
    fallback_models: list = field(default_factory=lambda: [
        "gemini-3.8-flash-high", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
    ])
    # 空结果/失败时把这块音频对半切开重试的最小长度（秒），低于此值不再切
    min_split_seconds: int = 45
    chunk_seconds: int = 600
    bitrate: str = "32k"
    max_tokens: int = 8000
    whisper_model: str = "small"
    whisper_bin: str = "whisper"


@dataclass
class VisionConfig:
    interval_seconds: int = 45
    width: int = 1280
    batch: int = 8
    model: str = "gemini-3.8-flash-high"
    max_tokens: int = 4000
    # 抽帧后按 dHash 去掉几乎相同的帧（课堂幻灯片长时间不动）
    dedupe: bool = True
    dedupe_threshold: int = 3   # 256 bit dHash 的汉明距离，越小越保守


@dataclass
class DigestConfig:
    """整课级归纳（作业要求 / 课堂笔记）。"""
    model: str = "gemini-3.8-flash-high"
    max_tokens: int = 8000


@dataclass
class ConcurrencyConfig:
    """并发度。全部可退回 1（= 原来的串行行为）。"""
    # 同时发往本地网关的请求数（ASR 块 / OCR 批）
    gateway: int = 4
    # 同一节课里同时下载的机位数
    downloads: int = 3
    # 后台压缩线程数（x264 很吃 CPU，默认 1）
    compress: int = 1
    # 处理第 N 节时同时下载第 N+1 节
    pipeline: bool = True


@dataclass
class GroupingConfig:
    # 相邻小节间隔 ≤ 此值（分钟）即并成一节「整课」
    max_gap_minutes: int = 30


@dataclass
class RetentionConfig:
    # none = 只留音频+关键帧+文本；compressed = 压缩后就地替换；original = 不动
    keep_video: str = "compressed"
    video_height: int = 1080      # 源高于此值才缩放（默认保持 1080p，保护课件小字）
    video_crf: int = 33
    video_preset: str = "veryfast"
    video_codec: str = "libx264"  # 仅 CPU 编码时使用
    # auto = 探测 GPU 编码器（QSV→NVENC→AMF），都不可用则 CPU；也可强制 cpu/qsv/nvenc/amf
    video_encoder: str = "auto"
    # 0 = 保持源帧率；设为 10/5 可显著加快编码（课堂录屏通常不需要 25fps）
    video_fps: int = 0
    # 码率低于此值就不必再压（防止把已压缩的文件再压一遍）
    skip_below_kbps: int = 600


@dataclass
class WatchConfig:
    # 定时批处理只处理这些课（留空 = 不限制）
    courses: list = field(default_factory=list)


@dataclass
class ScheduleConfig:
    time: str = "22:30"
    task_name: str = "smartclass-buct-daily"


@dataclass
class Config:
    site: SiteConfig = field(default_factory=SiteConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    digest: DigestConfig = field(default_factory=DigestConfig)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)
    grouping: GroupingConfig = field(default_factory=GroupingConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)


def _apply(section, data) -> None:
    valid = {f.name for f in fields(section)}
    for k, v in (data or {}).items():
        if k in valid:
            setattr(section, k, v)


def load_config(path: str | None = None) -> Config:
    """按 内置默认 < 配置文件 的顺序合并配置。"""
    cfg = Config()

    candidates = []
    if path:
        candidates.append(Path(path))
    elif os.environ.get(CONFIG_ENV):
        candidates.append(Path(os.environ[CONFIG_ENV]))
    else:
        candidates.extend(DEFAULT_CONFIG_PATHS)

    for p in candidates:
        if p.is_file():
            with open(p, "rb") as fh:
                raw = tomllib.load(fh)
            _apply(cfg.site, raw.get("site"))
            _apply(cfg.paths, raw.get("paths"))
            _apply(cfg.asr, raw.get("asr"))
            _apply(cfg.vision, raw.get("vision"))
            _apply(cfg.digest, raw.get("digest"))
            _apply(cfg.concurrency, raw.get("concurrency"))
            _apply(cfg.grouping, raw.get("grouping"))
            _apply(cfg.retention, raw.get("retention"))
            _apply(cfg.watch, raw.get("watch"))
            _apply(cfg.schedule, raw.get("schedule"))
            break

    # 环境变量兜底（便于临时切网关）
    if os.environ.get("SCLS_GATEWAY_KEY"):
        cfg.asr.gateway_key = os.environ["SCLS_GATEWAY_KEY"]
    if os.environ.get("SCLS_GATEWAY_URL"):
        cfg.asr.gateway_url = os.environ["SCLS_GATEWAY_URL"]

    return cfg


def runs_root(cfg: Config, override: str | None = None) -> Path:
    p = Path(override or cfg.paths.runs_root).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p
