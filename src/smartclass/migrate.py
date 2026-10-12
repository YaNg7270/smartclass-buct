"""把第一阶段的小节目录原地合并成整课包（不重新下载）。

旧：data/<课程>/<日期>/<时段>/
新：data/<课程>/<日期>/<首段>-<末段>/parts/<时段>/
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from . import digest as digest_mod
from .config import Config, runs_root
from .discover import Session
from .grouping import group_sessions, Lecture
from .merge import PartRef, merge_slides, merge_transcripts
from .workspace import LectureDir

SKIP_DIRS = {"_state", "_logs", "_profile", "_手工初版", "parts"}


def _log(msg: str) -> None:
    print(f"[regroup] {msg}", file=sys.stderr, flush=True)


def _collect_old_sessions(date_dir: Path) -> list[tuple[Session, Path]]:
    """找出还是旧布局的小节目录（有 meta.json、且不在 parts/ 下）。"""
    out = []
    for d in sorted(p for p in date_dir.iterdir() if p.is_dir()):
        if d.name in SKIP_DIRS or (d / "parts").is_dir():
            continue
        meta_p = d / "meta.json"
        if not meta_p.is_file():
            continue
        try:
            m = json.loads(meta_p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not m.get("new_id"):
            continue
        s = Session(new_id=m["new_id"], text=m.get("text", ""), date=m.get("date", date_dir.name),
                    start=m.get("start", ""), end=m.get("end", ""), course=m.get("course", ""))
        out.append((s, d))
    return out


def _part_refs(lec: Lecture, lec_dir: LectureDir) -> list[PartRef]:
    base = lec.start_min
    refs = []
    for p in lec.sorted_parts():
        mins = int(p.start.split(":")[0]) * 60 + int(p.start.split(":")[1]) if p.start else 0
        refs.append(PartRef(label=f"{p.start[:5]}-{p.end[:5]}",
                            run=lec_dir.part(p.slot),
                            base_seconds=max(0, (mins - base)) * 60))
    return refs


def regroup(cfg: Config, root: str | None = None, with_digest: bool = True) -> int:
    base = runs_root(cfg, root)
    moved = 0
    for course_dir in sorted(p for p in base.iterdir() if p.is_dir() and p.name not in SKIP_DIRS):
        for date_dir in sorted(p for p in course_dir.iterdir() if p.is_dir()):
            olds = _collect_old_sessions(date_dir)
            if not olds:
                continue
            sessions = [s for s, _ in olds]
            by_id = {s.new_id: d for s, d in olds}
            for lec in group_sessions(sessions, cfg.grouping.max_gap_minutes):
                lec_dir = LectureDir(date_dir / lec.span).ensure()
                for p in lec.sorted_parts():
                    src = by_id.get(p.new_id)
                    if not src or not src.is_dir():
                        continue
                    dst = lec_dir.parts_dir / p.slot
                    if dst.exists():
                        shutil.rmtree(dst)
                    shutil.move(str(src), str(dst))
                    moved += 1

                lec_dir.save_meta({
                    "course": lec.course, "date": lec.date,
                    "start": lec.start + ":00", "end": lec.end + ":00",
                    "span": lec.span, "regrouped": True,
                    "parts": [{"new_id": p.new_id, "start": p.start, "end": p.end,
                               "slot": p.slot} for p in lec.sorted_parts()],
                })
                refs = _part_refs(lec, lec_dir)
                title = f"{lec.course} {lec.date} {lec.start}-{lec.end}"
                merge_transcripts(refs, lec_dir.transcript_path, title)
                merge_slides(refs, lec_dir.slides_path, title)
                if with_digest:
                    transcript = lec_dir.transcript_path.read_text(encoding="utf-8")
                    slides = (lec_dir.slides_path.read_text(encoding="utf-8")
                              if lec_dir.slides_path.is_file() else "")
                    digest_mod.build_homework(cfg, lec_dir, transcript, slides)
                    digest_mod.build_notes(cfg, lec_dir, transcript, slides)
                _log(f"合并 → {lec_dir.root.relative_to(base)}（{len(lec.parts)} 小节）")

            # 旧目录搬空后清理
            for _, d in olds:
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
    return moved
