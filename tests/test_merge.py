"""整课转写合并：时间戳平移测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass.merge import merge_transcripts, shift_timestamps, PartRef   # noqa: E402
from smartclass.workspace import RunDir                                     # noqa: E402


def test_shift_basic():
    # 第二小节从整课第 55 分钟开始（09:45 → 10:40）
    assert shift_timestamps("[02:13] 开始上课", 55 * 60) == "[57:13] 开始上课"


def test_shift_zero_is_identity():
    assert shift_timestamps("[02:13] 甲[10:05] 乙", 0) == "[02:13] 甲[10:05] 乙"


def test_shift_multiple_occurrences():
    out = shift_timestamps("[00:10] a [01:00] b", 600)   # +10 分钟
    assert out == "[10:10] a [11:00] b"


def test_shift_ignores_non_timestamp_brackets():
    assert shift_timestamps("[备注] 正文", 600) == "[备注] 正文"


def _write_chunks(run: RunDir, chunks):
    run.ensure()
    import json
    for i, (off, text) in enumerate(chunks):
        (run.transcript / f"part_{i:03d}.json").write_text(
            json.dumps({"index": i, "offset": off, "dur": 600, "text": text},
                       ensure_ascii=False), encoding="utf-8")


def test_merge_aligns_to_lecture_timeline(tmp_path):
    """两小节的转写合并后，时间戳应连续（第二节从 55 分钟起），不出现两个 [02:13]。"""
    p1 = RunDir(tmp_path / "parts" / "0945-1030")
    p2 = RunDir(tmp_path / "parts" / "1040-1125")
    _write_chunks(p1, [(0, "[02:13] 第一小节开头")])
    _write_chunks(p2, [(0, "[02:13] 第二小节开头")])

    out = tmp_path / "transcript.txt"
    merge_transcripts([
        PartRef(label="09:45-10:30", run=p1, base_seconds=0),
        PartRef(label="10:40-11:25", run=p2, base_seconds=55 * 60),
    ], out, "化工导论 2026-10-12 09:45-11:25")

    text = out.read_text(encoding="utf-8")
    assert "【09:45-10:30】" in text and "【10:40-11:25】" in text
    assert "[02:13] 第一小节开头" in text
    assert "[57:13] 第二小节开头" in text, "第二节的时间戳应按整课时间轴平移"
