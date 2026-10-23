"""Web 界面测试：只读浏览、全文搜索、媒体 Range、运行控制 API。

全程不联网、不碰真实 data/；子进程一律 FakePopen（锁测试只判活不真启动）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass.config import Config, REPO_ROOT  # noqa: E402
from smartclass.web import create_app         # noqa: E402

COURSE = "物理化学(Ⅰ)"
DATE = "2026-10-16"
UID = f"{DATE}_0800-1030"
SLOT = "0800-0845"


# ---- 测试数据 -------------------------------------------------------------- #

def _cfg(tmp_path: Path, monkeypatch) -> Config:
    cfg = Config()
    monkeypatch.setattr(cfg.paths, "runs_root", str(tmp_path))
    return cfg


def _mk_package(root: Path) -> None:
    """造一个最小整课包：lecture.json / transcript / homework / 帧。"""
    d = root / COURSE / DATE
    (d / f"parts/{SLOT}/frames").mkdir(parents=True)
    (d / f"parts/{SLOT}/frames/f_001.jpg").write_bytes(b"\xff\xd8fake")
    (d / f"parts/{SLOT}/videos").mkdir(parents=True)
    (d / f"parts/{SLOT}/videos/demo.mp4").write_bytes(bytes(range(256)) * 4)
    (d / "lecture.json").write_text(json.dumps({
        "course": COURSE, "date": DATE,
        "start": "08:00:00", "end": "10:30:00", "span": "0800-1030",
        "parts": [{"slot": SLOT, "start": "08:00:00", "end": "08:45:00"}],
        "uid": UID, "layout": "flat",
    }, ensure_ascii=False), encoding="utf-8")
    (d / "transcript.txt").write_text(
        "# 整课转写 · 测试\n"
        "\n======================== 【08:00-08:45】 ========================\n\n"
        "[02:44] 这是一句带时间戳的转写，提到热力学第一定律。\n"
        "普通行\n",
        encoding="utf-8")
    (d / f"homework_{UID}.md").write_text(
        "# 作业要求\n\n- 完成第一题\n", encoding="utf-8")


def _client(tmp_path: Path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _mk_package(tmp_path)
    return create_app(cfg).test_client(), cfg


# ---- 只读浏览 --------------------------------------------------------------- #

def test_index_lists_course(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    html = client.get("/").get_data(as_text=True)
    assert COURSE in html
    assert "1 节课" in html


def test_course_page_badges(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    html = client.get(f"/course/{COURSE}").get_data(as_text=True)
    assert DATE in html and UID in html
    assert "转写✓" in html and "作业✓" in html
    assert "笔记" in html and "未生成" in html  # 没造 notes，应显示未生成


def test_lecture_page_renders_transcript_and_markdown(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    html = client.get(f"/lecture/{COURSE}/{DATE}/{UID}").get_data(as_text=True)
    assert 'id="t-0244"' in html and "[02:44]" in html and "热力学第一定律" in html
    assert "【08:00-08:45】" in html
    assert "<li>完成第一题</li>" in html          # homework md → HTML
    assert "/media/" in html and "f_001.jpg" in html  # 帧走媒体服务
    assert "demo.mp4" in html


def test_lecture_missing_artifact_degrades(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    html = client.get(f"/lecture/{COURSE}/{DATE}/{UID}").get_data(as_text=True)
    assert "未生成" in html and "scls resume --dir" in html


def test_suffixed_lecture_resolves_own_files(tmp_path, monkeypatch):
    """同日第二节整课：lecture_<uid>.json + transcript_<uid>.txt 各归各的。

    其 meta slot 在盘上不存在（未下载完）时，也不得借用第一节的小节/帧。
    """
    client, _ = _client(tmp_path, monkeypatch)
    uid2 = f"{DATE}_1040-1215"
    d = tmp_path / COURSE / DATE
    (d / "lecture_2026-10-16_1040-1215.json").write_text(json.dumps({
        "course": COURSE, "date": DATE, "span": "1040-1215",
        "parts": [{"slot": "1040-1125"}], "uid": uid2, "layout": "flat",
    }, ensure_ascii=False), encoding="utf-8")
    (d / f"transcript_{uid2}.txt").write_text("[00:10] 第二节的话\n", encoding="utf-8")
    (d / f"homework_{uid2}.md").write_text("# 第二节作业\n", encoding="utf-8")

    html = client.get(f"/lecture/{COURSE}/{DATE}/{uid2}").get_data(as_text=True)
    assert "第二节的话" in html
    assert "热力学第一定律" not in html  # 不能串到第一节的转写
    assert "/media/" not in html         # 第一节的帧/视频不得出现在本页


def test_search_hits_transcript_line(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    html = client.get("/search?q=热力学").get_data(as_text=True)
    assert "热力学第一定律" in html and f"{COURSE}/{DATE}/transcript.txt" in html
    assert "打开整课页" in html
    assert client.get("/search?q=不存在的词xyz").get_data(as_text=True).count("没有匹配") == 1
    assert "没有匹配" not in client.get("/search?q=").get_data(as_text=True)


# ---- 媒体服务 ---------------------------------------------------------------- #

def test_media_serves_frames(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    url = f"/media/{COURSE}/{DATE}/parts/{SLOT}/frames/f_001.jpg"
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.data[:2] == b"\xff\xd8"


def test_media_rejects_traversal_and_bad_ext(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    for url in ("/media/%2e%2e/lecture.json",
                f"/media/{COURSE}/{DATE}/lecture.json",
                "/media/%2e%2e/%2e%2e/etc/passwd"):
        assert client.get(url).status_code == 404, url


def test_media_mp4_range_single_segment(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    url = f"/media/{COURSE}/{DATE}/parts/{SLOT}/videos/demo.mp4"
    whole = client.get(url)
    assert whole.status_code == 200
    assert whole.headers["Accept-Ranges"] == "bytes"

    part = client.get(url, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206
    assert part.headers["Content-Range"] == "bytes 0-99/1024"
    assert part.headers["Content-Length"] == "100"
    assert part.data == bytes(range(100))

    tail = client.get(url, headers={"Range": "bytes=-10"})
    assert tail.status_code == 206 and tail.headers["Content-Length"] == "10"

    bad = client.get(url, headers={"Range": "bytes=9999-"})
    assert bad.status_code == 416


# ---- 登录失效状态 ------------------------------------------------------------- #

def test_login_needed_banner_state(tmp_path, monkeypatch):
    client, cfg = _client(tmp_path, monkeypatch)
    assert client.get("/api/status").get_json()["login_needed"] is False

    state = Path(cfg.paths.runs_root) / "_state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "login-needed.txt").write_text("网关返回 401\n", encoding="utf-8")
    st = client.get("/api/status").get_json()
    assert st["login_needed"] is True and "401" in st["detail"]

    assert client.post("/api/login/clear").get_json()["ok"] is True
    assert client.get("/api/status").get_json()["login_needed"] is False


# ---- 运行控制 API -------------------------------------------------------------- #

class FakeProc:
    """替身进程：记录 argv，可手动置退出码。"""

    instances: list["FakeProc"] = []

    def __init__(self, argv, stdout=None, stderr=None, cwd=None):
        self.argv = argv
        self.pid = 4321
        self.returncode = None
        self.terminated = False
        self.cwd = cwd
        FakeProc.instances.append(self)

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True


def _patch_popen(monkeypatch):
    FakeProc.instances = []
    monkeypatch.setattr("smartclass.web.jobs.subprocess.Popen", FakeProc)


def test_api_job_run_whitelist_argv(tmp_path, monkeypatch):
    client, cfg = _client(tmp_path, monkeypatch)
    _patch_popen(monkeypatch)

    resp = client.post("/api/job", json={
        "verb": "run", "date": DATE, "unit": "lecture", "tracks": "vga,video1",
        "keep_video": "compressed", "dry_run": True, "force": False,
        "no_slides": True, "evil": "--headed",
    })
    assert resp.status_code == 200 and resp.get_json()["ok"] is True
    argv = FakeProc.instances[0].argv
    assert argv[:4] == [sys.executable, "-m", "smartclass.cli", "run"]
    for pair in (("--date", DATE), ("--unit", "lecture"),
                 ("--tracks", "vga,video1"), ("--keep-video", "compressed")):
        i = argv.index(pair[0])
        assert argv[i + 1] == pair[1]
    assert "--dry-run" in argv and "--no-slides" in argv
    assert "--force" not in argv and "--headed" not in argv and "evil" not in " ".join(argv)
    assert FakeProc.instances[0].cwd == str(REPO_ROOT)


def test_api_job_validation_errors(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    _patch_popen(monkeypatch)
    bad = [
        {"verb": "run"},                                        # run 缺日期
        {"verb": "run", "date": "2026/10/16"},                  # 日期格式
        {"verb": "run", "date": DATE, "unit": "all"},           # unit 枚举
        {"verb": "run", "date": DATE, "keep_video": "zip"},     # keep 枚举
        {"verb": "run", "date": DATE, "tracks": "vga; rm -rf"},  # 机位字符
        {"verb": "attack", "date": DATE},                       # verb 白名单
        {"verb": "resume"},                                     # resume 缺 dir
    ]
    for body in bad:
        resp = client.post("/api/job", json=body)
        assert resp.status_code == 400, body
        assert resp.get_json()["ok"] is False


def test_api_job_lifecycle_and_exit_code(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    _patch_popen(monkeypatch)

    client.post("/api/job", json={"verb": "list", "course": "线性代数"})
    proc = FakeProc.instances[0]
    st = client.get("/api/job").get_json()
    assert st["running"] is True and st["exit_code"] is None
    assert st["verb"] == "list" and "线性代数" in " ".join(st["args"])

    proc.returncode = 3  # 数据锁被占
    st = client.get("/api/job").get_json()
    assert st["running"] is False and st["exit_code"] == 3

    client.post("/api/job", json={"verb": "list"})
    proc2 = FakeProc.instances[1]
    proc2.returncode = 130
    assert client.get("/api/job").get_json()["exit_code"] == 130


def test_api_job_cancel_calls_terminate(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    _patch_popen(monkeypatch)

    client.post("/api/job", json={"verb": "list"})
    resp = client.post("/api/job/cancel")
    assert resp.status_code == 200
    assert FakeProc.instances[0].terminated is True


def test_api_job_rejected_when_lock_busy(tmp_path, monkeypatch):
    client, cfg = _client(tmp_path, monkeypatch)
    _patch_popen(monkeypatch)
    monkeypatch.setattr("smartclass.web.jobs._pid_alive", lambda pid: True)

    lock = Path(cfg.paths.runs_root) / "_state/run.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("99999", encoding="utf-8")

    resp = client.post("/api/job", json={"verb": "list"})
    assert resp.status_code == 409
    assert "run.lock" in resp.get_json()["error"]
    assert FakeProc.instances == []  # 没有真正启动子进程
