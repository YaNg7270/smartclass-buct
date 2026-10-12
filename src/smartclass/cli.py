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
from . import streams as streams_mod
from . import vision as vision_mod
from .browser import browser_session, cookie_header, ensure_logged_in
from .config import Config, load_config
from .discover import Session, discover
from .workspace import RunDir, run_dir_for

TRACK_CHOICES = ["vga", "video1"]


def _today() -> str:
    return dt.date.today().isoformat()


def _course_name(args, session: Session) -> str:
    return args.course or session.course or (session.text.split(" ")[0] if session.text else "课程")


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_login(args) -> int:
    cfg = load_config(args.config)
    with browser_session(cfg, headed=True) as (_, page):
        ensure_logged_in(page, cfg, headed=True, interactive=True)
        print("登录态已保存到 profile，后续命令无需再登。")
    return 0


def cmd_list(args) -> int:
    cfg = load_config(args.config)
    date = args.date or _today()
    with browser_session(cfg, headed=args.headed) as (_, page):
        ensure_logged_in(page, cfg, headed=args.headed, interactive=args.headed)
        sessions = discover(cfg, page, date, args.course)
    if not sessions:
        print(f"{date} 没找到课时（课程过滤：{args.course or '无'}）")
        return 1
    print(f"\n{date} 共 {len(sessions)} 个课时：\n")
    for i, s in enumerate(sessions, 1):
        print(f"  {i}. [{s.start[:5]}-{s.end[:5]}] {s.text}")
        print(f"     NewID={s.new_id}")
    return 0


def _process_run(cfg: Config, run: RunDir, video: Path, args) -> None:
    """对一个课时目录依次跑 音频→转写→课件OCR→归纳。"""
    mp3 = audio_mod.extract_audio(cfg, video, run.audio / (video.stem + ".mp3"), cfg.asr.bitrate)
    transcript_path = asr_mod.transcribe(cfg, run, mp3, provider=args.asr, force=args.force)
    transcript = transcript_path.read_text(encoding="utf-8")

    slides_text = ""
    if not args.no_slides:
        slides_path = vision_mod.scan(cfg, run, video, mode="assignment", force=args.force)
        slides_text = slides_path.read_text(encoding="utf-8")

    if not args.no_digest:
        digest_mod.build_homework(cfg, run, transcript, slides_text, force=args.force)
        digest_mod.build_notes(cfg, run, transcript, slides_text, force=args.force)


def cmd_run(args) -> int:
    cfg = load_config(args.config)
    date = args.date or _today()
    tracks = [t.strip() for t in args.tracks.split(",") if t.strip()]

    # ---- 阶段一：浏览器（发现 + 取流 + 下载） ----
    jobs: list[tuple[RunDir, Path]] = []
    with browser_session(cfg, headed=args.headed) as (ctx, page):
        ensure_logged_in(page, cfg, headed=args.headed, interactive=args.headed)
        sessions = discover(cfg, page, date, args.course)
        if not sessions:
            print(f"{date} 没找到课时。")
            return 1
        print(f"\n{date} 命中 {len(sessions)} 个课时，开始取流。\n")

        for s in sessions:
            cookie = cookie_header(ctx)
            info = streams_mod.fetch_streams(cfg, page, s.new_id, cookie=cookie)
            if args.dry_run:
                print(f"\n  [{s.start[:5]}-{s.end[:5]}] {s.text}")
                print(f"    NewID={s.new_id}  scheduleId={info.schedule_id or '?'}"
                      f"  来源={info.source or '-'}")
                for t in info.tracks:
                    size = dl.remote_size(t.url, cookie)
                    print(f"    {t.kind:7s} {dl.human(size) if size else '?':>9s}  {t.url}")
                if not info.tracks:
                    print("    （没有录播机位）")
                continue

            course = _course_name(args, s)
            run = run_dir_for(cfg, course, s.date or date, s.slot, args.out).ensure()
            run.save_meta({
                "new_id": s.new_id, "course": course, "date": s.date or date,
                "start": s.start, "end": s.end, "text": s.text,
                "title": info.title, "schedule_id": info.schedule_id,
                "track_source": info.source,
                "tracks": {t.kind: t.url for t in info.tracks},
            })
            for kind in tracks:
                track = info.by_kind(kind)
                if not track:
                    print(f"  [{s.slot}] 无 {kind} 机位，跳过")
                    continue
                dest = run.videos / f"{course}_{s.date or date}_{s.slot}_{kind}.mp4"
                dl.download(track.url, dest, cookie=cookie, force=args.force)
                jobs.append((run, dest))

    # ---- 阶段二：本地处理（浏览器已关闭） ----
    for run, video in jobs:
        print(f"\n===== 处理 {run.root} =====")
        _process_run(cfg, run, video, args)
    print("\n全部完成。")
    return 0


def _resolve_run(args) -> RunDir:
    run = RunDir(Path(args.run).resolve())
    if not run.root.is_dir():
        raise SystemExit(f"目录不存在：{run.root}")
    return run.ensure()


def cmd_asr(args) -> int:
    cfg = load_config(args.config)
    run = _resolve_run(args)
    videos = sorted(run.videos.glob("*.mp4"))
    if not videos:
        raise SystemExit(f"{run.videos} 里没有 mp4")
    for video in videos:
        mp3 = audio_mod.extract_audio(cfg, video, run.audio / (video.stem + ".mp3"), cfg.asr.bitrate)
        asr_mod.transcribe(cfg, run, mp3, provider=args.asr, force=args.force)
    return 0


def cmd_slides(args) -> int:
    cfg = load_config(args.config)
    run = _resolve_run(args)
    videos = sorted(run.videos.glob("*.mp4"))
    if not videos:
        raise SystemExit(f"{run.videos} 里没有 mp4")
    vision_mod.scan(cfg, run, videos[0], mode=args.mode, force=args.force)
    return 0


def cmd_digest(args) -> int:
    cfg = load_config(args.config)
    run = _resolve_run(args)
    transcript = run.full_transcript.read_text(encoding="utf-8") if run.full_transcript.is_file() else ""
    slides = run.slides_path.read_text(encoding="utf-8") if run.slides_path.is_file() else ""
    if not transcript and not slides:
        raise SystemExit("既没有转写也没有课件文字，先跑 `scls asr` / `scls slides`")
    if not args.no_homework:
        digest_mod.build_homework(cfg, run, transcript, slides, force=args.force)
    if not args.no_notes:
        digest_mod.build_notes(cfg, run, transcript, slides, force=args.force)
    return 0


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scls", description="北化 smartclass 录播课处理工具")
    p.add_argument("--config", help="配置文件路径（默认 ./smartclass.toml 或 ~/.smartclass-buct.toml）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("login", help="有头打开浏览器，登录后保存 profile")
    sp.set_defaults(func=cmd_login)

    sp = sub.add_parser("list", help="列出指定日期的课时")
    sp.add_argument("--date", help="YYYY-MM-DD，默认今天")
    sp.add_argument("--course", default="", help="课程名（模糊匹配）")
    sp.add_argument("--headed", action="store_true", help="有头运行（登录失效时可现场登录）")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("run", help="全流程：发现→下载→转写→课件OCR→作业汇总+笔记")
    sp.add_argument("--date", help="YYYY-MM-DD，默认今天")
    sp.add_argument("--course", default="", help="课程名（模糊匹配）")
    sp.add_argument("--tracks", default="vga", help="机位：vga,video1（默认 vga=课件）")
    sp.add_argument("--out", help="输出根目录（默认取配置 runs_root）")
    sp.add_argument("--asr", choices=["gateway", "whisper"], help="转写通道")
    sp.add_argument("--headed", action="store_true", help="有头运行")
    sp.add_argument("--force", action="store_true", help="忽略缓存，全部重跑")
    sp.add_argument("--dry-run", action="store_true",
                    help="只发现课时 + 取流并打印机位地址，不下载、不处理")
    sp.add_argument("--no-slides", action="store_true", help="跳过课件 OCR")
    sp.add_argument("--no-digest", action="store_true", help="跳过作业汇总/笔记")
    sp.set_defaults(func=cmd_run)

    for name, fn, helptext in (
        ("asr", cmd_asr, "对已有课时目录重跑转写"),
        ("slides", cmd_slides, "对已有课时目录重跑课件 OCR"),
        ("digest", cmd_digest, "对已有课时目录重跑归纳"),
    ):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--run", required=True, help="课时目录（runs/<课程>/<日期>/<时段>）")
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
    # 允许 --config 出现在任意位置（子命令之前之后都行）
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
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"\n错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
