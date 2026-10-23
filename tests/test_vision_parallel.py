"""OCR 并发：批次并发跑，但落盘顺序必须还是 1..N。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass import scheduler                        # noqa: E402
from smartclass import vision as vision_mod             # noqa: E402
from smartclass.config import Config                    # noqa: E402
from smartclass.workspace import RunDir                 # noqa: E402


def test_scan_parallel_and_ordered(monkeypatch, tmp_path):
    cfg = Config()
    cfg.vision.batch = 2
    cfg.concurrency.gateway = 4

    frames = []
    for i in range(6):
        p = tmp_path / f"f_{i + 1:03d}.jpg"
        p.write_bytes(b"x")
        frames.append(p)

    calls: list[str] = []

    def fake_ocr(cfg, images, prompt):
        calls.append(images[0][0])
        time.sleep(0.2)
        return f"batch-{images[0][0]}-{images[-1][0]}"

    monkeypatch.setattr(vision_mod.audio_mod, "extract_frames", lambda *a, **k: frames)
    monkeypatch.setattr(vision_mod.audio_mod, "dedupe_frames",
                        lambda fr, th: list(enumerate(fr)))
    monkeypatch.setattr(vision_mod, "_ocr_batch", fake_ocr)

    run = RunDir(tmp_path / "part").ensure()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")

    t0 = time.perf_counter()
    try:
        out = vision_mod.scan(cfg, run, video)
    finally:
        scheduler.shutdown()
    elapsed = time.perf_counter() - t0

    assert len(calls) == 3
    assert elapsed < 0.45, f"3 批串行应约 0.6s，实际 {elapsed:.2f}s（并发没生效？）"
    text = out.read_text(encoding="utf-8")
    assert text.index("BATCH 1") < text.index("BATCH 2") < text.index("BATCH 3")
    # 时间戳用的是原始帧序号（0/2/4 × 45s）
    assert "[00:00 .. 00:45]" in text
    assert "[01:30 .. 02:15]" in text
