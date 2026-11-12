"""只读数据层：扫描 runs_root，为 Web 前端提供浏览与搜索数据。

绝不写任何文件；课程/日期/整课的扫描逻辑在 `catalog`（与 `scls review` 共用），
这里只补 Web 专属的 URL 与渲染信息。
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import catalog
from ..catalog import slots_of
from ..config import Config, runs_root
from ..workspace import LectureDir

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 纯粹的目录扫描，直接复用 catalog 的实现（保持 scan.* 的公开名称不变）
list_courses = catalog.list_courses
list_dates = catalog.list_dates


def lectures_in(cfg: Config, course: str, date: str) -> list[dict]:
    """某日所有整课（catalog 提供元数据，这里补整课页 URL）。"""
    return [dict(x, url=_lecture_url_for(course, date, x["uid"]))
            for x in catalog.lectures_in(cfg, course, date)]


def _lecture_url_for(course: str, date: str, uid: str) -> str:
    """整课页链接；uid 为空（旧布局）时退回只带日期的形式。"""
    if uid:
        return f"/lecture/{_q(course)}/{_q(date)}/{_q(uid)}"
    return f"/lecture/{_q(course)}/{_q(date)}"


def course_summary(cfg: Config) -> list[dict]:
    """仪表盘用：课程名 + 日期数 + 整课数。"""
    out = []
    for course in list_courses(cfg):
        dates = list_dates(cfg, course)
        n = sum(len(lectures_in(cfg, course, d.name)) for d in dates)
        out.append({"name": course, "dates": len(dates), "lectures": n,
                    "url": f"/course/{_q(course)}"})
    return out


def _q(s: str) -> str:
    from urllib.parse import quote
    return quote(str(s), safe="")


def list_parts(lecture_dir: Path, uid: str = "") -> list[dict]:
    """parts/<slot>/ 下的 transcript/full.txt、slides.txt、frames、videos 存在性。"""
    ld = LectureDir(Path(lecture_dir), uid=uid)
    if not ld.parts_dir.is_dir():
        return []
    slots = slots_of(ld, ld.load_meta().get("parts"))
    seen = set()
    out = []
    for slot in slots:
        if slot in seen:
            continue
        seen.add(slot)
        run = ld.part(slot)
        frames = sorted(run.frames.glob("f_*.jpg")) if run.frames.is_dir() else []
        vids = sorted(run.videos.glob("*.mp4")) if run.videos.is_dir() else []
        out.append({
            "slot": slot,
            "dir": run.root,
            "transcript": run.full_transcript.is_file(),
            "slides": run.slides_path.is_file(),
            "frames": frames,
            "frame_count": len(frames),
            "videos": vids,
        })
    return out


_SKIP_PARTS = ("frames", "videos", "audio")


def search_text(cfg: Config, query: str, limit: int = 200) -> list[dict]:
    """全文搜索：遍历 runs_root 下 *.txt / *.md（排除 `_` 目录与帧/音视频目录）。

    逐行子串匹配（忽略大小写），返回 {file, url, line_no, line}。
    """
    query = (query or "").strip()
    if not query:
        return []
    needle = query.casefold()
    root = runs_root(cfg)
    if not root.is_dir():
        return []

    results: list[dict] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (".txt", ".md"):
            continue
        rel = p.relative_to(root)
        if any(part.startswith("_") for part in rel.parts[:-1]):
            continue
        if any(part in _SKIP_PARTS for part in rel.parts[:-1]):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if needle in line.casefold():
                results.append({
                    "file": rel.as_posix(),
                    "url": _lecture_url(root, rel),
                    "line_no": i,
                    "line": line.strip(),
                })
                if len(results) >= limit:
                    return results
    return results


def _lecture_url(root: Path, rel: Path) -> str:
    """文件路径 → 整课页链接；不属于任何整课包则返回 None。"""
    parts = rel.parts
    if len(parts) >= 2 and _DATE_RE.match(parts[1]):
        return f"/lecture/{_q(parts[0])}/{_q(parts[1])}"
    return None
