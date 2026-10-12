"""ffmpeg 封装：抽音频、切块、抽帧、探测时长。"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import Config


def _log(msg: str) -> None:
    print(f"[audio] {msg}", file=sys.stderr, flush=True)


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"命令失败：{' '.join(cmd[:6])}...\n{proc.stderr[-800:]}"
        )


def require_ffmpeg(cfg: Config) -> None:
    for exe in (cfg.paths.ffmpeg, cfg.paths.ffprobe):
        if not shutil.which(exe):
            raise RuntimeError(f"找不到 {exe}，请安装 ffmpeg 或修改配置里的路径。")


def duration(cfg: Config, path: Path) -> float:
    proc = subprocess.run(
        [cfg.paths.ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


def extract_audio(cfg: Config, src: Path, dest: Path, bitrate: str = "32k") -> Path:
    """抽成 16k 单声道 mp3（课堂语音足够，体积小便于外发识别）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0 and \
            dest.stat().st_mtime >= Path(src).stat().st_mtime:
        _log(f"音频已存在，跳过：{dest.name}")
        return dest
    _log(f"抽取音频：{Path(src).name} → {dest.name}")
    _run([cfg.paths.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
          "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
          "-c:a", "libmp3lame", "-b:a", bitrate, str(dest)])
    return dest


@dataclass
class Chunk:
    path: Path
    offset: int   # 整节课内的秒数
    dur: float


def split_chunks(cfg: Config, src: Path, outdir: Path, chunk_seconds: int = 600) -> tuple[list[Chunk], float]:
    outdir.mkdir(parents=True, exist_ok=True)
    total = duration(cfg, src)
    n = max(1, int(total // chunk_seconds) + (1 if total % chunk_seconds else 0))
    chunks: list[Chunk] = []
    for i in range(n):
        offset = i * chunk_seconds
        dest = outdir / f"part_{i:03d}.mp3"
        if not (dest.is_file() and dest.stat().st_size > 0):
            _run([cfg.paths.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                  "-ss", str(offset), "-t", str(chunk_seconds), "-i", str(src),
                  "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "32k", str(dest)])
        chunks.append(Chunk(path=dest, offset=offset, dur=min(chunk_seconds, total - offset)))
    return chunks, total


def extract_frames(cfg: Config, src: Path, outdir: Path, interval: int = 45, width: int = 1280) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    existing = sorted(outdir.glob("f_*.jpg"))
    if existing:
        _log(f"抽帧已存在，跳过（{len(existing)} 张）")
        return existing
    _log(f"抽帧：每 {interval}s 一张")
    _run([cfg.paths.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
          "-i", str(src), "-vf", f"fps=1/{interval},scale={width}:-1",
          "-q:v", "3", str(outdir / "f_%03d.jpg")])
    return sorted(outdir.glob("f_*.jpg"))
