"""scls 命令行入口。"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from . import asr as asr_mod
from . import audio as audio_mod
from . import digest as digest_mod
from . import download as dl
from . import migrate as migrate_mod
from . import schedule as schedule_mod
from . import streams as streams_mod
from . import videoops as videoops_mod
from . import vision as vision_mod
from .browser import browser_session, cookie_header, ensure_logged_in
from .config import Config, load_config
from .discover import discover, filter_sessions
from .grouping import Lecture, group_sessions
from .merge import PartRef, merge_slides, merge_transcripts
from .state import Busy, clear_login_needed, mark_login_needed, solo_lock, tee_log
from .workspace import LectureDir, RunDir, lecture_dir_for

TRACK_CHOICES = ["vga", "video1"]
UNIT_CHOICES = ["lecture", "session", "both"]


# --------------------------------------------------------------------------- #
# 公共小工具
# --------------------------------------------------------------------------- #
def _today() -> str:
    return dt.date.today().isoformat()


def _resolve_range(args) -> tuple[str, str]:
    d = getattr(args, "date", "") or ""
    f = getattr(args, "date_from", "") or ""
    t = getattr(args, "date_to", "") or ""
    if d:
        return d, d
    if f or t:
        return (f or t), (t or f)
    return _today(), _today()


def _weekdays(args) -> set[int] | None:
    from .discover import WEEKDAYS
    raw = getattr(args, "weekday", "") or ""
    if not raw:
        return None
    days = set()
    for tok in raw.replace("，", ",").split(","):
        tok = tok.strip().lower()
        if tok in WEEKDAYS:
            days.add(WEEKDAYS[tok])
    return days or None


def _gap(args, cfg: Config) -> int:
    return getattr(args, "gap", 0) or cfg.grouping.max_gap_minutes


def _tracks(args) -> list[str]:
    return [t.strip() for t in (args.tracks or "vga").split(",") if t.strip()]


def _course_name(args, lec: Lecture) -> str:
    return args.course or _lecture_course(lec)


def _lecture_course(lec: Lecture) -> str:
    from .grouping import course_key
    return lec.course or course_key(lec.parts[0].text) or "课程"


def _print_lectures(lectures: list[Lecture]) -> None:
    for i, L in enumerate(lectures, 1):
        print(f"  {i}. 【{L.label}】 {_lecture_course(L)}   {L.date}")
        for p in L.sorted_parts():
            print(f"       · [{p.start[:5]}-{p.end[:5]}] {p.text}")
            print(f"         NewID={p.new_id}")


# --------------------------------------------------------------------------- #
# login / list
# --------------------------------------------------------------------------- #
def cmd_login(args) -> int:
    cfg = load_config(args.config)
    with browser_session(cfg, headed=True) as (_, page):
        ensure_logged_in(page, cfg, headed=True, interactive=True)
        clear_login_needed(cfg)
        print("登录态已保存到 profile，后续命令无需再登。")
    return 0


def cmd_list(args) -> int:
    cfg = load_config(args.config)
    date_from, date_to = _resolve_range(args)
    with browser_session(cfg, headed=args.headed) as (_, page):
        ensure_logged_in(page, cfg, headed=args.headed, interactive=args.headed)
        sessions = discover(cfg, page, course=args.course,
                            date_from=date_from, date_to=date_to)
    sessions = filter_sessions(sessions, time_from=args.time_from, time_to=args.time_to,
                               weekdays=_weekdays(args))
    lectures = group_sessions(sessions, _gap(args, cfg))
    if not lectures:
        print(f"{date_from}~{date_to} 没找到课时（课程过滤：{args.course or '无'}）")
        return 1
    print(f"\n{date_from}~{date_to} 共 {len(sessions)} 个小节 → {len(lectures)} 节整课：\n")
    _print_lectures(lectures)
    return 0


# --------------------------------------------------------------------------- #
# run / daily 共用的处理流水线
# --------------------------------------------------------------------------- #
def _fetch_lectures(cfg: Config, args, date_from: str, date_to: str) -> dict:
    """阶段一：占用浏览器，发现课时 → 分整课 → 取流 → 下载。"""
    tracks = _tracks(args)
    jobs: list[tuple[Lecture, LectureDir, dict]] = []

    with browser_session(cfg, headed=args.headed) as (ctx, page):
        ensure_logged_in(page, cfg, headed=args.headed, interactive=args.headed)
        sessions = discover(cfg, page, course=args.course,
                            date_from=date_from, date_to=date_to)
        sessions = filter_sessions(sessions, time_from=args.time_from, time_to=args.time_to,
                                   weekdays=_weekdays(args))
        if not sessions:
            print(f"{date_from}~{date_to} 没有课时。")
            return {"jobs": [], "images": None}

        lectures = group_sessions(sessions, _gap(args, cfg))
        print(f"\n{date_from}~{date_to} 命中 {len(sessions)} 个小节 → {len(lectures)} 节整课\n")

        for lec in lectures:
            course = _course_name(args, lec)
            print(f"\n  【{lec.label}】 {course}   {lec.date}")
            lec_dir = lecture_dir_for(cfg, course, lec.date, lec.span, args.out).ensure()
            videos: dict[str, Path] = {}

            for p in lec.sorted_parts():
                cookie = cookie_header(ctx)
                info = streams_mod.fetch_streams(cfg, page, p.new_id, cookie=cookie)
                part = lec_dir.part(p.slot).ensure()
                part.save_meta({
                    "new_id": p.new_id, "course": course, "date": p.date,
                    "start": p.start, "end": p.end, "text": p.text,
                    "title": info.title, "schedule_id": info.schedule_id,
                    "track_source": info.source,
                    "tracks": {t.kind: t.url for t in info.tracks},
                })
                for kind in tracks:
                    tr = info.by_kind(kind)
                    if not tr:
                        print(f"  [{p.slot}] 无 {kind} 机位，跳过")
                        continue
                    if getattr(args, "dry_run", False):
                        size = dl.remote_size(tr.url, cookie)
                        print(f"  [{p.slot}] {kind:7s} {dl.human(size) if size else '?':>9s}  {tr.url}")
                        continue
                    dest = part.videos / f"{course}_{p.date}_{p.slot}_{kind}.mp4"
                    # 已存在就不再下：文件可能已被压缩过（尺寸和远端不一致，不能按尺寸判断）
                    if dest.is_file() and not args.force:
                        print(f"  [{p.slot}] {kind} 已存在，跳过下载（{dl.human(dest.stat().st_size)}）")
                        videos[p.slot] = dest
                        continue
                    dl.download(tr.url, dest, cookie=cookie, force=args.force)
                    videos[p.slot] = dest

            lec_dir.save_meta({
                "course": course, "date": lec.date,
                "start": lec.start + ":00", "end": lec.end + ":00", "span": lec.span,
                "parts": [{"new_id": p.new_id, "start": p.start, "end": p.end,
                           "slot": p.slot, "text": p.text} for p in lec.sorted_parts()],
            })
            jobs.append((lec, lec_dir, videos))

    return {"jobs": jobs}


def _part_refs(lec: Lecture, lec_dir: LectureDir) -> list[PartRef]:
    base = lec.start_min
    refs = []
    for p in lec.sorted_parts():
        mins = int(p.start.split(":")[0]) * 60 + int(p.start.split(":")[1]) if p.start else 0
        refs.append(PartRef(label=f"{p.start[:5]}-{p.end[:5]}",
                            run=lec_dir.part(p.slot),
                            base_seconds=max(0, mins - base) * 60))
    return refs


def _process_lecture(cfg: Config, lec: Lecture, lec_dir: LectureDir,
                     videos: dict[str, Path], args) -> None:
    """阶段二：小节级抽取 → 合并 → 整课级归纳 → 视频瘦身。"""
    keep = getattr(args, "keep_video", None) or cfg.retention.keep_video

    for p in lec.sorted_parts():
        video = videos.get(p.slot)
        if not video or not video.is_file():
            continue
        part = lec_dir.part(p.slot)
        mp3 = audio_mod.extract_audio(cfg, video, part.audio / (video.stem + ".mp3"),
                                      cfg.asr.bitrate)
        asr_mod.transcribe(cfg, part, mp3, provider=getattr(args, "asr", None), force=args.force)
        if not getattr(args, "no_slides", False):
            vision_mod.scan(cfg, part, video, mode="assignment", force=args.force)

    refs = _part_refs(lec, lec_dir)
    title = f"{_lecture_course(lec)} {lec.date} {lec.start}-{lec.end}"
    merge_transcripts(refs, lec_dir.transcript_path, title)
    merge_slides(refs, lec_dir.slides_path, title)
    transcript = lec_dir.transcript_path.read_text(encoding="utf-8")
    slides = (lec_dir.slides_path.read_text(encoding="utf-8")
              if lec_dir.slides_path.is_file() else "")

    unit = getattr(args, "unit", "lecture")
    if not getattr(args, "no_digest", False):
        if unit in ("lecture", "both"):
            digest_mod.build_homework(cfg, lec_dir, transcript, slides, force=args.force)
            digest_mod.build_notes(cfg, lec_dir, transcript, slides, force=args.force)
        if unit in ("session", "both"):
            for ref in refs:
                if ref.run.full_transcript.is_file():
                    t = ref.run.full_transcript.read_text(encoding="utf-8")
                    s = (ref.run.slides_path.read_text(encoding="utf-8")
                         if ref.run.slides_path.is_file() else "")
                    digest_mod.build_homework(cfg, ref.run, t, s, force=args.force)
                    digest_mod.build_notes(cfg, ref.run, t, s, force=args.force)

    # 视频瘦身放在最后：此时音频/关键帧都已就绪
    if keep != videoops_mod.KEEP_ORIGINAL:
        for p in lec.sorted_parts():
            video = videos.get(p.slot)
            if not video or not video.is_file():
                continue
            part = lec_dir.part(p.slot)
            pmeta = part.load_meta()
            if pmeta.get("retained") and not args.force:
                print(f"  [{p.slot}] 已处理过（{pmeta['retained'].get('keep')}），跳过")
                continue
            have_backup = any(part.frames.glob("*.jpg")) and any(part.audio.glob("*.mp3"))
            _, why = videoops_mod.apply_retention(cfg, video, keep,
                                                  have_backup=have_backup, force=args.force)
            print(f"  [{p.slot}] {why}")
            pmeta["retained"] = {"keep": keep, "note": why,
                                 "size": video.stat().st_size if video.is_file() else 0}
            part.save_meta(pmeta)


def _run_pipeline(cfg: Config, args, date_from: str, date_to: str) -> int:
    result = _fetch_lectures(cfg, args, date_from, date_to)
    jobs = result["jobs"]
    if getattr(args, "dry_run", False):
        print("\n（--dry-run：未下载、未处理）")
        return 0
    for lec, lec_dir, videos in jobs:
        print(f"\n===== 处理整课 {lec_dir.root} =====")
        _process_lecture(cfg, lec, lec_dir, videos, args)
    print("\n全部完成。")
    return 0


# --------------------------------------------------------------------------- #
# run / daily
# --------------------------------------------------------------------------- #
def cmd_run(args) -> int:
    cfg = load_config(args.config)
    date_from, date_to = _resolve_range(args)
    with solo_lock(cfg):
        return _run_pipeline(cfg, args, date_from, date_to)


def cmd_daily(args) -> int:
    """无人值守：处理当天监控清单里所有课的整课。"""
    cfg = load_config(args.config)
    date = args.date or _today()

    with solo_lock(cfg):
        with tee_log(cfg, "daily") as logpath:
            print(f"[daily] {date} 开始，日志：{logpath}")
            courses = [args.course] if args.course else (cfg.watch.courses or [""])
            if not cfg.watch.courses and not args.course:
                print("[daily] 未配置 watch.courses，将处理当天全部课程")
            rc = 0
            try:
                for c in courses:
                    print(f"\n########## 课程：{c or '（全部）'} ##########")
                    a = argparse.Namespace(**vars(args))
                    a.course = c
                    a.headed = False
                    _run_pipeline(cfg, a, date, date)
            except (RuntimeError, TimeoutError) as exc:
                if "登录" in str(exc):
                    print(f"\n[daily] 登录已失效：{exc}")
                    mark_login_needed(cfg, str(exc))
                    return 2
                raise
            clear_login_needed(cfg)
            print(f"\n[daily] 完成，返回码 {rc}")
            return rc


# --------------------------------------------------------------------------- #
# regroup / prune / schedule
# --------------------------------------------------------------------------- #
def cmd_regroup(args) -> int:
    cfg = load_config(args.config)
    n = migrate_mod.regroup(cfg, args.out, with_digest=not args.no_digest)
    print(f"已合并 {n} 个小节目录（已存在的整课包会被跳过）。")
    return 0


def cmd_prune(args) -> int:
    cfg = load_config(args.config)
    from .config import runs_root
    base = runs_root(cfg, args.out)
    keep = args.keep_video or cfg.retention.keep_video
    policy = cfg.retention
    old_keep = policy.keep_video
    policy.keep_video = keep
    n = 0
    try:
        for video in sorted(base.rglob("*.mp4")):
            if ".compressing." in video.name or "_profile" in video.parts:
                continue
            part = RunDir(video.parent.parent)
            part.ensure()
            pmeta = part.load_meta()
            if pmeta.get("retained") and not args.force:
                continue
            have_backup = any(part.frames.glob("*.jpg")) and any(part.audio.glob("*.mp3"))
            _, why = videoops_mod.apply_retention(cfg, video, keep,
                                                  have_backup=have_backup, force=args.force)
            print(f"  {video.relative_to(base)}: {why}")
            pmeta["retained"] = {"keep": keep, "note": why,
                                 "size": video.stat().st_size if video.is_file() else 0}
            part.save_meta(pmeta)
            n += 1
    finally:
        policy.keep_video = old_keep
    print(f"处理了 {n} 个视频。")
    return 0


def cmd_schedule(args) -> int:
    cfg = load_config(args.config)
    if args.action == "install":
        schedule_mod.install(cfg, args.time, args.task_name)
        print(schedule_mod.show(cfg, args.task_name))
    elif args.action == "remove":
        schedule_mod.remove(cfg, args.task_name)
    else:
        print(schedule_mod.show(cfg, args.task_name))
    return 0


# --------------------------------------------------------------------------- #
# 分阶段重跑（对已有目录）
# --------------------------------------------------------------------------- #
def _resolve_dir(args) -> Path:
    p = Path(args.run).resolve()
    if not p.is_dir():
        raise SystemExit(f"目录不存在：{p}")
    return p


def _dirs_with_videos(root: Path) -> list[RunDir]:
    """找到所有含视频的小节目录（兼容新旧两种布局）。"""
    out = []
    for v in root.rglob("videos"):
        if not v.is_dir():
            continue
        d = RunDir(v.parent)
        if d.meta_path.is_file() or any(v.glob("*.mp4")):
            d.ensure()
            out.append(d)
    return out


def cmd_asr(args) -> int:
    cfg = load_config(args.config)
    root = _resolve_dir(args)
    for d in _dirs_with_videos(root):
        for video in sorted(d.videos.glob("*.mp4")):
            mp3 = audio_mod.extract_audio(cfg, video, d.audio / (video.stem + ".mp3"),
                                          cfg.asr.bitrate)
            asr_mod.transcribe(cfg, d, mp3, provider=args.asr, force=args.force)
    return 0


def cmd_slides(args) -> int:
    cfg = load_config(args.config)
    root = _resolve_dir(args)
    for d in _dirs_with_videos(root):
        for video in sorted(d.videos.glob("*.mp4")):
            vision_mod.scan(cfg, d, video, mode=args.mode, force=args.force)
    return 0


def cmd_digest(args) -> int:
    cfg = load_config(args.config)
    root = _resolve_dir(args)
    if (root / "lecture.json").is_file():
        lec_dir = LectureDir(root)
        transcript = (lec_dir.transcript_path.read_text(encoding="utf-8")
                      if lec_dir.transcript_path.is_file() else "")
        slides = (lec_dir.slides_path.read_text(encoding="utf-8")
                  if lec_dir.slides_path.is_file() else "")
        if not transcript and not slides:
            raise SystemExit("既没有整课转写也没有课件文字，先跑 `scls asr` / `scls slides`")
        if not args.no_homework:
            digest_mod.build_homework(cfg, lec_dir, transcript, slides, force=args.force)
        if not args.no_notes:
            digest_mod.build_notes(cfg, lec_dir, transcript, slides, force=args.force)
        return 0
    for d in _dirs_with_videos(root):
        transcript = d.full_transcript.read_text(encoding="utf-8") if d.full_transcript.is_file() else ""
        slides = d.slides_path.read_text(encoding="utf-8") if d.slides_path.is_file() else ""
        if not transcript and not slides:
            continue
        if not args.no_homework:
            digest_mod.build_homework(cfg, d, transcript, slides, force=args.force)
        if not args.no_notes:
            digest_mod.build_notes(cfg, d, transcript, slides, force=args.force)
    return 0


# --------------------------------------------------------------------------- #
def _add_filters(sp) -> None:
    sp.add_argument("--date", help="单日 YYYY-MM-DD（默认今天）")
    sp.add_argument("--from", dest="date_from", metavar="DATE", help="起始日期 YYYY-MM-DD")
    sp.add_argument("--to", dest="date_to", metavar="DATE", help="结束日期 YYYY-MM-DD")
    sp.add_argument("--time-from", dest="time_from", default="", metavar="HH:MM",
                    help="只要开始时间不早于此的课")
    sp.add_argument("--time-to", dest="time_to", default="", metavar="HH:MM",
                    help="只要开始时间不晚于此的课")
    sp.add_argument("--weekday", default="", metavar="mon,wed",
                    help="按星期过滤，如 mon,wed,fri")
    sp.add_argument("--course", default="", help="课程名（模糊匹配）")
    sp.add_argument("--gap", type=int, default=0, metavar="MIN",
                    help="相邻小节间隔 ≤ 此值即并成整课（默认取配置 30）")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scls", description="北化 smartclass 录播课处理工具")
    p.add_argument("--config", help="配置文件路径（默认 ./smartclass.toml 或仓库根 smartclass.toml）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("login", help="有头打开浏览器，登录后保存 profile")
    sp.set_defaults(func=cmd_login)

    sp = sub.add_parser("list", help="列出课时（按整课分组）")
    _add_filters(sp)
    sp.add_argument("--headed", action="store_true", help="有头运行（登录失效时可现场登录）")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("run", help="抓取+处理（默认按整课打包）")
    _add_filters(sp)
    sp.add_argument("--unit", choices=UNIT_CHOICES, default="lecture",
                    help="切片粒度：lecture=整课一份（默认），session=每小节各一份，both=都要")
    sp.add_argument("--tracks", default="vga", help="机位：vga,video1（默认 vga=课件）")
    sp.add_argument("--out", help="输出根目录（默认取配置 runs_root）")
    sp.add_argument("--asr", choices=["gateway", "whisper"], help="转写通道")
    sp.add_argument("--keep-video", choices=["none", "compressed", "original"],
                    help="视频保留策略（默认取配置）")
    sp.add_argument("--headed", action="store_true", help="有头运行")
    sp.add_argument("--force", action="store_true", help="忽略缓存，全部重跑")
    sp.add_argument("--dry-run", action="store_true",
                    help="只发现课时 + 取流并打印机位地址，不下载、不处理")
    sp.add_argument("--no-slides", action="store_true", help="跳过课件 OCR")
    sp.add_argument("--no-digest", action="store_true", help="跳过作业汇总/笔记")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("daily", help="无人值守：处理当天监控清单里所有课的整课")
    _add_filters(sp)
    sp.add_argument("--unit", choices=UNIT_CHOICES, default="lecture")
    sp.add_argument("--tracks", default="vga")
    sp.add_argument("--out")
    sp.add_argument("--asr", choices=["gateway", "whisper"])
    sp.add_argument("--keep-video", choices=["none", "compressed", "original"])
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--no-slides", action="store_true")
    sp.add_argument("--no-digest", action="store_true")
    sp.add_argument("--headed", action="store_true")
    sp.set_defaults(func=cmd_daily, course="", time_from="", time_to="",
                    weekday="", gap=0, date_from="", date_to="")

    sp = sub.add_parser("regroup", help="把旧的小节目录原地合并成整课包（不重新下载）")
    sp.add_argument("--out", help="数据根目录（默认取配置 runs_root）")
    sp.add_argument("--no-digest", action="store_true", help="只合并，不重新生成作业要求/笔记")
    sp.set_defaults(func=cmd_regroup)

    sp = sub.add_parser("prune", help="对已有数据补做视频压缩/删除")
    sp.add_argument("--out", help="数据根目录")
    sp.add_argument("--keep-video", choices=["none", "compressed", "original"])
    sp.add_argument("--force", action="store_true", help="已处理过的也重做")
    sp.set_defaults(func=cmd_prune)

    sp = sub.add_parser("schedule", help="Windows 计划任务：每天晚上自动跑 daily")
    sp.add_argument("action", choices=["install", "remove", "show"])
    sp.add_argument("--time", help="每天执行时间 HH:MM（默认取配置）")
    sp.add_argument("--task-name", dest="task_name", help="计划任务名（默认 smartclass-buct-daily）")
    sp.set_defaults(func=cmd_schedule)

    for name, fn, helptext in (
        ("asr", cmd_asr, "对已有目录重跑转写"),
        ("slides", cmd_slides, "对已有目录重跑课件 OCR"),
        ("digest", cmd_digest, "对已有目录重跑归纳"),
    ):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--run", required=True,
                        help="目录：整课包 或 数据根目录（会递归找小节）")
        sp.add_argument("--force", action="store_true", help="忽略缓存")
        if name == "asr":
            sp.add_argument("--asr", choices=["gateway", "whisper"], help="转写通道")
        if name == "slides":
            sp.add_argument("--mode", choices=["assignment", "full"], default="assignment")
        if name == "digest":
            sp.add_argument("--no-homework", action="store_true")
            sp.add_argument("--no-notes", action="store_true")
        sp.set_defaults(func=fn)

    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg_path = None
    if "--config" in argv:
        i = argv.index("--config")
        if i + 1 < len(argv):
            cfg_path = argv[i + 1]
        del argv[i:i + 2]

    args = build_parser().parse_args(argv)
    if cfg_path:
        args.config = cfg_path

    try:
        return args.func(args)
    except Busy as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"\n错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
