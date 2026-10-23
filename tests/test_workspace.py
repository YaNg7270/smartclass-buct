"""扁平目录布局与迁移。

新布局：data/<课程>/<日期>/ 直接放内容，homework/notes 带 `<日期>_<时段>` 后缀；
同一天有多节整课时，后续的 transcript/slides/lecture.json 自动加后缀。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass.config import Config                 # noqa: E402
from smartclass.migrate import flatten               # noqa: E402
from smartclass.workspace import lecture_dir_for     # noqa: E402


def _cfg(tmp: Path) -> Config:
    c = Config()
    c.paths.runs_root = str(tmp)
    return c


def test_lecture_dir_is_flat_with_uid(tmp_path):
    d = lecture_dir_for(_cfg(tmp_path), "物理化学(Ⅰ)", "2026-10-16", "0800-1030")
    assert d.root == tmp_path / "物理化学(Ⅰ)" / "2026-10-16"
    assert d.uid == "2026-10-16_0800-1030"
    assert d.homework_path.name == "homework_2026-10-16_0800-1030.md"
    assert d.notes_path.name == "notes_2026-10-16_0800-1030.md"
    assert d.transcript_path.name == "transcript.txt"
    assert d.slides_path.name == "slides.txt"
    assert d.part("0945-1030").homework_path.name == "homework_2026-10-16_0945-1030.md"
    assert d.part("0945-1030").notes_path.name == "notes_2026-10-16_0945-1030.md"


def test_second_lecture_same_day_gets_suffix(tmp_path):
    a = lecture_dir_for(_cfg(tmp_path), "物理化学(Ⅰ)", "2026-10-16", "0800-1030").ensure()
    a.save_meta({"course": "物理化学(Ⅰ)", "date": "2026-10-16", "span": "0800-1030"})

    b = lecture_dir_for(_cfg(tmp_path), "物理化学(Ⅰ)", "2026-10-16", "1400-1530").ensure()
    assert b.meta_path.name == "lecture_2026-10-16_1400-1530.json"
    assert b.transcript_path.name == "transcript_2026-10-16_1400-1530.txt"
    assert b.slides_path.name == "slides_2026-10-16_1400-1530.txt"
    assert b.homework_path.name == "homework_2026-10-16_1400-1530.md"
    # 第一节仍然用无后缀文件
    assert a.meta_path.name == "lecture.json"
    assert a.transcript_path.name == "transcript.txt"


def _old_lecture(root: Path, date: str, span: str, slot: str) -> Path:
    d = root / "课程" / date / span
    (d / "parts" / slot).mkdir(parents=True)
    (d / "parts" / slot / "meta.json").write_text("{}", encoding="utf-8")
    (d / "lecture.json").write_text(json.dumps({
        "course": "课程", "date": date, "span": span,
        "parts": [{"slot": slot, "start": "08:00:00", "end": "08:45:00"}],
    }), encoding="utf-8")
    (d / "homework.md").write_text("HW", encoding="utf-8")
    (d / "notes.md").write_text("NOTES", encoding="utf-8")
    (d / "transcript.txt").write_text("T", encoding="utf-8")
    (d / "slides.txt").write_text("S", encoding="utf-8")
    (d / "parts" / slot / "homework.md").write_text("PHW", encoding="utf-8")
    (d / "parts" / slot / "notes.md").write_text("PNOTES", encoding="utf-8")
    return d


def test_flatten_moves_content_up(tmp_path):
    cfg = _cfg(tmp_path)
    old = _old_lecture(tmp_path, "2026-10-16", "0800-1030", "0800-0845")
    assert flatten(cfg, str(tmp_path)) == (1, 2)

    date_dir = tmp_path / "课程" / "2026-10-16"
    assert (date_dir / "homework_2026-10-16_0800-1030.md").read_text(encoding="utf-8") == "HW"
    assert (date_dir / "notes_2026-10-16_0800-1030.md").read_text(encoding="utf-8") == "NOTES"
    assert (date_dir / "transcript.txt").read_text(encoding="utf-8") == "T"
    assert (date_dir / "slides.txt").read_text(encoding="utf-8") == "S"
    assert (date_dir / "parts" / "0800-0845" / "meta.json").is_file()
    assert (date_dir / "parts" / "0800-0845" /
            "homework_2026-10-16_0800-0845.md").read_text(encoding="utf-8") == "PHW"
    assert (date_dir / "parts" / "0800-0845" /
            "notes_2026-10-16_0800-0845.md").read_text(encoding="utf-8") == "PNOTES"
    assert not (date_dir / "parts" / "0800-0845" / "homework.md").exists()
    assert not old.exists()

    meta = json.loads((date_dir / "lecture.json").read_text(encoding="utf-8"))
    assert meta["uid"] == "2026-10-16_0800-1030"
    assert meta["layout"] == "flat"


def test_flatten_is_idempotent(tmp_path):
    cfg = _cfg(tmp_path)
    _old_lecture(tmp_path, "2026-10-16", "0800-1030", "0800-0845")
    assert flatten(cfg, str(tmp_path)) == (1, 2)
    assert flatten(cfg, str(tmp_path)) == (0, 0)


def test_flatten_two_lectures_same_day_do_not_collide(tmp_path):
    cfg = _cfg(tmp_path)
    _old_lecture(tmp_path, "2026-10-16", "0800-1030", "0800-0845")
    _old_lecture(tmp_path, "2026-10-16", "1400-1530", "1400-1445")
    assert flatten(cfg, str(tmp_path)) == (2, 4)

    date_dir = tmp_path / "课程" / "2026-10-16"
    assert (date_dir / "lecture.json").is_file()
    assert (date_dir / "lecture_2026-10-16_1400-1530.json").is_file()
    assert (date_dir / "transcript_2026-10-16_1400-1530.txt").is_file()
    assert (date_dir / "homework_2026-10-16_0800-1030.md").is_file()
    assert (date_dir / "homework_2026-10-16_1400-1530.md").is_file()
    assert (date_dir / "parts" / "0800-0845").is_dir()
    assert (date_dir / "parts" / "1400-1445").is_dir()
