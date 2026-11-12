"""复习资料生成：MOC 结构、增量缓存、过期页清理、总览缓存与失败降级。

全程 tmp_path，不联网（总览调用一律 monkeypatch gateway.chat）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass import gateway                          # noqa: E402
from smartclass import review as review_mod             # noqa: E402
from smartclass.config import Config                    # noqa: E402

COURSE = "线性代数A"
OVERVIEW_BODY = "## 课程知识框架\n\n板块甲 → 板块乙。\n\n## 重点与考点清单\n\n- [ ] 记住秩的定义\n"


def _cfg(tmp_path: Path, monkeypatch) -> Config:
    cfg = Config()
    monkeypatch.setattr(cfg.paths, "runs_root", str(tmp_path))
    return cfg


def _mk_lecture(root: Path, date: str, span: str, slot: str, *,
                notes: bool = True, homework: bool = True) -> Path:
    """造一个整课包：lecture.json + notes/homework（作业带「证据区」）。"""
    uid = f"{date}_{span}"
    d = root / COURSE / date
    d.mkdir(parents=True, exist_ok=True)
    # 同日多讲时后一节用 lecture_<uid>.json（与 workspace.LectureDir 一致）
    plain = d / "lecture.json"
    meta_path = plain
    if plain.is_file():
        old = json.loads(plain.read_text(encoding="utf-8"))
        if str(old.get("uid") or "") != uid:
            meta_path = d / f"lecture_{uid}.json"
    meta_path.write_text(json.dumps({
        "course": COURSE, "date": date, "uid": uid, "span": span,
        "start": f"{slot[:2]}:{slot[2:4]}:00", "end": "10:30:00",
        "parts": [], "layout": "flat",
    }, ensure_ascii=False), encoding="utf-8")
    if notes:
        (d / f"notes_{uid}.md").write_text(
            f"# 课堂笔记 · {COURSE} {date}\n\n> 由 `scls` 自动整理，仅供复习参考。\n\n"
            "## 矩阵的秩\n\n- **要点**：秩是非零子式的最高阶数。\n\n"
            "## 初等变换\n\n- **要点**：初等变换不改变秩。\n",
            encoding="utf-8")
    if homework:
        (d / f"homework_{uid}.md").write_text(
            f"# 作业要求汇总 · {COURSE} {date}\n\n"
            "> 本文件由 `scls` 自动归纳（转写 + 课件 OCR）。\n\n"
            "## 1. 作业形式\n- 独立完成\n\n"
            "---\n\n## 证据区（含关键词的原文行）\n\n```\n证据区里的原文行不要出现\n```\n",
            encoding="utf-8")
    return d


def _fake_chat(monkeypatch, body: str = OVERVIEW_BODY):
    calls: list[str] = []

    def fake(cfg, *, modality, messages, max_tokens, timeout=600, attempts=3,
             validate=None, label=""):
        calls.append(label)
        return body

    monkeypatch.setattr(review_mod.gateway, "chat", fake)
    return calls


# ---- 结构 ------------------------------------------------------------------- #

def test_build_writes_moc_and_pages(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _mk_lecture(tmp_path, "2026-09-04", "0800-1030", "0800")
    _mk_lecture(tmp_path, "2026-09-11", "0800-1030", "0800")
    _fake_chat(monkeypatch)

    n = review_mod.build(cfg, COURSE)
    assert n == 4  # README + 2 个讲次页 + 课程总览

    out = tmp_path / COURSE / "复习"
    moc = (out / "README.md").read_text(encoding="utf-8")
    assert "[[01-2026-09-04]]" in moc and "[[02-2026-09-11]]" in moc
    assert "| # | 日期 | 时段 | 内容 | 作业 | 笔记 |" in moc
    assert "共 2 讲" in moc

    page = (out / "01-2026-09-04.md").read_text(encoding="utf-8")
    assert "## 矩阵的秩" in page and "秩是非零子式的最高阶数" in page
    assert "证据区里的原文行不要出现" not in page
    assert "## 证据区" not in page
    assert "[[README|" in page
    assert "上一讲：—" in page and "[[02-2026-09-11]]" in page
    assert "作业要求汇总" not in page, "开头的 `# ` 标题应被去掉"
    assert "本文件由 `scls`" not in page, "开头的 `> ` 说明行应被去掉"


def test_no_lectures_is_noop(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _fake_chat(monkeypatch)
    assert review_mod.build(cfg, "没有这门课") == 0
    assert not (tmp_path / "没有这门课").exists()


def test_same_date_multiple_lectures_get_span_suffix(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _mk_lecture(tmp_path, "2026-09-04", "0800-0935", "0800")
    _mk_lecture(tmp_path, "2026-09-04", "1040-1215", "1040")
    _fake_chat(monkeypatch)

    review_mod.build(cfg, COURSE)
    out = tmp_path / COURSE / "复习"
    assert (out / "01-2026-09-04-0800-0935.md").is_file()
    assert (out / "02-2026-09-04-1040-1215.md").is_file()


# ---- 增量 ------------------------------------------------------------------- #

def test_build_is_incremental(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _mk_lecture(tmp_path, "2026-09-04", "0800-1030", "0800")
    _fake_chat(monkeypatch)

    review_mod.build(cfg, COURSE)
    out = tmp_path / COURSE / "复习"
    before = {p.name: p.stat().st_mtime_ns for p in out.glob("*.md")}

    assert review_mod.build(cfg, COURSE) == 0
    after = {p.name: p.stat().st_mtime_ns for p in out.glob("*.md")}
    assert before == after, "内容未变时不应重写（保留用户手改）"


def test_force_rewrites(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _mk_lecture(tmp_path, "2026-09-04", "0800-1030", "0800")
    _fake_chat(monkeypatch)

    review_mod.build(cfg, COURSE)
    out = tmp_path / COURSE / "复习"
    page = out / "01-2026-09-04.md"
    before = page.stat().st_mtime_ns

    assert review_mod.build(cfg, COURSE, force=True) >= 1
    assert page.stat().st_mtime_ns != before


def test_stale_page_removed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _mk_lecture(tmp_path, "2026-09-04", "0800-1030", "0800")
    _mk_lecture(tmp_path, "2026-09-11", "0800-1030", "0800")
    d3 = _mk_lecture(tmp_path, "2026-09-18", "0800-1030", "0800")
    _fake_chat(monkeypatch)

    review_mod.build(cfg, COURSE)
    out = tmp_path / COURSE / "复习"
    assert (out / "03-2026-09-18.md").is_file()

    import shutil
    shutil.rmtree(d3)
    review_mod.build(cfg, COURSE)
    assert not (out / "03-2026-09-18.md").exists()
    assert (out / "02-2026-09-11.md").is_file()


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _mk_lecture(tmp_path, "2026-09-04", "0800-1030", "0800")
    calls = _fake_chat(monkeypatch)

    assert review_mod.build(cfg, COURSE, dry_run=True) == 0
    assert not (tmp_path / COURSE / "复习").exists()
    assert calls == [], "dry-run 不应调用模型"


# ---- 课程总览 --------------------------------------------------------------- #

def test_overview_cached_by_hash(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    d = _mk_lecture(tmp_path, "2026-09-04", "0800-1030", "0800")
    calls = _fake_chat(monkeypatch)

    review_mod.build(cfg, COURSE)
    assert len(calls) == 1
    review_mod.build(cfg, COURSE)
    assert len(calls) == 1, "材料与提示词都没变，不应重复调用模型"

    notes = next(d.glob("notes_*.md"))
    notes.write_text(notes.read_text(encoding="utf-8") + "\n## 新增小节\n\n- **要点**：x\n",
                     encoding="utf-8")
    review_mod.build(cfg, COURSE)
    assert len(calls) == 2, "笔记变了，总览必须重算"

    overview = (tmp_path / COURSE / "复习" / "课程总览.md").read_text(encoding="utf-8")
    assert "## 课程知识框架" in overview and "返回 [[README|" in overview


def test_overview_disabled_does_not_call_model(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _mk_lecture(tmp_path, "2026-09-04", "0800-1030", "0800")
    calls = _fake_chat(monkeypatch)

    review_mod.build(cfg, COURSE, overview=False)
    assert calls == []
    assert not (tmp_path / COURSE / "复习" / "课程总览.md").exists()


def test_overview_failure_keeps_old_file(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    d = _mk_lecture(tmp_path, "2026-09-04", "0800-1030", "0800")
    _fake_chat(monkeypatch)
    review_mod.build(cfg, COURSE)
    target = tmp_path / COURSE / "复习" / "课程总览.md"
    old = target.read_text(encoding="utf-8")

    def boom(*a, **k):
        raise gateway.GatewayError("模型调用失败（课程总览）：HTTP 429",
                                   reasons=["m: HTTP 429"], rate_limited=True)

    monkeypatch.setattr(review_mod.gateway, "chat", boom)
    notes = next(d.glob("notes_*.md"))
    notes.write_text(notes.read_text(encoding="utf-8") + "\n## 又一节\n", encoding="utf-8")

    n = review_mod.build(cfg, COURSE)      # 不抛异常
    assert n == 2                          # README + 该讲页
    assert target.read_text(encoding="utf-8") == old, "失败时应保留上次结果"


def test_compact_for_overview_truncates_and_warns(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, monkeypatch)
    d = _mk_lecture(tmp_path, "2026-09-04", "0800-1030", "0800")
    notes = next(d.glob("notes_*.md"))
    notes.write_text("## 小节\n" + "- **要点**：" + "长" * 5000 + "\n", encoding="utf-8")

    lessons = review_mod._lessons(cfg, COURSE)
    text = review_mod._compact_for_overview(lessons)
    assert len(text) <= review_mod.LESSON_LIMIT + 100
    capsys.readouterr()

    # 多讲累积超上限时停止并告警
    many = [lessons[0]] * 40
    for i, le in enumerate(many):
        le.index = i + 1
    text = review_mod._compact_for_overview(many)
    assert len(text) <= review_mod.OVERVIEW_LIMIT + 200
    assert "（材料过多，后续讲次未纳入）" in text
    assert "材料过多" in capsys.readouterr().err


# ---- 文本清理 --------------------------------------------------------------- #

def test_strip_notes_keeps_from_first_heading2():
    raw = "# 课堂笔记 · 课\n\n> 由 scls 生成。\n\n## 甲\n- a\n\n## 乙\n- b\n"
    assert review_mod._strip_notes(raw) == "## 甲\n- a\n\n## 乙\n- b"
    assert review_mod._strip_notes("") == ""


def test_strip_evidence_cuts_tail():
    raw = ("# 作业要求汇总\n\n> 本文件由 scls 生成。\n\n## 1. 形式\n- x\n\n---\n\n"
           "## 证据区（含关键词的原文行）\n\n```\n原文\n```\n")
    out = review_mod._strip_evidence(raw)
    assert out == "## 1. 形式\n- x"
    assert "证据区" not in out and "原文" not in out


def test_title_falls_back_without_notes(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _mk_lecture(tmp_path, "2026-09-04", "0800-1030", "0800", notes=False)
    lessons = review_mod._lessons(cfg, COURSE)
    assert lessons[0].title == "第1讲"
    assert lessons[0].time_range == "08:00-10:30"


def test_lesson_without_homework_marks_dash(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _mk_lecture(tmp_path, "2026-09-04", "0800-1030", "0800", homework=False)
    _fake_chat(monkeypatch)
    review_mod.build(cfg, COURSE)
    moc = (tmp_path / COURSE / "复习" / "README.md").read_text(encoding="utf-8")
    row = next(ln for ln in moc.splitlines() if ln.startswith("| 1 |"))
    assert "| — |" in row


@pytest.mark.parametrize("name,expected", [
    ("01-2026-09-04.md", True),
    ("02-2026-09-04-0800-0835.md", True),
    ("README.md", False),
    ("课程总览.md", False),
    ("01-2026-09-04.txt", False),
])
def test_page_name_pattern(name, expected):
    assert bool(review_mod._PAGE_RE.match(name)) is expected
