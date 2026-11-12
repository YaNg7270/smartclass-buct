"""携带版不受启动目录、开发机浏览器缓存和 PATH 影响。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass import runtime  # noqa: E402
from smartclass.config import load_config  # noqa: E402
from smartclass import schedule  # noqa: E402


def test_moved_executable_owns_config_data_and_children(tmp_path, monkeypatch):
    root = tmp_path / "移动 目录"
    root.mkdir()
    exe = root / "改过名字.exe"
    unpacked = tmp_path / "_MEI123"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(unpacked), raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.delenv("SCLS_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "smartclass.toml").write_text("[wrong", encoding="utf-8")
    cfg = load_config()
    assert cfg.config_path == str(root / "smartclass.toml")
    assert cfg.paths.runs_root == str(root / "data")
    assert cfg.site.profile_dir == str(root / "data" / "_profile")
    assert Path(cfg.paths.ffprobe) == unpacked / "media" / "ffprobe.exe"
    assert runtime.bundled_file("chromium", "chrome.exe") == unpacked / "chromium" / "chrome.exe"
    assert runtime.cli_command("review", [], cfg) == [
        str(exe), "review", "--config", str(root / "smartclass.toml")]


def test_frozen_schedule_uses_durable_exe_and_config(tmp_path, monkeypatch):
    import subprocess
    exe = tmp_path / "携带版 测试.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "临时"), raising=False)
    cfg = load_config(str(tmp_path / "我的 设置.toml"))
    calls = []

    def fake_task(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(schedule, "_run_schtasks", fake_task)
    schedule.install(cfg)
    task = calls[0][calls[0].index("/TR") + 1]
    assert task == subprocess.list2cmdline([str(exe), "daily", "--config", cfg.config_path])
