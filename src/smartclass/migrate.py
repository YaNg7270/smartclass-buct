"""目录迁移：

1. `regroup`：把第一阶段的小节目录合并成整课包（不重新下载）。
   旧：data/<课程>/<日期>/<时段>/
   新：data/<课程>/<日期>/parts/<时段>/（扁平）
2. `flatten`：把第二阶段的 data/<课程>/<日期>/<首段>-<末段>/ 扁平化到
   data/<课程>/<日期>/，homework/notes 改名带唯一后缀。
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

SKIP_DIRS = {"_state", "_logs", "_profile", "_手工初版", "parts", "__pycache__"}


def _log(msg: str) -> None:
    print(f"[migrate] {msg}", file=sys.stderr, flush=True)


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
                uid = f"{lec.date}_{lec.span}"
                lec_dir = LectureDir(date_dir, uid=uid).ensure()
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
                    "span": lec.span, "uid": uid, "layout": "flat",
                    "regrouped": True,
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


# --------------------------------------------------------------------------- #
# 扁平化：<日期>/<时段>/ → <日期>/
# --------------------------------------------------------------------------- #

def _old_lecture_dirs(date_dir: Path) -> list[Path]:
    """找出 <日期>/<时段>/ 这种旧整课目录。"""
    out = []
    for d in sorted(p for p in date_dir.iterdir() if p.is_dir()):
        if d.name in SKIP_DIRS or d.name.startswith("_"):
            continue
        if (d / "lecture.json").is_file():
            out.append(d)
    return out


def _move_file(src: Path, dst: Path, dry_run: bool) -> None:
    if not src.is_file():
        return
    if dst.exists():
        if dst.read_bytes() == src.read_bytes():
            if not dry_run:
                src.unlink()
            return
        raise RuntimeError(f"目标已存在且内容不同：{dst}")
    _log(f"  {src.name} → {dst.name}")
    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))


def _move_parts(src: Path, dst: Path, dry_run: bool) -> None:
    if not src.is_dir():
        return
    if not dry_run:
        dst.mkdir(parents=True, exist_ok=True)
    for sub in sorted(src.iterdir()):
        target = dst / sub.name
        if target.exists():
            raise RuntimeError(f"parts/{sub.name} 已存在，先手工确认")
        _log(f"  parts/{sub.name} → {dst.name}/{sub.name}")
        if not dry_run:
            shutil.move(str(sub), str(target))
    if not dry_run:
        try:
            src.rmdir()
        except OSError:
            pass


def _rename_part_digests(date_dir: Path, date: str, dry_run: bool) -> int:
    """把小节级 homework.md / notes.md 也改成带唯一后缀的名字。"""
    parts = date_dir / "parts"
    if not parts.is_dir():
        return 0
    n = 0
    for part in sorted(p for p in parts.iterdir() if p.is_dir()):
        slot = part.name
        for old, new in ((part / "homework.md", part / f"homework_{date}_{slot}.md"),
                         (part / "notes.md", part / f"notes_{date}_{slot}.md")):
            if not old.is_file():
                continue
            if new.exists():
                _log(f"  [跳过] 目标已存在：{new.relative_to(date_dir)}")
                continue
            _log(f"  {part.name}/{old.name} → {new.name}")
            if not dry_run:
                old.rename(new)
            n += 1
    return n


def flatten(cfg: Config, root: str | None = None,
            dry_run: bool = False) -> tuple[int, int]:
    """把 data/<课程>/<日期>/<时段>/ 的内容上移到 data/<课程>/<日期>/。

    返回 (迁移的整课目录数, 重命名的小节级 homework/notes 数)。
    homework/notes 改成 `homework_<日期>_<时段>.md`；同一天有多节整课时，
    第一节保留无后缀的 transcript/slides/lecture.json，其余自动加后缀。
    幂等：已经扁平化的目录不会再被识别为 `<时段>/`。
    """
    base = runs_root(cfg, root)
    total = 0
    renamed = 0
    for course_dir in sorted(p for p in base.iterdir()
                             if p.is_dir() and p.name not in SKIP_DIRS
                             and not p.name.startswith("_")):
        for date_dir in sorted(p for p in course_dir.iterdir()
                               if p.is_dir() and p.name not in SKIP_DIRS
                               and not p.name.startswith("_")):
            olds = _old_lecture_dirs(date_dir)
            if not olds:
                renamed += _rename_part_digests(date_dir, date_dir.name, dry_run)
                continue
            for src in olds:
                try:
                    meta = json.loads((src / "lecture.json").read_text(encoding="utf-8"))
                except Exception:
                    _log(f"跳过（lecture.json 读不了）：{src}")
                    continue
                span = str(meta.get("span") or src.name)
                date = str(meta.get("date") or date_dir.name)
                uid = f"{date}_{span}"

                plain = date_dir / "lecture.json"
                use_suffixed = False
                if plain.is_file():
                    try:
                        puid = str(json.loads(plain.read_text(encoding="utf-8")).get("uid") or "")
                    except Exception:
                        puid = ""
                    use_suffixed = bool(puid) and puid != uid
                meta_name = f"lecture_{uid}.json" if use_suffixed else "lecture.json"
                t_name = f"transcript_{uid}.txt" if use_suffixed else "transcript.txt"
                s_name = f"slides_{uid}.txt" if use_suffixed else "slides.txt"

                _log(f"{src.relative_to(base)} → {date_dir.relative_to(base)}/{meta_name}")
                try:
                    _move_parts(src / "parts", date_dir / "parts", dry_run)
                    _move_file(src / "transcript.txt", date_dir / t_name, dry_run)
                    _move_file(src / "slides.txt", date_dir / s_name, dry_run)
                    _move_file(src / "homework.md", date_dir / f"homework_{uid}.md", dry_run)
                    _move_file(src / "notes.md", date_dir / f"notes_{uid}.md", dry_run)
                    _move_file(src / "slides_full.txt",
                               date_dir / f"slides_full_{uid}.txt", dry_run)
                except Exception as exc:
                    _log(f"  [失败] {exc}，保留原目录")
                    continue

                meta.update({"uid": uid, "layout": "flat", "span": span,
                             "date": date, "course": meta.get("course") or course_dir.name})
                if not dry_run:
                    (date_dir / meta_name).write_text(
                        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                    try:
                        (src / "lecture.json").unlink()
                    except FileNotFoundError:
                        pass
                    leftover = [p.name for p in src.iterdir()]
                    if leftover:
                        _log(f"  [注意] 原目录还有未识别文件，未删除：{leftover}")
                    else:
                        src.rmdir()
                total += 1
            renamed += _rename_part_digests(date_dir, date_dir.name, dry_run)
    return total, renamed
