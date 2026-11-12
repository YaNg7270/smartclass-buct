"""把已有的笔记 / 作业要求整理成可直接放进 Obsidian 的复习资料。

产物（`data/<课程>/复习/`）：

```
README.md          MOC 索引：讲次表 + 用法 + 复习建议
01-<日期>.md       每讲一页：本讲目录 + 笔记全文 + 作业正文 + 上/下讲链接
课程总览.md         LLM 课程级总览（[review] overview = true 时生成）
.review.json       缓存：每页内容 sha256 + 总览输入 sha256
```

内容全部来自本地文件（不重新转写、不重新 OCR）：每讲笔记全文 + 作业正文
（去掉「证据区」）+ 一次可选的课程总览 LLM 调用。链接一律用 Obsidian 的
短路径 `[[文件名]]`，整个目录拖进仓库即可用，不需要插件。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from . import catalog
from . import gateway
from .config import Config, runs_root
from .workspace import LectureDir, safe_name

PROMPT_VERSION = "1"          # 改总览提示词时递增，使缓存失效
TAG = "复习"                   # Obsidian tags 根

INDEX_NAME = "README"
OVERVIEW_NAME = "课程总览"
CACHE_NAME = ".review.json"

CACHE_VERSION = 1
OVERVIEW_LIMIT = 48000        # 总览材料的字符上限
LESSON_LIMIT = 2000           # 单讲材料截断
TITLE_MAX = 40

COURSE_OVERVIEW_TEMPLATE = """你是这门中国大学课程的复习助教。下面是按时间排序的各讲课堂笔记要点（来自逐字转写与课件 OCR）。
请只依据这些材料，整理一份课程级复习总览，严格按下面小节输出 Markdown（不要前言后语）：

## 课程知识框架
按知识板块（不是按讲次）归纳，写清各板块之间的先后依赖。

## 各讲脉络
逐讲一行：`- 第N讲（日期）：一句话主题 → 关键结论`。

## 核心概念与关系
列出贯穿多讲的核心概念，并写明它们之间的关系（包含/因果/对偶/推广）。
用 `[[讲次页文件名]]` 的形式在概念后面标注它首次出现的讲次页，例如 `[[03-2026-09-18]]`。

## 重点与考点清单
每条以 `- [ ] ` 开头（Obsidian 任务框），可勾选复习；按重要度排序，最多 15 条。

## 易混点与待补
指出笔记里相互矛盾、讲得含糊或明显缺失的部分；没有就写「暂未发现」。

硬性规则：
- 只能依据给定材料，不得补充材料以外的知识；材料不足的地方写「笔记未覆盖」。
- 术语、人名、公式、年份必须与材料一致。
- 不要输出本说明以外的任何内容。
"""

_PAGE_RE = re.compile(r"^\d+-\d{4}-\d{2}-\d{2}(-\d{4}-\d{4})?\.md$")
_SPAN_RE = re.compile(r"^(\d{2})(\d{2})-(\d{2})(\d{2})$")


@dataclass
class Lesson:
    index: int                 # 1 起
    date: str
    span: str                  # "0800-1030"
    uid: str
    lec: LectureDir
    notes_text: str            # 原始笔记文件内容（可为 ""）
    homework_text: str
    title: str                 # 由笔记里前 2~3 个 "## " 标题拼成，截断 40 字
    course: str = ""
    start: str = ""
    end: str = ""
    filename: str = ""         # 页面文件名（不含目录）

    @property
    def stem(self) -> str:
        return self.filename[:-3] if self.filename.endswith(".md") else self.filename

    @property
    def time_range(self) -> str:
        """"08:00-10:30"：优先 meta 的 start/end，其次从 span 推。"""
        if self.start and self.end:
            return f"{self.start[:5]}-{self.end[:5]}"
        m = _SPAN_RE.match(self.span or "")
        if m:
            return f"{m.group(1)}:{m.group(2)}-{m.group(3)}:{m.group(4)}"
        return self.span or ""


# --------------------------------------------------------------------------- #
# 文本清理
# --------------------------------------------------------------------------- #
def _strip_notes(text: str) -> str:
    """去掉开头连续的 `# ` 标题行与 `> 由 scls …` 说明行，保留从第一个 `## ` 起的正文。"""
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## "):
            return "\n".join(lines[i:]).strip("\n")
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].startswith(("# ", ">"))):
        i += 1
    return "\n".join(lines[i:]).strip("\n")


def _strip_evidence(text: str) -> str:
    """去掉 `## 证据区` 及其后的全部内容，并去掉开头的标题行与 `> 本文件由 scls …` 行。"""
    kept: list[str] = []
    for line in (text or "").splitlines():
        if line.strip().startswith("## 证据区"):
            break
        kept.append(line)
    while kept and (not kept[-1].strip() or kept[-1].strip() == "---"):
        kept.pop()
    i = 0
    while i < len(kept) and (not kept[i].strip() or kept[i].startswith(("# ", ">"))):
        i += 1
    return "\n".join(kept[i:]).strip("\n")


def _headings(text: str) -> list[str]:
    return [ln[3:].strip() for ln in (text or "").splitlines()
            if ln.startswith("## ") and ln[3:].strip()]


def _title_of(notes_text: str, index: int) -> str:
    heads = _headings(notes_text)[:3]
    if not heads:
        return f"第{index}讲"
    return " / ".join(heads)[:TITLE_MAX]


def _cell(s: str) -> str:
    """MOC 表格单元格：竖线会截断列，换行会截断行。"""
    return re.sub(r"\s+", " ", str(s or "")).replace("|", "\\|").strip()


# --------------------------------------------------------------------------- #
# 发现讲次
# --------------------------------------------------------------------------- #
def _lessons(cfg: Config, course: str) -> list[Lesson]:
    entries: list[dict] = []
    for d in catalog.list_dates(cfg, course):           # 倒序
        for entry in catalog.lectures_in(cfg, course, d.name):
            if not (entry["has"]["notes"] or entry["has"]["homework"]):
                continue
            entry = dict(entry, dir_name=d.name)
            entries.append(entry)
    entries.sort(key=lambda e: (e["dir_name"], e.get("start") or e.get("span") or "",
                                e.get("uid") or ""))

    total = len(entries)
    width = max(2, len(str(total)))
    per_date: dict[str, int] = {}
    for e in entries:
        per_date[e["dir_name"]] = per_date.get(e["dir_name"], 0) + 1

    lessons: list[Lesson] = []
    for i, e in enumerate(entries, 1):
        date = e["dir_name"]
        span = e.get("span") or ""
        uid = e.get("uid") or ""
        lec = LectureDir(runs_root(cfg) / safe_name(course, "课程") / date, uid=uid)
        notes_text = _read(lec.notes_path)
        homework_text = _read(lec.homework_path)
        name = f"{i:0{width}d}-{date}"
        if per_date[date] > 1 and span:
            name += f"-{safe_name(span, '时段')}"
        lessons.append(Lesson(
            index=i, date=date, span=span, uid=uid, lec=lec,
            notes_text=notes_text, homework_text=homework_text,
            title=_title_of(notes_text, i), course=course,
            start=e.get("start") or "", end=e.get("end") or "",
            filename=name + ".md"))
    return lessons


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8") if p.is_file() else ""
    except (OSError, UnicodeDecodeError):
        return ""


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
def _index_text(course: str, lessons: list[Lesson]) -> str:
    first, last = lessons[0].date, lessons[-1].date
    lines = [
        "---",
        f"course: {course}",
        "tags:",
        f"  - {TAG}",
        "  - MOC",
        "aliases:",
        f"  - {course} 复习",
        "---",
        "",
        f"# {course} · 复习索引",
        "",
        f"> [!abstract] 共 {len(lessons)} 讲，{first} ~ {last}。",
        f"> 跨讲综合见 [[{OVERVIEW_NAME}]]（若已生成）。",
        "",
        "| # | 日期 | 时段 | 内容 | 作业 | 笔记 |",
        "|---|---|---|---|---|---|",
    ]
    for le in lessons:
        hw = "✓" if le.homework_text.strip() else "—"
        lines.append(f"| {le.index} | {le.date} | {le.time_range} | {_cell(le.title)} "
                     f"| {hw} | [[{le.stem}]] |")
    lines += [
        "",
        "## 用法（Obsidian）",
        f"- 把 `data/{safe_name(course, '课程')}/复习/` 整个目录拖进仓库即可；"
        "`[[链接]]` 按文件名解析，无需插件。",
        "- 「重点与考点清单」是任务框，可在阅读视图里直接勾选。",
        "- 每讲页底部「来源」指向原始笔记/作业文件名，需要原文时回整课包查看。",
        "",
        "## 复习建议",
    ]
    lines += [
        f"1. 先看 [[{OVERVIEW_NAME}]] 的「课程知识框架」，建立板块顺序。",
        "2. 按讲次页的「本讲目录」回忆要点，答不出的回「课堂笔记」原文。",
        "3. 用「重点与考点清单」做最后一遍自测。",
        "",
    ]
    return "\n".join(lines)


def _lesson_text(le: Lesson, prev: Lesson | None, nxt: Lesson | None,
                 course: str) -> str:
    body = _strip_notes(le.notes_text)
    homework = _strip_evidence(le.homework_text)
    heads = _headings(le.notes_text)

    origin = []
    if le.lec.notes_path.is_file():
        origin.append(f"`{le.lec.notes_path.name}`")
    if le.lec.homework_path.is_file():
        origin.append(f"`{le.lec.homework_path.name}`")

    lines = [
        "---",
        f"course: {course}",
        f"date: {le.date}",
        f"span: {le.span}",
        "tags:",
        f"  - {TAG}",
        f"  - {safe_name(course, '课程')}",
        "---",
        "",
        f"# 第 {le.index} 讲 · {le.date} · {le.time_range}",
        "",
        f"> **来源**：`data/{safe_name(course, '课程')}/{le.date}/` ｜ "
        f"[[{INDEX_NAME}|{course} 复习索引]]",
        "> 原文：" + (" · ".join(origin) if origin else "（无）"),
        "",
        "## 本讲目录",
    ]
    if heads:
        lines += [f"- {h}" for h in heads]
    else:
        lines.append("- （本讲未生成笔记）")
    lines += [
        "",
        "## 课堂笔记",
        body or "（本讲未生成笔记）",
        "",
        "## 作业要求",
        homework or "（本讲未生成作业要求）",
        "",
        "---",
        f"上一讲：{f'[[{prev.stem}]]' if prev else '—'} ｜ "
        f"下一讲：{f'[[{nxt.stem}]]' if nxt else '—'} ｜ "
        f"返回 [[{INDEX_NAME}|{course} 复习索引]]",
        "",
    ]
    return "\n".join(lines)


def _overview_text(course: str, lessons: list[Lesson], body: str) -> str:
    return "\n".join([
        "---",
        f"course: {course}",
        "tags:",
        f"  - {TAG}",
        f"  - {OVERVIEW_NAME}",
        "---",
        "",
        f"# {course} · {OVERVIEW_NAME}",
        "",
        f"> 由 `scls review` 依据 {len(lessons)} 讲笔记归纳；仅覆盖笔记内容，未引入课外材料。",
        "",
        body.strip(),
        "",
        "---",
        f"返回 [[{INDEX_NAME}|{course} 复习索引]]",
        "",
    ])


def _compact_for_overview(lessons: list[Lesson]) -> str:
    """每讲取「日期 时段」+ 各 `## ` 标题 + 各 `- **要点**` 块（含缩进子项），
    单讲截断 2000 字符；整体累积到 48000 字符即停止，并记一条警告。"""
    blocks: list[str] = []
    total = 0
    dropped = 0
    for le in lessons:
        lines = [f"## 第{le.index}讲 {le.date} {le.time_range}".strip()]
        keep_indent = -1
        for raw in le.notes_text.splitlines():
            line = raw.rstrip()
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if line.startswith("## "):
                lines.append(stripped)
                keep_indent = -1
            elif stripped.startswith("- **要点**"):
                lines.append(stripped)
                keep_indent = indent
            elif keep_indent >= 0 and stripped and indent > keep_indent:
                lines.append(stripped)
            elif stripped and indent <= keep_indent:
                keep_indent = -1
        block = "\n".join(lines)[:LESSON_LIMIT]
        if total + len(block) > OVERVIEW_LIMIT:
            dropped = len(lessons) - len(blocks)
            break
        blocks.append(block)
        total += len(block) + 2
    text = "\n\n".join(blocks)
    if dropped:
        text += "\n\n（材料过多，后续讲次未纳入）"
        print(f"[review] 材料过多，课程总览只覆盖前 {len(blocks)} 讲（另有 {dropped} 讲未纳入）",
              file=sys.stderr)
    return text


def _overview_prompt(lessons: list[Lesson], material: str) -> tuple[str, str]:
    """(system, user)。`material` 由调用方算一次后同时用于缓存键与提示词。"""
    course = lessons[0].course if lessons else ""
    user = (f"课程：{course}\n共 {len(lessons)} 讲。下面是各讲笔记要点：\n\n"
            + material)
    return COURSE_OVERVIEW_TEMPLATE, user


# --------------------------------------------------------------------------- #
# 缓存
# --------------------------------------------------------------------------- #
def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_cache(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        return {}
    if not isinstance(data.get("inputs"), dict):
        data["inputs"] = {}
    return data


def _save_cache(path: Path, cache: dict) -> None:
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def build(cfg: Config, course: str, *, force: bool = False,
          overview: bool = True, dry_run: bool = False) -> int:
    """生成/更新一门课的复习目录，返回写入的文件数；无讲次时返回 0。"""
    lessons = _lessons(cfg, course)
    if not lessons:
        print(f"[review] {course}：没有可整理的讲次（缺 notes/homework）")
        return 0

    outdir = runs_root(cfg) / safe_name(course, "课程") / (cfg.review.folder or "复习")
    cache_path = outdir / CACHE_NAME
    cache = {} if force else _load_cache(cache_path)
    inputs: dict[str, str] = dict(cache.get("inputs") or {})

    # 1) 渲染每讲页 + MOC
    pages: dict[str, str] = {}
    pages[INDEX_NAME + ".md"] = _index_text(course, lessons)
    for i, le in enumerate(lessons):
        prev = lessons[i - 1] if i > 0 else None
        nxt = lessons[i + 1] if i + 1 < len(lessons) else None
        pages[le.filename] = _lesson_text(le, prev, nxt, course)

    # 2) 比对缓存：渲染结果没变且文件还在 → 跳过写盘。
    #    变了就整页覆盖（不做 diff 合并），所以手改只在「内容未变」时存活。
    pending: dict[str, str] = {}
    for name, text in pages.items():
        p = outdir / name
        if not force and p.is_file() and inputs.get(name) == _sha(text):
            continue
        pending[name] = text

    # 3) 过期页面（讲次被删/改名后遗留的）
    stale: list[Path] = []
    if outdir.is_dir():
        for p in sorted(outdir.glob("*.md")):
            if _PAGE_RE.match(p.name) and p.name not in pages:
                stale.append(p)

    if dry_run:
        for name in sorted(pending):
            print(f"[review] （dry-run）将写入 {outdir / name}")
        for p in stale:
            print(f"[review] （dry-run）将删除过期页面 {p}")
        if not pending and not stale:
            print(f"[review] {course}：内容未变化，无需写入")
        return 0

    outdir.mkdir(parents=True, exist_ok=True)
    written = 0
    for name in sorted(pending):
        (outdir / name).write_text(pending[name], encoding="utf-8")
        inputs[name] = _sha(pending[name])
        written += 1
    for p in stale:
        p.unlink(missing_ok=True)
        inputs.pop(p.name, None)
        print(f"[review] 删除过期页面 {p.name}")

    cache = {"version": CACHE_VERSION, "prompt": PROMPT_VERSION,
             "inputs": inputs, "overview_sha": cache.get("overview_sha", "")}

    # 4) 课程总览（可关；失败只告警，绝不影响页面）
    if overview:
        written += _build_overview(cfg, course, lessons, outdir, cache, force=force)
    _save_cache(cache_path, cache)
    return written


def _build_overview(cfg: Config, course: str, lessons: list[Lesson], outdir: Path,
                    cache: dict, *, force: bool = False) -> int:
    model = str(getattr(cfg.digest, "model", "") or cfg.asr.model)
    # 材料只算一次：既做缓存键，也进提示词（否则截断告警会打印两遍）
    material = _compact_for_overview(lessons)
    need = _sha(PROMPT_VERSION + model + material)
    target = outdir / f"{OVERVIEW_NAME}.md"
    if not force and cache.get("overview_sha") == need and target.is_file():
        return 0

    system, user = _overview_prompt(lessons, material)
    try:
        body = gateway.chat(cfg, modality="digest",
                            messages=[{"role": "system", "content": system},
                                      {"role": "user", "content": user}],
                            max_tokens=int(getattr(cfg.review, "max_tokens", 6000) or 6000),
                            label=OVERVIEW_NAME)
    except gateway.GatewayError as exc:
        print(f"[review] {OVERVIEW_NAME}生成失败：{exc}（保留上次结果）")
        return 0
    target.write_text(_overview_text(course, lessons, body), encoding="utf-8")
    cache["overview_sha"] = need
    return 1


def build_all(cfg: Config, *, force: bool = False, overview: bool = True,
              dry_run: bool = False) -> int:
    """对 runs_root 下每门课逐个 build，返回写入的文件总数。"""
    total = 0
    for course in catalog.list_courses(cfg):
        total += build(cfg, course, force=force, overview=overview, dry_run=dry_run)
    return total
