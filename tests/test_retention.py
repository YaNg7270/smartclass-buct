"""保留策略测试：重点是"校验不过绝不能删原件"。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass import videoops                                   # noqa: E402
from smartclass.config import Config                              # noqa: E402


def _cfg():
    return Config()


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
