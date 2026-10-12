"""解析与工具函数的单元测试（不依赖网络/浏览器）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass.discover import sessions_from_payloads                        # noqa: E402
from smartclass.digest import keyword_lines                              # noqa: E402
from smartclass.streams import classify                                  # noqa: E402
from smartclass.workspace import safe_name, RunDir                       # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_sessions_from_public_videos_fixture():
    payload = load_fixture("get_public_videos.json")
    sessions = sessions_from_payloads([payload])
    by_id = {s.new_id: s for s in sessions}

    assert "1d864ea7a8644498af2fe54e050ad2e4" in by_id
    s = by_id["1d864ea7a8644498af2fe54e050ad2e4"]
    assert s.date == "2026-10-12"
    assert s.start == "09:45:00"
    assert s.end == "10:30:00"
    assert s.slot == "0945-1030"
    assert "化工导论" in s.text


def test_sessions_skip_short_ids():
    payload = {"Value": {"Data": [{"NewID": "abc", "Title": "x"}]}}
    assert sessions_from_payloads([payload]) == []


def test_sessions_dedupe():
    item = {"NewID": "aabbccddeeff0011", "Title": "化工导论", "StartTime": "2026-10-12 09:45:00"}
    payload = {"Value": {"Data": [item, dict(item)]}}
    assert len(sessions_from_payloads([payload])) == 1


def test_keyword_lines_filters():
    text = "今天天气不错\n这个作业要写3000字\n随便聊聊"
    hits = keyword_lines(text, ["作业", "字数"])
    assert "这个作业要写3000字" in hits
    assert "今天天气不错" not in hits


def test_classify_tracks():
    assert classify("https://v.smartclass.cn:8010/vod4/2026/10/12/1/1/VGA.mp4") == "vga"
    assert classify("https://v.smartclass.cn:8010/vod4/2026/10/12/1/1/Video1.mp4") == "video1"
    assert classify("https://x/other.mp4") == "other"


def test_safe_name_strips_illegal_chars():
    assert safe_name('化工/导论: 第1节*?') == "化工_导论_第1节"
    assert safe_name("") == "未命名"


def test_rundir_layout(tmp_path):
    run = RunDir(tmp_path / "a" / "b").ensure()
    assert run.videos.is_dir() and run.transcript.is_dir()
    run.save_meta({"new_id": "x"})
    assert run.load_meta()["new_id"] == "x"
