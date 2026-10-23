"""scls 命令行入口。"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import asr as asr_mod
from . import audio as audio_mod
from . import bench as bench_mod
from . import digest as digest_mod
from . import download as dl
from . import migrate as migrate_mod
from . import schedule as schedule_mod
from . import scheduler
from . import streams as streams_mod
from . import videoops as videoops_mod
from . import vision as vision_mod
from .browser import browser_session, cookie_header, ensure_logged_in
from .config import Config, load_config
from .discover import discover, filter_sessions
from .grouping import Lecture, group_sessions
from .merge import PartRef, merge_slides, merge_transcripts
from .state import Busy, clear_login_needed, mark_login_needed, solo_lock, tee_log
from .web.server import cmd_serve
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
    if d and (f or t):
        # 曾经这里 --date 会静默盖掉 --to，导致区间被缩成一天、课时数明显偏少
        raise SystemExit(
            f"--date {d} 与 --from/--to 不能同时使用。\n"
            f"  要区间：--from {f or d} --to {t or '结束日期'}\n"
            f"  要单日：只写 --date {d}"
        )
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
    """目录名一律用平台给的课程名，避免用户输入法不同导致生成两个目录。"""
    return lec.course or _lecture_course(lec) or getattr(args, "course", "") or "课程"


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
def _prepare_lecture(cfg: Config, args, ctx, page, lec: Lecture,
                     lec_dir: LectureDir, course: str) -> dict[str, Path]:
    """取流 + 下载一节整课；同一个小节的多个机位并发下载。

    本地已经有视频时**先认本地**：不再去 VOD 取流/HEAD。这样已下载的课可以
    离线重跑，也不会因为 VOD 主机一次 SSL 抖动就把整节判成「没有录播」。
    """
    tracks = _tracks(args)
    videos: dict[str, Path] = {}
    tasks: list[tuple[str, str, str, Path, str]] = []

    for p in lec.sorted_parts():
        part = lec_dir.part(p.slot).ensure()
        have: dict[str, Path] = {}
        if not args.force:
            for kind in tracks:
                dest = part.videos / f"{course}_{p.date}_{p.slot}_{kind}.mp4"
                if dest.is_file():
                    have[kind] = dest
                else:
                    cand = sorted(part.videos.glob(f"*_{kind}.mp4"))
                    if cand:
                        have[kind] = cand[0]

        need = [k for k in tracks if k not in have]
        if not need:
            print(f"  [{p.slot}] 本地已有 {', '.join(have)}，跳过取流/下载")
            for kind, dest in have.items():
                if p.slot not in videos or kind == "vga":
                    videos[p.slot] = dest
            continue

        cookie = cookie_header(ctx)
        info = streams_mod.fetch_streams(cfg, page, p.new_id, cookie=cookie)
        # 合并而不是覆盖：保留上次的 retained / timings，避免重复压缩
        pm = part.load_meta()
        pm.update({
            "new_id": p.new_id, "course": course, "date": p.date,
            "start": p.start, "end": p.end, "text": p.text,
            "title": info.title, "schedule_id": info.schedule_id,
            "track_source": info.source,
            "tracks": {t.kind: t.url for t in info.tracks},
        })
        part.save_meta(pm)
        for kind, dest in have.items():
            if p.slot not in videos or kind == "vga":
                videos[p.slot] = dest
        for kind in need:
            tr = info.by_kind(kind)
            if not tr:
                print(f"  [{p.slot}] 无 {kind} 机位，跳过")
                continue
            if getattr(args, "dry_run", False):
                size = dl.remote_size(tr.url, cookie)
                print(f"  [{p.slot}] {kind:7s} {dl.human(size) if size else '?':>9s}  {tr.url}")
                continue
            dest = part.videos / f"{course}_{p.date}_{p.slot}_{kind}.mp4"
            tasks.append((p.slot, kind, tr.url, dest, cookie))

    def one(task: tuple[str, str, str, Path, str]):
        slot, kind, url, dest, cookie = task
        print(f"  [下载] [{slot}] {kind} 开始")
        dl.download(url, dest, cookie=cookie, force=args.force)
        return slot, kind, dest

    if tasks:
        workers = max(1, int(getattr(cfg.concurrency, "downloads", 1) or 1))
        results = (scheduler.parallel_map(one, tasks, workers=workers, label="下载")
                   if workers > 1 and len(tasks) > 1 else [one(t) for t in tasks])
        for slot, kind, dest in results:
            # 同一小节有机位多个时优先 VGA（课件机位）
            if slot not in videos or kind == "vga":
                videos[slot] = dest
    return videos


def _iter_lectures(cfg: Config, args, date_from: str, date_to: str):
    """阶段一：占着浏览器发现、取流、下载；每准备好一节整课就 yield。

    生成器写法是为了让 `_run_pipeline` 能在下载第 N+1 节的同时处理第 N 节。
    Playwright 全程留在主线程；处理线程只碰文件 / requests / ffmpeg。
    """
    with browser_session(cfg, headed=args.headed) as (ctx, page):
        ensure_logged_in(page, cfg, headed=args.headed, interactive=args.headed)
        sessions = discover(cfg, page, course=args.course,
                            date_from=date_from, date_to=date_to)
        sessions = filter_sessions(sessions, time_from=args.time_from, time_to=args.time_to,
                                   weekdays=_weekdays(args))
        if not sessions:
            print(f"{date_from}~{date_to} 没有课时。")
            return

        lectures = group_sessions(sessions, _gap(args, cfg))
        print(f"\n{date_from}~{date_to} 命中 {len(sessions)} 个小节 → {len(lectures)} 节整课\n")

        for lec in lectures:
            course = _course_name(args, lec)
            print(f"\n  【{lec.label}】 {course}   {lec.date}")
            lec_dir = lecture_dir_for(cfg, course, lec.date, lec.span, args.out).ensure()
            videos = _prepare_lecture(cfg, args, ctx, page, lec, lec_dir, course)
            lm = lec_dir.load_meta()
            lm.update({
                "course": course, "date": lec.date,
                "start": lec.start + ":00", "end": lec.end + ":00", "span": lec.span,
                "parts": [{"new_id": p.new_id, "start": p.start, "end": p.end,
                           "slot": p.slot, "text": p.text} for p in lec.sorted_parts()],
            })
            lec_dir.save_meta(lm)
            yield lec, lec_dir, videos


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
    """阶段二：小节级抽取（ASR 与 OCR 并行）→ 合并 → 整课级归纳 → 后台压缩。"""
    keep = getattr(args, "keep_video", None) or cfg.retention.keep_video
    stats = {"audio": 0.0, "asr": 0.0, "ocr": 0.0, "compress": 0.0}
    started = time.perf_counter()

    compress_pool = None
    compress_futs = []
    if keep != videoops_mod.KEEP_ORIGINAL:
        workers = max(1, int(getattr(cfg.concurrency, "compress", 1) or 1))
        compress_pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="compress")

    try:
        for p in lec.sorted_parts():
            video = videos.get(p.slot)
            if not video or not video.is_file():
                continue
            part = lec_dir.part(p.slot)

            t0 = time.perf_counter()
            mp3 = audio_mod.extract_audio(cfg, video, part.audio / (video.stem + ".mp3"),
                                          cfg.asr.bitrate)
            stats["audio"] += time.perf_counter() - t0

            def do_asr(_part=part, _mp3=mp3):
                t = time.perf_counter()
                asr_mod.transcribe(cfg, _part, _mp3,
                                   provider=getattr(args, "asr", None), force=args.force)
                return time.perf_counter() - t

            def do_ocr(_part=part, _video=video):
                if getattr(args, "no_slides", False):
                    return 0.0
                t = time.perf_counter()
                vision_mod.scan(cfg, _part, _video, mode="assignment", force=args.force)
                return time.perf_counter() - t

            # ASR 与「抽帧 + OCR」互不依赖：并行后，抽帧的几十秒解码藏在 ASR 等待里
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="part") as pex:
                fa = pex.submit(do_asr)
                fo = pex.submit(do_ocr)
                stats["asr"] += fa.result()
                stats["ocr"] += fo.result()

            # 本小节的音频/关键帧已就绪，立刻把压缩丢到后台，和下一小节重叠
            if compress_pool is not None:
                pmeta = part.load_meta()
                if pmeta.get("retained") and not args.force:
                    print(f"  [{p.slot}] 已处理过（{pmeta['retained'].get('keep')}），跳过压缩")
                else:
                    have_backup = (any(part.frames.glob("*.jpg"))
                                   and any(part.audio.glob("*.mp3")))

                    def do_compress(_slot=p.slot, _video=video, _part=part,
                                    _have_backup=have_backup):
                        t = time.perf_counter()
                        _, why = videoops_mod.apply_retention(
                            cfg, _video, keep, have_backup=_have_backup, force=args.force)
                        spent = time.perf_counter() - t
                        print(f"  [{_slot}] {why}（{spent:.1f}s）")
                        pm = _part.load_meta()
                        pm["retained"] = {
                            "keep": keep, "note": why,
                            "size": _video.stat().st_size if _video.is_file() else 0,
                        }
                        _part.save_meta(pm)
                        return spent

                    compress_futs.append(compress_pool.submit(do_compress))

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
                # 作业要求与课堂笔记互不依赖，两条请求并行
                scheduler.parallel_map(
                    lambda fn: fn(),
                    [
                        lambda: digest_mod.build_homework(cfg, lec_dir, transcript, slides,
                                                          force=args.force),
                        lambda: digest_mod.build_notes(cfg, lec_dir, transcript, slides,
                                                       force=args.force),
                    ],
                    workers=2, label="归纳")
            if unit in ("session", "both"):
                for ref in refs:
                    if ref.run.full_transcript.is_file():
                        t = ref.run.full_transcript.read_text(encoding="utf-8")
                        s = (ref.run.slides_path.read_text(encoding="utf-8")
                             if ref.run.slides_path.is_file() else "")
                        digest_mod.build_homework(cfg, ref.run, t, s, force=args.force)
                        digest_mod.build_notes(cfg, ref.run, t, s, force=args.force)
    finally:
        if compress_pool is not None:
            for fut in compress_futs:
                try:
                    stats["compress"] += fut.result()
                except Exception as exc:
                    print(f"  [压缩] 失败：{exc}")
            compress_pool.shutdown(wait=True)

    total = time.perf_counter() - started
    print(f"[time] {lec_dir.root.name}: 总 {total:.0f}s | 抽音频 {stats['audio']:.0f}s | "
          f"ASR {stats['asr']:.0f}s | OCR {stats['ocr']:.0f}s | 压缩 {stats['compress']:.0f}s")
    meta = lec_dir.load_meta()
    meta["timings"] = {k: round(v, 1) for k, v in stats.items()}
    meta["timings"]["total"] = round(total, 1)
    lec_dir.save_meta(meta)


def _run_pipeline(cfg: Config, args, date_from: str, date_to: str) -> int:
    scheduler.configure(cfg)
    local = getattr(args, "from_dir", "") or ""
    if local:
        jobs = _jobs_from_local_dir(cfg, local)
        if not jobs:
            print(f"{local} 下没有找到可处理的整课目录。")
            return 1
        if getattr(args, "dry_run", False):
            print("\n（--dry-run：未处理）")
            return 0
        for lec, lec_dir, videos in jobs:
            print(f"\n===== 处理整课 {lec_dir.root} =====")
            _process_lecture(cfg, lec, lec_dir, videos, args)
        print("\n全部完成。")
        return 0

    gen = _iter_lectures(cfg, args, date_from, date_to)
    count = 0
    try:
        if getattr(args, "dry_run", False):
            for _ in gen:
                count += 1
            print("\n（--dry-run：未下载、未处理）")
            return 0

        if bool(getattr(cfg.concurrency, "pipeline", True)):
            # 主线程继续下载第 N+1 节，工作线程处理第 N 节
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="proc") as pool:
                futs = []
                for job in gen:
                    count += 1
                    futs.append(pool.submit(_process_lecture, cfg, *job, args))
                    for f in [f for f in futs if f.done()]:
                        f.result()   # 先完成的先报错，不把异常压到最后
                for f in futs:
                    f.result()
        else:
            for job in gen:
                count += 1
                _process_lecture(cfg, *job, args)
    finally:
        gen.close()

    if count == 0:
        return 1
    print("\n全部完成。")
    return 0


def _jobs_from_local_dir(cfg: Config, root: str) -> list[tuple[Lecture, LectureDir, dict]]:
    """不联网重建：读本地整课包（lecture.json + parts/*/videos）。

    视频已经下载完但处理中断时用这个续跑，省掉重新发现/取流/下载。
    也接受数据根目录（会递归找出所有整课包）。
    """
    from .discover import Session

    base = Path(root).resolve()
    if not base.is_dir():
        raise SystemExit(f"目录不存在：{base}")

    meta_files = sorted(set(base.rglob("lecture.json")) | set(base.rglob("lecture_*.json")))
    # 跳过 _state/_profile/_手工初版 等归档目录，避免把备份当成待处理整课
    meta_files = [p for p in meta_files
                  if not any(part.startswith("_") for part in p.relative_to(base).parts)]
    if not meta_files:
        return []

    jobs = []
    seen: set[tuple[str, str]] = set()
    for meta_p in meta_files:
        lec_root = meta_p.parent
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
        except Exception:
            print(f"  [跳过] {meta_p}：不是合法 JSON")
            continue
        uid = str(meta.get("uid") or "")
        key = (str(lec_root), uid)
        if key in seen:
            continue
        seen.add(key)
        lec_dir = LectureDir(lec_root, uid=uid)
        parts = []
        for item in meta.get("parts", []):
            slot = item.get("slot", "")
            if not slot:
                continue
            parts.append(Session(
                new_id=item.get("new_id", ""), date=meta.get("date", ""),
                start=item.get("start", ""), end=item.get("end", ""),
                text=item.get("text", ""), course=meta.get("course", ""),
            ))
        if not parts:
            print(f"  [跳过] {lec_root}：lecture.json 里没有 parts")
            continue
        lec = Lecture(course=meta.get("course", ""), date=meta.get("date", ""), parts=parts)

        videos: dict[str, Path] = {}
        for p in parts:
            part = lec_dir.part(p.slot).ensure()
            picks = sorted(part.videos.glob("*.mp4"))
            if not picks:
                print(f"  [跳过小节] {part.root}：没有视频")
                continue
            # 优先 VGA（课件机位）
            vga = [v for v in picks if v.stem.lower().endswith("_vga")]
            videos[p.slot] = (vga or picks)[0]
        print(f"  【{lec.label}】 {lec.course} {lec.date}  本地视频 {len(videos)}/{len(parts)}")
        jobs.append((lec, lec_dir, videos))
    return jobs


# --------------------------------------------------------------------------- #
# run / daily
# --------------------------------------------------------------------------- #
def cmd_run(args) -> int:
    cfg = load_config(args.config)
    if getattr(args, "jobs", 0):
        cfg.concurrency.gateway = int(args.jobs)
    date_from, date_to = _resolve_range(args)
    with solo_lock(cfg):
        return _run_pipeline(cfg, args, date_from, date_to)


def cmd_resume(args) -> int:
    """不联网，直接从已下载的整课包续跑处理（转写/OCR/归纳/压缩）。"""
    cfg = load_config(args.config)
    if getattr(args, "jobs", 0):
        cfg.concurrency.gateway = int(args.jobs)
    from .config import runs_root
    base = Path(args.dir).resolve() if args.dir else runs_root(cfg, None)
    args.from_dir = str(base)
    args.date = args.date_from = args.date_to = ""
    with solo_lock(cfg):
        return _run_pipeline(cfg, args, "", "")


def cmd_daily(args) -> int:
    """无人值守：处理当天监控清单里所有课的整课。"""
    cfg = load_config(args.config)
    if getattr(args, "jobs", 0):
        cfg.concurrency.gateway = int(args.jobs)
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

    def one(video: Path) -> str:
        part = RunDir(video.parent.parent)
        part.ensure()
        if part.load_meta().get("retained") and not args.force:
            print(f"  {video.relative_to(base)}: 已处理过，跳过")
            return "已处理过，跳过"
        have_backup = any(part.frames.glob("*.jpg")) and any(part.audio.glob("*.mp3"))
        _, why = videoops_mod.apply_retention(cfg, video, keep,
                                              have_backup=have_backup, force=args.force)
        print(f"  {video.relative_to(base)}: {why}")
        pm = part.load_meta()
        pm["retained"] = {"keep": keep, "note": why,
                          "size": video.stat().st_size if video.is_file() else 0}
        part.save_meta(pm)
        return why

    videos = [v for v in sorted(base.rglob("*.mp4"))
              if ".compressing." not in v.name
              and not any(p.startswith("_") for p in v.relative_to(base).parts)]
    try:
        if videos:
            scheduler.parallel_map(one, videos,
                                   workers=max(1, int(cfg.concurrency.compress or 1)),
                                   label="压缩")
    finally:
        policy.keep_video = old_keep
    print(f"处理了 {len(videos)} 个视频。")
    return 0


def cmd_migrate_layout(args) -> int:
    """把 data/<课程>/<日期>/<时段>/ 扁平化到 data/<课程>/<日期>/。"""
    cfg = load_config(args.config)
    n, m = migrate_mod.flatten(cfg, args.out, dry_run=args.dry_run)
    verb = "将迁移" if args.dry_run else "已迁移"
    print(f"{verb} {n} 个整课目录，重命名 {m} 个小节级 homework/notes。")
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


def _part_uid(root: Path) -> str:
    """从 data/<课程>/<日期>/parts/<时段>/ 推出小节的唯一标识。"""
    parts = root.parts
    if "parts" in parts:
        i = parts.index("parts")
        if i >= 2 and i + 1 < len(parts):
            return f"{parts[i - 1]}_{parts[i + 1]}"
    return ""


def _dirs_with_videos(root: Path) -> list[RunDir]:
    """找到所有含视频的小节目录（兼容新旧两种布局）。"""
    out = []
    for v in root.rglob("videos"):
        if not v.is_dir():
            continue
        d = RunDir(v.parent, uid=_part_uid(v.parent))
        if d.meta_path.is_file() or any(v.glob("*.mp4")):
            d.ensure()
            out.append(d)
    return out


def cmd_asr(args) -> int:
    cfg = load_config(args.config)
    scheduler.configure(cfg)
    root = _resolve_dir(args)
    for d in _dirs_with_videos(root):
        for video in sorted(d.videos.glob("*.mp4")):
            mp3 = audio_mod.extract_audio(cfg, video, d.audio / (video.stem + ".mp3"),
                                          cfg.asr.bitrate)
            asr_mod.transcribe(cfg, d, mp3, provider=args.asr, force=args.force)
    return 0


def cmd_slides(args) -> int:
    cfg = load_config(args.config)
    scheduler.configure(cfg)
    root = _resolve_dir(args)
    for d in _dirs_with_videos(root):
        for video in sorted(d.videos.glob("*.mp4")):
            vision_mod.scan(cfg, d, video, mode=args.mode, force=args.force)
    return 0


def cmd_digest(args) -> int:
    cfg = load_config(args.config)
    root = _resolve_dir(args)
    metas = [p for p in [root / "lecture.json", *sorted(root.glob("lecture_*.json"))]
             if p.is_file()]
    if metas:
        for meta_p in metas:
            try:
                meta = json.loads(meta_p.read_text(encoding="utf-8"))
            except Exception:
                continue
            lec_dir = LectureDir(root, uid=str(meta.get("uid") or ""))
            transcript = (lec_dir.transcript_path.read_text(encoding="utf-8")
                          if lec_dir.transcript_path.is_file() else "")
            slides = (lec_dir.slides_path.read_text(encoding="utf-8")
                      if lec_dir.slides_path.is_file() else "")
            if not transcript and not slides:
                print(f"  [跳过] {lec_dir.root}：既没有整课转写也没有课件文字")
                continue
            jobs = []
            if not args.no_homework:
                jobs.append(lambda: digest_mod.build_homework(cfg, lec_dir, transcript, slides,
                                                              force=args.force))
            if not args.no_notes:
                jobs.append(lambda: digest_mod.build_notes(cfg, lec_dir, transcript, slides,
                                                           force=args.force))
            if len(jobs) > 1:
                scheduler.parallel_map(lambda fn: fn(), jobs, workers=2, label="归纳")
            else:
                for fn in jobs:
                    fn()
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


def cmd_bench(args) -> int:
    """用已缓存的音频块/抽帧测网关并发与模型速度，不覆盖任何缓存。"""
    cfg = load_config(args.config)
    stages = [s.strip() for s in (args.stage or "asr,ocr").split(",") if s.strip()]
    concs = [int(x) for x in (args.concurrency or "1,2,4").replace("，", ",").split(",")
             if x.strip()]
    models_asr = [m.strip() for m in (args.asr_models or "").split(",") if m.strip()]
    if not models_asr:
        models_asr = [cfg.asr.model] + list(cfg.asr.fallback_models or [])
    models_ocr = [m.strip() for m in (args.ocr_models or "").split(",") if m.strip()]
    if not models_ocr:
        models_ocr = [cfg.vision.model]
    return bench_mod.run(cfg, args.run, stages, models_asr, models_ocr, concs, args.n)


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
    sp.add_argument("--jobs", type=int, default=0, metavar="N",
                    help="网关并发数（覆盖 [concurrency] gateway）")
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
    sp.add_argument("--jobs", type=int, default=0, metavar="N")
    sp.set_defaults(func=cmd_daily, course="", time_from="", time_to="",
                    weekday="", gap=0, date_from="", date_to="")

    sp = sub.add_parser("resume", help="不联网：对已下载的整课包续跑转写/OCR/归纳/压缩")
    sp.add_argument("--dir", help="整课包目录或数据根目录（默认取配置 runs_root）")
    sp.add_argument("--unit", choices=UNIT_CHOICES, default="lecture")
    sp.add_argument("--asr", choices=["gateway", "whisper"], help="转写通道")
    sp.add_argument("--keep-video", choices=["none", "compressed", "original"],
                    help="视频保留策略（默认取配置）")
    sp.add_argument("--force", action="store_true", help="忽略缓存，全部重跑")
    sp.add_argument("--dry-run", action="store_true", help="只列出将要处理的整课")
    sp.add_argument("--no-slides", action="store_true", help="跳过课件 OCR")
    sp.add_argument("--no-digest", action="store_true", help="跳过作业汇总/笔记")
    sp.add_argument("--jobs", type=int, default=0, metavar="N",
                    help="网关并发数（覆盖 [concurrency] gateway）")
    sp.add_argument("--headed", action="store_true", help=argparse.SUPPRESS)
    sp.add_argument("--config", help=argparse.SUPPRESS)
    sp.set_defaults(func=cmd_resume, date_from="", date_to="", course="",
                    time_from="", time_to="", weekday="", gap=0, tracks="vga",
                    out=None, from_dir="")

    sp = sub.add_parser("bench", help="测网关并发/模型速度（用已缓存的音频块和抽帧）")
    sp.add_argument("--run", required=True, help="整课包 / 数据根目录 / 小节目录")
    sp.add_argument("--stage", default="asr,ocr", help="asr / ocr / asr,ocr（默认都测）")
    sp.add_argument("--asr-models", dest="asr_models", default="",
                    help="逗号分隔；默认取配置主模型 + fallback_models")
    sp.add_argument("--ocr-models", dest="ocr_models", default="",
                    help="逗号分隔；默认取 vision.model")
    sp.add_argument("--concurrency", default="1,2,4", help="逗号分隔的并发度，如 1,2,4,6")
    sp.add_argument("--n", type=int, default=3, help="每种配置取几个块/批（默认 3）")
    sp.set_defaults(func=cmd_bench)

    sp = sub.add_parser("regroup", help="把旧的小节目录原地合并成整课包（不重新下载）")
    sp.add_argument("--out", help="数据根目录（默认取配置 runs_root）")
    sp.add_argument("--no-digest", action="store_true", help="只合并，不重新生成作业要求/笔记")
    sp.set_defaults(func=cmd_regroup)

    sp = sub.add_parser("migrate-layout",
                        help="把 <日期>/<时段>/ 扁平化为 <日期>/（homework/notes 带唯一后缀）")
    sp.add_argument("--out", help="数据根目录（默认取配置 runs_root）")
    sp.add_argument("--dry-run", action="store_true", help="只打印将要移动的内容")
    sp.set_defaults(func=cmd_migrate_layout)

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

    sp = sub.add_parser("serve", help="本机 Web 界面（浏览整课包 + 触发任务）")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8000)
    sp.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    sp.set_defaults(func=cmd_serve)

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
