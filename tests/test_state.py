"""进程锁测试。

重点：Windows 上 `os.kill(pid, 0)` 会**以退出码 0 杀掉进程**，
所以探活必须走 Win32 API。这条回归测试直接用真子进程验证。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass.config import Config
from smartclass.state import Busy, _pid_alive, solo_lock  # noqa: E402


def test_pid_alive_does_not_kill_the_process():
    """探活一个子进程后，它必须还活着。"""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert _pid_alive(p.pid) is True
        time.sleep(0.5)
        assert p.poll() is None, "探活不该把进程杀掉（Windows 上 os.kill(pid,0) 会）"
    finally:
        p.kill()
        p.wait()


def test_pid_alive_false_for_dead_and_bogus():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert _pid_alive(p.pid) is False
    assert _pid_alive(0) is False
    assert _pid_alive(-1) is False
    assert _pid_alive(99_999_999) is False


def test_solo_lock_blocks_when_holder_alive(tmp_path, monkeypatch):
    """锁被活进程占用时，第二个进程应抛 Busy 而不是抢锁/杀进程。"""
    cfg = Config()
    monkeypatch.setattr(cfg.paths, "runs_root", str(tmp_path))

    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        lock = tmp_path / "_state" / "run.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(str(p.pid), encoding="utf-8")
        try:
            with solo_lock(cfg):
                raise AssertionError("不该拿到锁")
        except Busy:
            pass
        assert p.poll() is None, "检查和独占锁都不该杀进程"
    finally:
        p.kill()
        p.wait()


def test_solo_lock_reclaims_stale_lock(tmp_path, monkeypatch):
    cfg = Config()
    monkeypatch.setattr(cfg.paths, "runs_root", str(tmp_path))
    lock = tmp_path / "_state" / "run.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("99999999", encoding="utf-8")

    with solo_lock(cfg):
        assert lock.is_file()
        assert lock.read_text(encoding="utf-8") == str(os.getpid())
    assert not lock.is_file(), "退出时应释放锁"
