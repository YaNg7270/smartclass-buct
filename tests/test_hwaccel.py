"""编码器探测 / 参数映射的单元测试（不依赖真实 GPU）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                                      # noqa: E402

from smartclass import hwaccel                                     # noqa: E402
from smartclass.config import Config                               # noqa: E402


@pytest.fixture(autouse=True)
def _clean_cache():
    hwaccel._cache.clear()
    yield
    hwaccel._cache.clear()


def _cfg(pref: str = "auto") -> Config:
    cfg = Config()
    cfg.retention.video_encoder = pref
    return cfg


def test_explicit_cpu_skips_probe(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("不应探测")
    monkeypatch.setattr(hwaccel, "_probe", boom)
    enc = hwaccel.detect(_cfg("cpu"))
    assert enc.kind == "cpu" and enc.name == "libx264"


def test_auto_prefers_qsv(monkeypatch):
    monkeypatch.setattr(hwaccel, "_probe", lambda cfg, kind: kind == "qsv")
    enc = hwaccel.detect(_cfg())
    assert enc.kind == "qsv" and enc.name == "h264_qsv"


def test_auto_falls_back_to_cpu(monkeypatch):
    monkeypatch.setattr(hwaccel, "_probe", lambda cfg, kind: False)
    enc = hwaccel.detect(_cfg())
    assert enc.kind == "cpu"


def test_explicit_hw_unavailable_falls_back(monkeypatch):
    monkeypatch.setattr(hwaccel, "_probe", lambda cfg, kind: False)
    enc = hwaccel.detect(_cfg("nvenc"))
    assert enc.kind == "cpu"


def test_detection_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(hwaccel, "_probe", lambda cfg, kind: calls.append(kind) or True)
    cfg = _cfg()
    a, b = hwaccel.detect(cfg), hwaccel.detect(cfg)
    assert a is b and calls == ["qsv"], "同一进程只探测一次"


def test_demote_switches_to_cpu(monkeypatch):
    monkeypatch.setattr(hwaccel, "_probe", lambda cfg, kind: True)
    cfg = _cfg()
    enc = hwaccel.detect(cfg)
    assert enc.is_hw
    hwaccel.demote(cfg, enc)
    assert hwaccel.detect(cfg).kind == "cpu"


@pytest.mark.parametrize("name,crf,args,tag", [
    ("libx264", 33, ["-crf", "33"], "crf33"),
    ("h264_qsv", 33, ["-global_quality", "30"], "gq30"),
    ("hevc_qsv", 33, ["-global_quality", "30"], "gq30"),
    ("h264_nvenc", 33, ["-rc", "vbr", "-cq", "33", "-b:v", "0"], "cq33"),
    ("h264_amf", 33, ["-rc", "cqp"], "qp33"),
])
def test_quality_args(name, crf, args, tag):
    got, label = hwaccel.quality_args(name, crf)
    assert got[:len(args)] == args and label == tag


def test_preset_mapping():
    assert hwaccel.preset_args("h264_nvenc", "veryfast") == ["-preset", "p2"]
    assert hwaccel.preset_args("h264_amf", "veryfast") == ["-quality", "speed"]
    assert hwaccel.preset_args("h264_qsv", "veryfast") == ["-preset", "veryfast"]
    assert hwaccel.preset_args("libx264", "medium") == ["-preset", "medium"]
