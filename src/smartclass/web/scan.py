"""只读数据层：扫描 runs_root，为 Web 前端提供浏览与搜索数据。

绝不写任何文件；一切产物存在性判断都复用 workspace.LectureDir 的既有后缀规则。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import Config, runs_root
from ..workspace import LectureDir, safe_name

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def list_courses(cfg: Config) -> list[str]:
    """runs_root 下非 `_` 前缀的课程目录（跳过 _state/_logs/_profile 等）。"""
    root = runs_root(cfg)
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir(), key=lambda x: x.name):
        if p.is_dir() and not p.name.startswith("_") and not p.name.startswith("."):
            out.append(p.name)
    return out


def list_dates(cfg: Config, course: str) -> list[Path]:
    """data/<课程>/ 下的日期目录，倒序（最新在前）。"""
    course_dir = runs_root(cfg) / safe_name(course, "课程")
    if not course_dir.is_dir():
        return []
    out = [p for p in course_dir.iterdir() if p.is_dir() and _DATE_RE.match(p.name)]
    return sorted(out, key=lambda p: p.name, reverse=True)


def lectures_in(cfg: Config, course: str, date: str) -> list[dict]:
    """某日所有整课：先读 lecture.json（主整课），再 glob lecture_<*.json>。

    返回元数据 + 各产物存在性（复用 LectureDir 的 uid 后缀规则）。
    """
    course_dir = runs_root(cfg) / safe_name(course, "课程") / date
    if not course_dir.is_dir():
        return []

    metas: list[tuple[str, Path]] = []  # (uid, meta 文件)
    plain = course_dir / "lecture.json"
    if plain.is_file():
        try:
            uid = str(json.loads(plain.read_text(encoding="utf-8")).get("uid") or "")
        except Exception:
            uid = ""
        metas.append((uid, plain))
    for p in sorted(course_dir.glob("lecture_*.json")):
        m = re.match(r"^lecture_(.+)\.json$", p.name)
        if m:
            metas.append((m.group(1), p))

    out = []
    for uid, meta_path in metas:
        ld = LectureDir(course_dir, uid=uid)
        meta = ld.load_meta()
        if not meta:
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        out.append(_lecture_entry(course, ld, meta))
    return out


def _lecture_entry(course: str, ld: LectureDir, meta: dict) -> dict:
    uid = meta.get("uid") or ld.uid or ""
    parts = meta.get("parts") or []
    frames = videos = False
    for slot in _slots_of(ld, parts):
        run = ld.part(slot)
        if not frames and run.frames.is_dir() and any(run.frames.glob("f_*.jpg")):
            frames = True
        if not videos and run.videos.is_dir() and any(run.videos.glob("*.mp4")):
            videos = True
    return {
        "uid": uid,
        "course": meta.get("course") or course,
        "date": meta.get("date") or (uid.rsplit("_", 1)[0] if uid else ""),
        "span": meta.get("span") or (uid.rsplit("_", 1)[-1] if uid else ""),
        "start": meta.get("start") or "",
        "end": meta.get("end") or "",
        "parts": parts or [],
        "url": (f"/lecture/{_q(course)}/{_q(ld.root.name)}/{_q(uid)}"
                if uid else f"/lecture/{_q(course)}/{_q(ld.root.name)}"),
        "has": {
            "transcript": ld.transcript_path.is_file(),
            "slides": ld.slides_path.is_file(),
            "homework": ld.homework_path.is_file(),
            "notes": ld.notes_path.is_file(),
            "frames": frames,
            "videos": videos,
        },
    }


def _slots_of(ld: LectureDir, parts) -> list[str]:
    """meta 里声明的 slot（且目录真实存在）。

    兜底扫描 parts/ 仅在 meta 完全没有 parts 时进行（老数据）——
    meta 有 slot 但目录缺失（未下载完）就返回空，绝不借用同日
    另一节整课的小节/帧。
    """
    if parts:
        slots = []
        for p in parts:
            slot = str((p or {}).get("slot") or "").strip()
            if slot and (ld.parts_dir / safe_name(slot, "小节")).is_dir():
                slots.append(slot)
        return slots
    if ld.parts_dir.is_dir():
        return [p.name for p in sorted(ld.parts_dir.iterdir()) if p.is_dir()]
    return []


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
    slots = _slots_of(ld, ld.load_meta().get("parts"))
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
