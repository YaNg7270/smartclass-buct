"""转写并发：同一小节的音频块应并发跑，且 full.txt 顺序不变。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass import asr as asr_mod                   # noqa: E402
from smartclass import audio as audio_mod               # noqa: E402
from smartclass import scheduler                        # noqa: E402
from smartclass.config import Config                    # noqa: E402
from smartclass.workspace import RunDir                 # noqa: E402


def _setup(monkeypatch, tmp_path: Path, n_chunks: int = 5):
    cfg = Config()
    cfg.concurrency.gateway = n_chunks
    run = RunDir(tmp_path / "part").ensure()
    src = tmp_path / "audio.mp3"
    src.write_bytes(b"x")

    calls: list[str] = []

    def fake_split(cfg, src, outdir, chunk_seconds=600):
        outdir.mkdir(parents=True, exist_ok=True)
        chunks = []
        for i in range(n_chunks):
            p = outdir / f"part_{i:03d}.mp3"
            p.write_bytes(b"x")
            chunks.append(audio_mod.Chunk(path=p, offset=i * 600, dur=600))
        return chunks, n_chunks * 600

    def fake_post(cfg, model, path, offset, duration):
        calls.append(path.name)
        time.sleep(0.2)
        return f"text-{path.stem}", ""

    monkeypatch.setattr(asr_mod, "check_gateway", lambda cfg: None)
    monkeypatch.setattr(asr_mod.audio_mod, "split_chunks", fake_split)
    monkeypatch.setattr(asr_mod.audio_mod, "duration", lambda cfg, p: 600.0)
    monkeypatch.setattr(asr_mod, "_post_gateway", fake_post)
    return cfg, run, src, calls


def test_clean_text_strips_cjk_spaces():
    """快模型有时会在汉字间插空格，清理后不能留下多余空格。"""
    assert asr_mod._clean_text("缺 29 个 。 这次 课 应该 是") == "缺 29个。这次课应该是"
    assert asr_mod._clean_text("[00:00] 呃 ， 这个 反正") == "[00:00] 呃，这个反正"
    assert asr_mod._clean_text("normal English text") == "normal English text"


def test_transcribe_parallel_and_ordered(monkeypatch, tmp_path):
    cfg, run, src, calls = _setup(monkeypatch, tmp_path, 5)
    t0 = time.perf_counter()
    try:
        out = asr_mod.transcribe(cfg, run, src, provider="gateway")
    finally:
        scheduler.shutdown()
    elapsed = time.perf_counter() - t0

    assert len(calls) == 5
    assert elapsed < 0.75, f"5 块串行应约 1.0s，实际 {elapsed:.2f}s（并发没生效？）"
    text = out.read_text(encoding="utf-8")
    positions = [text.index(f"text-part_{i:03d}") for i in range(5)]
    assert positions == sorted(positions), "full.txt 必须按块顺序拼接"


def test_transcribe_reuses_cache(monkeypatch, tmp_path):
    cfg, run, src, calls = _setup(monkeypatch, tmp_path, 3)
    try:
        asr_mod.transcribe(cfg, run, src, provider="gateway")
        first = len(calls)
        asr_mod.transcribe(cfg, run, src, provider="gateway")
    finally:
        scheduler.shutdown()
    assert first == 3
    assert len(calls) == 3, "第二次应全部复用 part_NNN.json"
