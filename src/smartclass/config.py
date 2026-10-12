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
    model: str = "gemini-3.8-flash-high"
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


@dataclass
class Config:
    site: SiteConfig = field(default_factory=SiteConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)


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
