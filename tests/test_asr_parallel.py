"""转写并发：同一小节的音频块应并发跑，且 full.txt 顺序不变。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass import asr as asr_mod                   # noqa: E402
from smartclass import audio as audio_mod               # noqa: E402
from smartclass import bench                            # noqa: E402
from smartclass import merge                            # noqa: E402
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

    def fake_chat(cfg, *, modality, messages, max_tokens, timeout=600,
                  attempts=3, validate=None, label=""):
        calls.append(label)
        time.sleep(0.2)
        return f"text-{Path(label).stem}"

    monkeypatch.setattr(asr_mod, "check_gateway", lambda cfg: None)
    monkeypatch.setattr(asr_mod.audio_mod, "split_chunks", fake_split)
    monkeypatch.setattr(asr_mod.audio_mod, "duration", lambda cfg, p: 600.0)
    monkeypatch.setattr(asr_mod.gateway, "chat", fake_chat)
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


def _dashscope_sample(monkeypatch, tmp_path):
    """整块维持历史命名，模拟每份 MP3 容器增加 72 毫秒编码填充。"""
    cfg = Config()
    cfg.asr.protocol = "dashscope"
    cfg.asr.model = "qwen-audio-3.1-asr-flash"
    run = RunDir(tmp_path / "part").ensure()
    src = tmp_path / "sample.mp3"
    durations = {}
    # 保存实际内容起点，验证不同逻辑块在同时切片时不会串用同名文件。
    starts = {}
    chunks = []
    for index, seconds in enumerate((600, 600, 15)):
        path = run.audio / "chunks" / f"part_{index:03d}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"mp3")
        starts[path] = index * 600
        durations[path] = seconds + 0.072
        chunks.append(audio_mod.Chunk(path, starts[path], seconds))

    def cut(args):
        source = Path(args[args.index("-i") + 1])
        cuts = [int(s) for s in args[args.index("-segment_times") + 1].split(",")]
        for index, (a, b) in enumerate(zip([0, *cuts], [*cuts, durations[source]])):
            dest = Path(args[-1] % index)
            dest.write_bytes(b"mp3")
            starts[dest] = starts[source] + a
            durations[dest] = b - a + 0.072

    monkeypatch.setattr(asr_mod, "check_gateway", lambda cfg: None)
    monkeypatch.setattr(audio_mod, "duration", lambda cfg, p: durations[p])
    monkeypatch.setattr(audio_mod, "_run", cut)
    monkeypatch.setattr(audio_mod, "split_chunks", lambda *args: (chunks, 1215))
    return cfg, run, src, starts, durations


def test_dashscope_resume_keeps_logical_blocks_and_timeline(monkeypatch, tmp_path):
    """复用已经完成的新块；旧协议和失败的块需补转。整课和小节各只平移一次。"""
    cfg, run, src, starts, durations = _dashscope_sample(monkeypatch, tmp_path)
    for i in range(3):
        (run.transcript / f"part_{i:03d}.json").write_text('{"text": "原有 Gemini 缓存"}', encoding="utf-8")
    run.full_transcript.write_text("原有整节输出", encoding="utf-8")
    calls = []
    fail = True

    def chat(cfg, *, audio_file, messages, **kwargs):
        assert messages == [] and audio_file.is_file() and durations[audio_file] <= 240
        start = int(starts[audio_file])
        calls.append(start)
        if fail and start == 1200:
            raise asr_mod.gateway.GatewayError("测试中断", ["模型: HTTP 403: denied"])
        return "SILENCE" if start == 201 else f"[00:02] 内容{start}"

    monkeypatch.setattr(asr_mod.gateway, "chat", chat)
    try:
        with pytest.raises(RuntimeError, match="403"):
            asr_mod.transcribe(cfg, run, src, force=True)
        first = sorted(calls)
        assert first == [0, 201, 402, 600, 801, 1002, 1200]
        assert run.full_transcript.read_text(encoding="utf-8") == "原有整节输出"
        fail = False
        calls.clear()
        out = asr_mod.transcribe(cfg, run, src)
        assert calls == [1200]
        partial = json.loads((run.transcript / "part_001.json").read_text(encoding="utf-8"))
        assert partial["protocol"] == "dashscope" and partial["requested_model"] == cfg.asr.model
        assert partial["offset"] == 600 and "[03:23] 内容801" in partial["text"]
        text = out.read_text(encoding="utf-8")
        assert "[13:23] 内容801" in text and "[20:02] 内容1200" in text
        assert "SILENCE" not in text and "Gemini" not in text
        merged = merge.merge_transcripts([merge.PartRef("第二小节", run, 2700)], tmp_path / "lecture.txt")
        assert "[58:23] 内容801" in merged.read_text(encoding="utf-8")
        asr_mod.transcribe(cfg, run, src)
        assert calls == [1200]
        calls.clear()
        asr_mod.transcribe(cfg, run, src, force=True)
        assert sorted(calls) == first
        cfg.asr.protocol = "chat"
        assert asr_mod.transcribe(cfg, run, src).read_text(encoding="utf-8") == text
        assert sorted(calls) == first
    finally:
        scheduler.shutdown()


def test_complete_legacy_cache_survives_model_upgrade(monkeypatch, tmp_path):
    """日常 resume 不能重新计费改写历史成品，仅缺 full.txt 时也从现有 JSON 恢复。"""
    cfg, run, src, _, _ = _dashscope_sample(monkeypatch, tmp_path)
    for i in range(3):
        cached = {"index": i, "offset": i * 600, "dur": 600, "text": f"[00:02] 旧稿{i}"}
        (run.transcript / f"part_{i:03d}.json").write_text(json.dumps(cached), encoding="utf-8")
    run.full_transcript.write_text("保留完整旧稿", encoding="utf-8")
    monkeypatch.setattr(asr_mod.gateway, "chat", lambda *a, **kw: pytest.fail("不应重新识别"))
    assert asr_mod.transcribe(cfg, run, src).read_text(encoding="utf-8") == "保留完整旧稿"
    run.full_transcript.unlink()
    rebuilt = asr_mod.transcribe(cfg, run, src).read_text(encoding="utf-8")
    assert "[10:02] 旧稿1" in rebuilt and "[20:02] 旧稿2" in rebuilt
    run.full_transcript.write_text("", encoding="utf-8")
    assert asr_mod.transcribe(cfg, run, src).read_text(encoding="utf-8") == rebuilt


def test_bench_finds_part_from_lecture_directory(tmp_path):
    """日期包、课程根目录和直接指定的小节目录须选择同一份基准样本。"""
    lecture = tmp_path / "物理化学" / "2026-10-19"
    part = lecture / "parts" / "0945-1030"
    chunks = part / "audio" / "chunks"
    chunks.mkdir(parents=True)
    (chunks / "part_000.mp3").write_bytes(b"audio")
    assert [bench.pick_part(p) for p in (part, lecture, lecture.parent)] == [part, part, part]


def test_dashscope_bench_measures_named_model_for_whole_blocks(monkeypatch, tmp_path):
    """长块任一物理请求出错须计整块失败；缓存和命名模型均不能被备用模型改写。"""
    cfg, run, _, starts, durations = _dashscope_sample(monkeypatch, tmp_path)
    cfg.asr.fallback_models = ["不可调用"]
    baseline = run.transcript / "part_000.json"
    baseline.write_text('{"text": "旧基线"}', encoding="utf-8")
    seen = []

    def raw(cfg, *, model, audio_file, messages, **kwargs):
        assert audio_file.is_file() and durations[audio_file] <= 240 and messages == []
        seen.append((model, int(starts[audio_file])))
        if model == "待测甲" and starts[audio_file] == 201:
            return None, "片段失败"
        return "SILENCE", ""

    monkeypatch.setattr(asr_mod.gateway, "raw_chat", raw)
    try:
        failed, good = bench.bench_asr(cfg, run.root, ["待测甲", "待测乙"], [2])
    finally:
        scheduler.shutdown()
    for model in ("待测甲", "待测乙"):
        assert sorted(start for m, start in seen if m == model) == [0, 201, 402, 600, 801, 1002, 1200]
    assert len(seen) == 14
    assert failed["empty"] == 1 and failed["reasons"] == ["片段失败"]
    assert good["empty"] == 0 and good["chars"] == len("SILENCE") * 3
    assert baseline.read_text(encoding="utf-8") == '{"text": "旧基线"}'


def test_retry_halves_shift_timestamps_within_logical_block(monkeypatch, tmp_path):
    """失败重切时后半段的时间只在当前片内平移，不能叠加逻辑块或课程起点。"""
    cfg = Config()
    cfg.asr.protocol = "dashscope"
    paths = [tmp_path / name for name in ("source.mp3", "left.mp3", "right.mp3")]
    lengths = dict(zip(paths, (180.0, 90.0, 90.0)))
    monkeypatch.setattr(audio_mod, "duration", lambda cfg, path: lengths[path])
    monkeypatch.setattr(asr_mod, "_split_audio", lambda cfg, path, dest: paths[1:])

    def chat(cfg, *, audio_file, **kwargs):
        if audio_file == paths[0]:
            raise asr_mod.gateway.GatewayError("测试空结果", ["模型: 空文本"])
        return "[00:01] 一句"

    monkeypatch.setattr(asr_mod.gateway, "chat", chat)
    assert asr_mod._transcribe_segment(cfg, paths[0], 600) == "[00:01] 一句\n[01:31] 一句"
