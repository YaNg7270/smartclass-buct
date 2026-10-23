"""ffmpeg 视频编码器探测：优先用 GPU（QSV/NVENC/AMF），不可用或失败时退回 CPU。

为什么只加速编码、不加速解码（本机实测，Intel Arc B390 + 1080p 课件流）：
  - libx264 crf33 veryfast 编码原始课件 ≈ 130 fps（一节课约 100 秒）
  - h264_qsv  global_quality 30 编码 ≈ 410 fps，一节课约 45 秒，且几乎不占 CPU
  - -hwaccel d3d11va 解码反而更慢（编码 657 vs 804 fps；抽帧 6.2s vs 1.0s）：
    1080p H.264 软解本来就快，硬解还要在显存与内存之间来回拷贝。
  结论：编码走硬件、解码保持软件，是这类课件流的最优组合。
QSV 的质量参数用 -global_quality（ICQ），与 x264 的 CRF 不是同一标尺；
原始课件实测：gq = crf - 3 时体积/SSIM 与 x264 crf 对齐（gq30 ≈ crf33，SSIM 甚至略高）。
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

from .config import Config

# 各硬件编码器的探测参数（320x180 黑帧 3 帧，几百毫秒内完成）
_SPECS: dict[str, tuple[str, list[str]]] = {
    "qsv": ("h264_qsv", ["-global_quality", "28", "-preset", "veryfast"]),
    "nvenc": ("h264_nvenc", ["-rc", "vbr", "-cq", "28", "-b:v", "0", "-preset", "p4"]),
    "amf": ("h264_amf", ["-rc", "cqp", "-qp_i", "28", "-qp_p", "28", "-qp_b", "28"]),
}
_AUTO_ORDER = ("qsv", "nvenc", "amf")

# (ffmpeg 路径, 用户偏好) -> 探测结果；进程内复用，避免每节视频重复探测
_cache: dict[tuple[str, str], "VideoEncoder"] = {}


@dataclass(frozen=True)
class VideoEncoder:
    kind: str   # qsv | nvenc | amf | cpu
    name: str   # ffmpeg 编码器名，如 h264_qsv / libx264

    @property
    def is_hw(self) -> bool:
        return self.kind != "cpu"


def _log(msg: str) -> None:
    print(f"[hwaccel] {msg}", file=sys.stderr, flush=True)


def _probe(cfg: Config, kind: str) -> bool:
    """真跑一次极小规模的编码，确认驱动/编码器真的可用（不只是编译进了 ffmpeg）。"""
    name, args = _SPECS[kind]
    cmd = [cfg.paths.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
           "-f", "lavfi", "-i", "color=c=black:s=320x180:d=0.2",
           "-frames:v", "3", "-an", "-c:v", name, *args, "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=20)
        return proc.returncode == 0
    except Exception:
        return False


def _cpu_encoder(cfg: Config) -> VideoEncoder:
    return VideoEncoder("cpu", cfg.retention.video_codec or "libx264")


def detect(cfg: Config) -> VideoEncoder:
    """按配置选择编码器；auto = qsv → nvenc → amf → cpu。"""
    pref = (getattr(cfg.retention, "video_encoder", "auto") or "auto").lower()
    key = (cfg.paths.ffmpeg, pref)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    if pref in ("cpu", "libx264", "none"):
        enc = _cpu_encoder(cfg)
    elif pref in _SPECS:
        if _probe(cfg, pref):
            enc = VideoEncoder(pref, _SPECS[pref][0])
        else:
            _log(f"指定的硬件编码器 {_SPECS[pref][0]} 不可用，回退 CPU")
            enc = _cpu_encoder(cfg)
    else:  # auto
        enc = _cpu_encoder(cfg)
        for kind in _AUTO_ORDER:
            if _probe(cfg, kind):
                enc = VideoEncoder(kind, _SPECS[kind][0])
                break
        else:
            _log("未检测到可用硬件编码器（QSV/NVENC/AMF），使用 CPU 编码")
    if enc.is_hw:
        _log(f"使用硬件编码器 {enc.name}")
    _cache[key] = enc
    return enc


def demote(cfg: Config, enc: VideoEncoder) -> None:
    """硬件编码在真实文件上失败时，本进程内退回 CPU，避免反复踩坑。"""
    if enc.is_hw:
        _log(f"{enc.name} 编码失败，本次运行改用 CPU")
        _cache[(cfg.paths.ffmpeg, (getattr(cfg.retention, "video_encoder", "auto") or "auto").lower())] = _cpu_encoder(cfg)


def quality_args(name: str, crf: int) -> tuple[list[str], str]:
    """把统一的 CRF 语义映射到各编码器的质量参数，返回 (参数, 人类可读标签)。"""
    if name.endswith("_qsv"):
        q = max(1, crf - 3)
        return ["-global_quality", str(q)], f"gq{q}"
    if name.endswith("_nvenc"):
        return ["-rc", "vbr", "-cq", str(crf), "-b:v", "0"], f"cq{crf}"
    if name.endswith("_amf"):
        return (["-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf), "-qp_b", str(crf)],
                f"qp{crf}")
    return ["-crf", str(crf)], f"crf{crf}"


def preset_args(name: str, preset: str) -> list[str]:
    """各编码器的 preset 命名不同，做最小映射。"""
    if name.endswith("_nvenc"):
        table = {"ultrafast": "p1", "superfast": "p1", "veryfast": "p2", "faster": "p3",
                 "fast": "p3", "medium": "p4", "slow": "p6", "slower": "p7", "veryslow": "p7"}
        return ["-preset", table.get(preset, "p4")]
    if name.endswith("_amf"):
        table = {"ultrafast": "speed", "veryfast": "speed", "faster": "speed",
                 "fast": "speed", "medium": "balanced", "slow": "quality", "veryslow": "quality"}
        return ["-quality", table.get(preset, "balanced")]
    return ["-preset", preset]
