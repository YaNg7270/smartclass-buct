"""保留策略测试：重点是"校验不过绝不能删原件"。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass import videoops                                   # noqa: E402
from smartclass.config import Config                              # noqa: E402


def _cfg():
    cfg = Config()
    cfg.retention.video_encoder = "cpu"   # 单测不探测硬件，保持确定性
    return cfg


def test_keep_original_untouched(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x" * 100)
    out, why = videoops.apply_retention(_cfg(), f, keep="original")
    assert out == f and f.is_file()
    assert "原始" in why


def test_keep_none_requires_backup(tmp_path):
    """没有音频/关键帧备份时，不能删视频。"""
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x" * 100)
    out, why = videoops.apply_retention(_cfg(), f, keep="none", have_backup=False)
    assert out == f and f.is_file(), "无备份时不应删除"
    assert "保留" in why


def test_keep_none_deletes_when_backed_up(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x" * 100)
    out, why = videoops.apply_retention(_cfg(), f, keep="none", have_backup=True)
    assert out is None and not f.is_file()


def test_verify_rejects_garbage(tmp_path):
    f = tmp_path / "bad.mp4"
    f.write_bytes(b"not a video")
    ok, why = videoops.verify(_cfg(), f)
    assert not ok and why


def test_verify_rejects_missing(tmp_path):
    ok, why = videoops.verify(_cfg(), tmp_path / "nope.mp4")
    assert not ok


def test_compress_failure_keeps_original(tmp_path):
    """喂一个假 mp4：ffmpeg 必然失败，原文件必须还在（安全闸）。"""
    f = tmp_path / "fake.mp4"
    f.write_bytes(b"\x00" * 4096)
    out, why = videoops.compress_video(_cfg(), f)
    assert out is None, "压缩失败应返回 None"
    assert f.is_file(), "压缩失败绝不能删掉原文件"
    assert not list(tmp_path.glob("*.compressing.mp4")), "临时文件应清理掉"


def test_ffprobe_available():
    cfg = _cfg()
    for exe in (cfg.paths.ffmpeg, cfg.paths.ffprobe):
        assert subprocess.run([exe, "-version"], capture_output=True).returncode == 0


# ---- ASR 网关空结果回退 ------------------------------------------------ #

def test_asr_survives_empty_choices():
    """网关对某些音频返回 HTTP 200 + choices=[]，旧代码直接 [0] 抛 IndexError。

    这里用假响应确认：空 choices 会被当成"失败"降级，而不是让整节课崩掉。
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

    from smartclass import asr
    from smartclass.config import Config

    cfg = Config()
    cfg.asr.fallback_models = ["good-model"]
    calls = []

    class FakeResp:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

        @property
        def text(self):
            return str(self._body)

    bodies = [
        {"choices": [], "usage": {"prompt_tokens": 15038, "completion_tokens": 0}},
        {"choices": [], "usage": {"prompt_tokens": 15038, "completion_tokens": 0}},
        {"choices": [{"message": {"content": "这是转写正文"}}]},
    ]

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json["model"])
        return FakeResp(bodies[min(len(calls) - 1, len(bodies) - 1)])

    orig_post, orig_dur = asr.requests.post, asr.audio_mod.duration
    asr.requests.post = fake_post
    asr.audio_mod.duration = lambda *a, **k: 600.0
    try:
        tmp = Path(__file__).parent / "_tmp_asr.mp3"
        tmp.write_bytes(b"fake")
        text = asr._transcribe_segment(cfg, tmp, 0, cfg.asr.model)
        tmp.unlink(missing_ok=True)
    finally:
        asr.requests.post, asr.audio_mod.duration = orig_post, orig_dur

    assert text == "这是转写正文"
    assert "good-model" in calls, "空 choices 后应降级到回退模型"
    assert calls.count(cfg.asr.model) >= 1
    assert len(calls) <= 4, "不能让一节音频无限重试"


# ---- 压缩参数 ---------------------------------------------------------- #

def test_compress_command_honors_video_fps(monkeypatch, tmp_path):
    """video_fps 与 scale 必须合并进同一个 -vf，不能互相覆盖。"""
    cfg = _cfg()
    cfg.retention.video_fps = 10
    cfg.retention.video_height = 720
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x" * 200)
    seen = {}

    monkeypatch.setattr(videoops, "bitrate_kbps", lambda c, p: 2000.0)
    monkeypatch.setattr(videoops, "duration", lambda c, p: 120.0)
    monkeypatch.setattr(videoops, "stream_height", lambda c, p: 1080)

    def fake_run(cmd):
        seen["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"y" * 100)

    monkeypatch.setattr(videoops, "_run", fake_run)
    monkeypatch.setattr(videoops, "verify", lambda *a, **k: (True, "ok"))

    out, why = videoops.compress_video(cfg, src)
    assert out == src
    vf = seen["cmd"][seen["cmd"].index("-vf") + 1]
    assert "fps=10" in vf
    assert "scale=-2:720" in vf

