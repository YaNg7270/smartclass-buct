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
    protocol: str = "chat"   # chat 输入音频，或 transcriptions 上传音频文件
    gateway_url: str = ""
    gateway_key: str = ""
    model: str = ""
    fallback_models: list = field(default_factory=list)
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
    model: str = ""
    max_tokens: int = 4000
    # 主模型失败（空结果/HTTP 错误）时依次尝试这些模型
    fallback_models: list = field(default_factory=list)
    # 端点留空沿用 ASR 地址，凭据仅在同源时允许共享。
    gateway_url: str = ""
    gateway_key: str = ""
    inherit_key: bool = True
    # 抽帧后按 dHash 去掉几乎相同的帧（课堂幻灯片长时间不动）
    dedupe: bool = True
    dedupe_threshold: int = 3   # 256 bit dHash 的汉明距离，越小越保守


@dataclass
class DigestConfig:
    """整课级归纳（作业要求 / 课堂笔记 / 课程总览）。"""
    model: str = ""
    max_tokens: int = 8000
    # 主模型失败时依次尝试这些模型
    fallback_models: list = field(default_factory=list)
    gateway_url: str = ""
    gateway_key: str = ""
    inherit_key: bool = True


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
class ReviewConfig:
    """把每讲的笔记/作业整理成 Obsidian 复习资料（MOC + 每讲一页）。"""
    folder: str = "复习"        # 复习资料目录名（位于 data/<课程>/ 下）
    auto: bool = False          # run/daily/resume 结束后自动增量更新
    overview: bool = True       # 是否生成 LLM 课程总览
    max_tokens: int = 6000


@dataclass
class TlsConfig:
    """从学生配置导入的私有信任锚，各阶段只信任各自导入时的源站。"""
    ca_pem: str = ""
    origins: dict = field(default_factory=dict)


@dataclass
class Config:
    site: SiteConfig = field(default_factory=SiteConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    digest: DigestConfig = field(default_factory=DigestConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)
    grouping: GroupingConfig = field(default_factory=GroupingConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)
    tls: TlsConfig = field(default_factory=TlsConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    config_path: str = ""


def _apply(section, data) -> None:
    valid = {f.name for f in fields(section)}
    for k, v in (data or {}).items():
        if k in valid:
            setattr(section, k, v)


def load_config(path: str | None = None) -> Config:
    """按 内置默认 < 配置文件 的顺序合并配置。"""
    from .runtime import apply_portable_defaults, portable_root

    cfg = Config()
    apply_portable_defaults(cfg)

    candidates = []
    if path:
        candidates.append(Path(path))
    elif os.environ.get(CONFIG_ENV):
        candidates.append(Path(os.environ[CONFIG_ENV]))
    elif (root := portable_root()) is not None:
        candidates.append(root / "smartclass.toml")
    else:
        candidates.extend([Path.cwd() / "smartclass.toml", *DEFAULT_CONFIG_PATHS[1:]])

    cfg.config_path = str(candidates[0].expanduser().resolve())
    for p in candidates:
        p = p.expanduser().resolve()
        if p.is_file():
            cfg.config_path = str(p)
            with open(p, "rb") as fh:
                raw = tomllib.load(fh)
            _apply(cfg.site, raw.get("site"))
            _apply(cfg.paths, raw.get("paths"))
            _apply(cfg.asr, raw.get("asr"))
            _apply(cfg.vision, raw.get("vision"))
            _apply(cfg.digest, raw.get("digest"))
            _apply(cfg.review, raw.get("review"))
            _apply(cfg.concurrency, raw.get("concurrency"))
            _apply(cfg.grouping, raw.get("grouping"))
            _apply(cfg.retention, raw.get("retention"))
            _apply(cfg.watch, raw.get("watch"))
            _apply(cfg.tls, raw.get("tls"))
            _apply(cfg.schedule, raw.get("schedule"))
            break

    # 环境变量兜底（便于临时切网关）
    if os.environ.get("SCLS_GATEWAY_URL"):
        from .gateway import origin
        new_url = os.environ["SCLS_GATEWAY_URL"].strip()
        if origin(cfg.asr.gateway_url) != origin(new_url):
            cfg.asr.gateway_key = ""
            for section in (cfg.vision, cfg.digest):
                if not section.gateway_url:
                    section.gateway_key = ""
        cfg.asr.gateway_url = new_url
    if os.environ.get("SCLS_GATEWAY_KEY"):
        cfg.asr.gateway_key = os.environ["SCLS_GATEWAY_KEY"]

    return cfg


def runs_root(cfg: Config, override: str | None = None) -> Path:
    p = Path(override or cfg.paths.runs_root).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p
