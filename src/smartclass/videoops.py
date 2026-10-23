"""视频瘦身：压缩 / 删除，带"先校验再删"的安全闸。

基准实测（3 分钟样片 / 1080p 静态课件）：
  x264 1080p crf33 veryfast → 143 kbps，约 15x，48 分钟编码约 80 秒
  x264 720p  crf30 veryfast → 106 kbps，约 20x（但课件文字会变小、发糊）
课件是文字型内容，默认保留 1080p、只提高 CRF，保证小字仍可读。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from . import hwaccel
from .audio import duration, _run
from .config import Config

KEEP_NONE = "none"
KEEP_COMPRESSED = "compressed"
KEEP_ORIGINAL = "original"


def _log(msg: str) -> None:
    print(f"[retention] {msg}", file=sys.stderr, flush=True)


def stream_height(cfg: Config, path: Path) -> int:
    proc = subprocess.run(
        [cfg.paths.ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=height", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return 0


def verify(cfg: Config, path: Path, expect_seconds: float | None = None,
           tol: float = 0.08) -> tuple[bool, str]:
    """校验媒体文件可用：能探到时长，且与预期时长接近。"""
    if not path.is_file() or path.stat().st_size == 0:
        return False, "文件不存在或为空"
    dur = duration(cfg, path)
    if dur <= 0:
        return False, "探测不到时长"
    if expect_seconds and expect_seconds > 10:
        if abs(dur - expect_seconds) > max(5.0, expect_seconds * tol):
            return False, f"时长不符（{dur:.0f}s vs 预期 {expect_seconds:.0f}s）"
    return True, f"{dur:.0f}s"


def bitrate_kbps(cfg: Config, path: Path) -> float:
    dur = duration(cfg, path)
    if dur <= 0:
        return 0.0
    return path.stat().st_size * 8 / dur / 1000


def compress_video(cfg: Config, src: Path, force: bool = False) -> tuple[Path | None, str]:
    """把 src 压成小体积（就地替换）。

    返回 (结果文件, 说明)。压缩失败时返回 (None, 原因)，**原文件保持不动**。
    """
    if not src.is_file():
        return None, "源文件不存在"

    # 已经是低码率的（多半压过了），不要再压一遍，否则画质二次损失
    br = bitrate_kbps(cfg, src)
    floor = float(getattr(cfg.retention, "skip_below_kbps", 600) or 0)
    if not force and br and br < floor:
        return src, f"码率已很低（{br:.0f} kbps < {floor:.0f}），跳过"

    src_dur = duration(cfg, src)
    h = stream_height(cfg, src)
    target_h = int(cfg.retention.video_height or 0)
    target_fps = int(getattr(cfg.retention, "video_fps", 0) or 0)
    filters = []
    if target_fps > 0:
        filters.append(f"fps={target_fps}")
    if target_h and h and h > target_h:
        filters.append(f"scale=-2:{target_h}")
    vf = ["-vf", ",".join(filters)] if filters else []

    tmp = src.with_suffix(".compressing.mp4")
    if tmp.exists():
        tmp.unlink()

    def build(encoder: hwaccel.VideoEncoder) -> list[str]:
        qargs, _ = hwaccel.quality_args(encoder.name, int(cfg.retention.video_crf))
        return [cfg.paths.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-nostdin", "-i", str(src), *vf,
                "-c:v", encoder.name, *qargs,
                *hwaccel.preset_args(encoder.name, cfg.retention.video_preset),
                "-movflags", "+faststart", "-c:a", "copy", str(tmp)]

    enc = hwaccel.detect(cfg)
    _, qlabel = hwaccel.quality_args(enc.name, int(cfg.retention.video_crf))
    fps_note = f", {target_fps}fps" if target_fps > 0 else ""
    _log(f"压缩 {src.name}（{h}p → {target_h or h}p{fps_note}, {enc.name} {qlabel}）")
    t0 = time.perf_counter()
    try:
        _run(build(enc))
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        if not enc.is_hw:
            return None, f"ffmpeg 失败：{exc}"
        # 硬件编码在真实文件上失败（驱动/源参数怪）：本进程退回 CPU 再试一次
        hwaccel.demote(cfg, enc)
        try:
            _run(build(hwaccel.detect(cfg)))
        except Exception as exc2:
            tmp.unlink(missing_ok=True)
            return None, f"ffmpeg 失败（CPU 回退也失败）：{exc2}"
    _log(f"  压缩完成（{time.perf_counter() - t0:.1f}s）")

    ok, why = verify(cfg, tmp, src_dur)
    if not ok:
        tmp.unlink(missing_ok=True)
        return None, f"压缩产物校验不通过（{why}），已保留原文件"

    before, after = src.stat().st_size, tmp.stat().st_size
    src.unlink()
    tmp.rename(src)
    _log(f"  {before / 1048576:.1f}MB → {after / 1048576:.1f}MB（{before / max(after,1):.1f}x）")
    return src, f"{before / 1048576:.1f}MB → {after / 1048576:.1f}MB"


def apply_retention(cfg: Config, src: Path, keep: str | None = None,
                    have_backup: bool = True, force: bool = False) -> tuple[Path | None, str]:
    """按策略处理一节视频。返回 (保留的文件或 None, 说明)。"""
    keep = keep or cfg.retention.keep_video
    if keep == KEEP_ORIGINAL:
        return src, "保留原始"
    if keep == KEEP_COMPRESSED:
        return compress_video(cfg, src, force=force)
    if keep == KEEP_NONE:
        if not have_backup:
            return src, "无音频/关键帧备份，保留原文件"
        size = src.stat().st_size / 1048576
        src.unlink()
        return None, f"已删除原始视频（释放 {size:.1f}MB）"
    return src, f"未知策略 {keep}，保留原文件"
