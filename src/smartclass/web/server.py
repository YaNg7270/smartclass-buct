"""scls serve：本机 Flask 应用（路由 + API + cmd_serve 入口）。

浏览只读 scans runs_root；运行控制经子进程调用 scls CLI；
媒体服务仅限 runs_root 内的 .mp4/.jpg/.mp3。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import Config, load_config, runs_root
from ..state import login_needed, login_needed_path
from ..workspace import LectureDir, safe_name
from . import scan
from .format import render_md, render_slides, render_transcript
from .jobs import JobManager, busy_locks

if TYPE_CHECKING:
    from flask import Flask

MEDIA_EXTS = {".mp4", ".jpg", ".mp3"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TRACKS_RE = re.compile(r"^[\w,]+$")
_UNIT_CHOICES = {"lecture", "session", "both"}
_KEEP_CHOICES = {"none", "compressed", "original"}
_VERBS = {"run", "resume", "daily", "list"}


def create_app(cfg: Config) -> "Flask":
    import flask

    app = flask.Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    jobs = JobManager(cfg)

    # ---- 只读浏览 -------------------------------------------------------- #

    @app.get("/")
    def index():
        return flask.render_template(
            "index.html",
            courses=scan.course_summary(cfg),
            login_needed=_login_state(),
        )

    @app.get("/course/<name>")
    def course(name: str):
        dates = []
        for d in scan.list_dates(cfg, name):
            dates.append({"name": d.name, "lectures": scan.lectures_in(cfg, name, d.name)})
        if not dates:
            flask.abort(404)
        return flask.render_template("course.html", course=name, dates=dates)

    @app.get("/lecture/<course>/<date>")
    @app.get("/lecture/<course>/<date>/<uid>")
    def lecture(course: str, date: str, uid: str = ""):
        lectures = scan.lectures_in(cfg, course, date)
        if not lectures:
            flask.abort(404)
        if not uid:
            return flask.redirect(lectures[0]["url"])
        entry = next((x for x in lectures if x["uid"] == uid), None)
        if entry is None:
            flask.abort(404)
        d = LectureDir(runs_root(cfg) / safe_name(course, "课程") / date, uid=uid)
        parts = scan.list_parts(d.root, uid)
        interval = int(cfg.vision.interval_seconds)
        for part in parts:
            part["media_rel"] = [
                (p.relative_to(runs_root(cfg)).as_posix(), _frame_ts(i, interval))
                for i, p in enumerate(part["frames"], 1)
            ]
            part["video_rel"] = [
                p.relative_to(runs_root(cfg)).as_posix() for p in part["videos"]]
        meta_pretty = ""
        if d.meta_path.is_file():
            try:
                meta_pretty = json.dumps(
                    json.loads(d.meta_path.read_text(encoding="utf-8")),
                    ensure_ascii=False, indent=2)
            except Exception:
                meta_pretty = d.meta_path.read_text(encoding="utf-8", errors="replace")
        content = {
            "transcript": _read_text(d.transcript_path, render_transcript),
            "slides": _read_text(d.slides_path, render_slides),
            "homework": _read_text(d.homework_path, render_md),
            "notes": _read_text(d.notes_path, render_md),
        }
        resume_hint = f'scls resume --dir "data/{course}"'
        return flask.render_template(
            "lecture.html", entry=entry, course=course, date=date,
            parts=parts, meta_pretty=meta_pretty, content=content,
            resume_hint=resume_hint,
            lectures=[x["uid"] for x in lectures],
        )

    @app.get("/search")
    def search():
        q = flask.request.args.get("q", "")
        results = scan.search_text(cfg, q) if q.strip() else []
        return flask.render_template(
            "search.html", q=q, results=results,
            searched=bool(q.strip()), limit_reached=len(results) >= 200)

    # ---- 媒体服务（单区间 Range） ----------------------------------------- #

    @app.get("/media/<path:relpath>")
    def media(relpath: str):
        root = runs_root(cfg).resolve()
        p = (root / relpath).resolve()
        try:
            p.relative_to(root)
        except ValueError:  # 路径穿越
            flask.abort(404)
        if not p.is_file() or p.suffix.lower() not in MEDIA_EXTS:
            flask.abort(404)
        if p.suffix.lower() == ".mp4":
            return _send_range(p)
        return flask.send_file(p)

    def _send_range(p: Path):
        size = p.stat().st_size
        start, end, status = 0, size - 1, 200
        rng = flask.request.headers.get("Range", "").strip()
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng) if rng else None
        if rng and not m:
            flask.abort(416)
        if m:
            s, e = m.group(1), m.group(2)
            if s == "" and e == "":
                flask.abort(416)
            if s == "":  # 后缀区间：最后 N 字节
                start = max(0, size - int(e))
            else:
                start = int(s)
                end = min(int(e), size - 1) if e else size - 1
            if start >= size or start > end:
                resp = flask.Response(status=416,
                                      headers={"Content-Range": f"bytes */{size}"})
                return resp
            status = 206
        length = end - start + 1
        fh = open(p, "rb")

        def stream():
            try:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(1 << 20, remaining))  # 1MB 分块，别整段进内存
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
            finally:
                fh.close()

        resp = flask.Response(stream(), status=status, mimetype="video/mp4")
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        resp.headers["Content-Length"] = str(length)
        return resp

    # ---- 运行控制 API ----------------------------------------------------- #

    @app.post("/api/job")
    def api_job_post():
        data = flask.request.get_json(silent=True) or {}
        built = _build_args(data)
        if isinstance(built, str):
            return flask.jsonify({"ok": False, "error": built}), 400
        verb, argv = built
        result = jobs.start(verb, argv)
        code = result.pop("code", 200)
        return flask.jsonify(result), (200 if result.get("ok") else code)

    @app.get("/api/job")
    def api_job_get():
        return flask.jsonify(jobs.status())

    @app.post("/api/job/cancel")
    def api_job_cancel():
        result = jobs.cancel()
        return flask.jsonify(result), (200 if result.get("ok") else 409)

    @app.get("/api/status")
    def api_status():
        state = _login_state()
        return flask.jsonify({
            "login_needed": state["login_needed"],
            "detail": state["detail"],
            "busy_locks": busy_locks(cfg),
        })

    @app.post("/api/login/clear")
    def api_login_clear():
        from ..state import clear_login_needed
        clear_login_needed(cfg)
        return flask.jsonify({"ok": True})

    # ---- 模板辅助 --------------------------------------------------------- #

    def _login_state() -> dict:
        detail = None
        if login_needed(cfg):
            path = login_needed_path(cfg)
            try:
                detail = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                detail = None
            if detail:
                detail = detail.splitlines()[0][:200]
        return {"login_needed": bool(detail) or login_needed(cfg), "detail": detail}

    app.jinja_env.globals["job_status"] = jobs.status

    return app


# ---- 参数白名单组装（绝无 shell 拼接） ------------------------------------- #

def _build_args(data: dict):
    """按白名单把 JSON 体组装成 (verb, argv)；非法输入返回错误字符串。"""
    if not isinstance(data, dict):
        return "请求体必须是 JSON 对象。"
    verb = str(data.get("verb") or "")
    if verb not in _VERBS:
        return "verb 必须是 run / resume / daily / list 之一。"

    def opt(key: str):
        v = data.get(key)
        return str(v).strip() if isinstance(v, str) and v.strip() else None

    def flag(key: str) -> bool:
        return data.get(key) is True

    date = opt("date")
    date_from, date_to = opt("date_from"), opt("date_to")
    for name, v in (("--date", date), ("--from", date_from), ("--to", date_to)):
        if v and not _DATE_RE.match(v):
            return f"{name} 的日期格式必须是 YYYY-MM-DD。"
    unit = opt("unit")
    if unit and unit not in _UNIT_CHOICES:
        return "unit 必须是 lecture / session / both 之一。"
    keep = opt("keep_video")
    if keep and keep not in _KEEP_CHOICES:
        return "keep_video 必须是 none / compressed / original 之一。"
    tracks = opt("tracks")
    if tracks and not _TRACKS_RE.match(tracks):
        return "tracks 只能包含机位名、逗号（如 vga,video1）。"
    course = opt("course")

    argv: list[str] = []
    if verb == "run":
        if not (date or date_from or date_to):
            return "run 需要至少提供 date 或 from/to 其一。"
        if date:
            argv += ["--date", date]
        if date_from:
            argv += ["--from", date_from]
        if date_to:
            argv += ["--to", date_to]
        if unit:
            argv += ["--unit", unit]
        if tracks:
            argv += ["--tracks", tracks]
        if keep:
            argv += ["--keep-video", keep]
        if course:
            argv += ["--course", course]
        for key, optname in (("force", "--force"), ("dry_run", "--dry-run"),
                             ("no_slides", "--no-slides"), ("no_digest", "--no-digest")):
            if flag(key):
                argv.append(optname)
    elif verb == "daily":
        if flag("dry_run"):
            argv.append("--dry-run")
    elif verb == "resume":
        d = opt("dir")
        if not d:
            return "resume 需要 dir 参数（整课包目录或数据根目录）。"
        argv += ["--dir", d]
        if unit:
            argv += ["--unit", unit]
        if keep:
            argv += ["--keep-video", keep]
        for key, optname in (("force", "--force"), ("dry_run", "--dry-run"),
                             ("no_slides", "--no-slides"), ("no_digest", "--no-digest")):
            if flag(key):
                argv.append(optname)
    else:  # list：仅筛选参数
        if date:
            argv += ["--date", date]
        if date_from:
            argv += ["--from", date_from]
        if date_to:
            argv += ["--to", date_to]
        if course:
            argv += ["--course", course]
    return verb, argv


# ---- 小工具 --------------------------------------------------------------- #

def _frame_ts(index: int, interval: int) -> str:
    """帧文件序号（1 起）→ 时间戳；f_001 即 t=0:00，与 OCR 批次时间轴一致。"""
    sec = (index - 1) * interval
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _read_text(path: Path, render) -> str:
    if not path.is_file():
        return ""
    try:
        return render(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return ""


def cmd_serve(args) -> int:
    """`scls serve` 入口：惰性导入 flask，起服务，可自动开浏览器。"""
    import importlib.util
    if importlib.util.find_spec("flask") is None:
        print("缺少依赖：请先运行 `python -m pip install -e .` 安装 flask。",
              file=sys.stderr)
        return 1

    cfg = load_config(args.config)
    app = create_app(cfg)
    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        import threading
        import webbrowser
        threading.Timer(1.0, webbrowser.open, [url]).start()
    print(f"scls Web 界面：{url}（Ctrl+C 退出）")
    try:
        app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    except OSError as exc:
        print(f"无法启动 Web 服务：{exc}", file=sys.stderr)
        return 1
    return 0
