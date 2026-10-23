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


def _dhash(path: Path, size: int = 16) -> int:
    """16×16 灰度差分哈希（256 bit）。同一张幻灯片基本不变，汉明距离很小。"""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("L").resize((size + 1, size), Image.LANCZOS)
        # Pillow 12 起 getdata() 逐步废弃；老版本没有 get_flattened_data
        getter = getattr(im, "get_flattened_data", None) or im.getdata
        px = list(getter())
    bits = 0
    for row in range(size):
        base = row * (size + 1)
        for col in range(size):
            bits = (bits << 1) | (1 if px[base + col] > px[base + col + 1] else 0)
    return bits


def dedupe_frames(frames: list[Path], threshold: int = 3) -> list[tuple[int, Path]]:
    """去掉几乎没变化的帧，返回 [(原始序号, Path)]。

    原始序号必须保留：OCR 的时间戳标签是按「第几张 × interval」算的，
    去重后不能用新下标，否则笔记里的时间会整体错位。
    """
    if not frames:
        return []
    try:
        prev = _dhash(frames[0])
    except ImportError:
        _log("未安装 Pillow，跳过抽帧去重（pip install Pillow）")
        return list(enumerate(frames))
    kept: list[tuple[int, Path]] = [(0, frames[0])]
    for idx, path in enumerate(frames[1:], 1):
        cur = _dhash(path)
        if bin(prev ^ cur).count("1") > max(0, int(threshold)):
            kept.append((idx, path))
            prev = cur
    if len(kept) < len(frames):
        _log(f"去重：{len(frames)} 帧 → {len(kept)} 帧（阈值 {threshold}）")
    return kept

