"""区分开发安装目录、单文件的解包资源和用户可携带的数据目录。"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config


def portable_root() -> Path | None:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def bundled_file(*parts: str) -> Path | None:
    if portable_root() is None:
        return None
    return Path(sys._MEIPASS, *parts)


def apply_portable_defaults(cfg: Config) -> None:
    root = portable_root()
    if root is not None:
        cfg.paths.runs_root = str(root / "data")
        cfg.site.profile_dir = str(root / "data" / "_profile")
        cfg.paths.ffmpeg = str(bundled_file("media", "ffmpeg.exe"))
        cfg.paths.ffprobe = str(bundled_file("media", "ffprobe.exe"))


def cli_command(verb: str, args: list[str], cfg: Config) -> list[str]:
    entry = [sys.executable]
    if portable_root() is None:
        entry.extend(["-m", "smartclass.cli"])
    entry.extend([verb, *args])
    if cfg.config_path:
        entry.extend(["--config", str(Path(cfg.config_path).resolve())])
    return entry
