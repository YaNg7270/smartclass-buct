"""运行控制：经子进程调用 scls CLI，服务器进程自身绝不抓取。

- 复用经过验证的 CLI 路径，规避「Playwright 必须在主线程」
  与「transcribe/scan 不进 gateway 线程池」两条进程内不变量；
- 单活跃任务，进程内存态；日志写 data/_logs/web_<HHMMSS>_<verb>.log；
- 启动前检查 state/*.lock 是否有活进程持有（锁属于子进程，
  这里只判活，不调用 solo_lock）。
"""
from __future__ import annotations

import datetime as _dt
import os
import subprocess
import threading
from pathlib import Path

from ..config import REPO_ROOT, Config
from ..runtime import cli_command, portable_root
from ..state import _pid_alive, log_dir, state_dir


class JobManager:
    """同一时刻只允许一个 scls 子进程任务。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._guard = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._fh = None
        self._verb = ""
        self._args: list[str] = []
        self._log_path: Path | None = None
        self._started_at: str = ""
        self._exit_code: int | None = None

    # ---- 启动 / 取消 ----------------------------------------------------- #

    def start(self, verb: str, cmd_args: list[str]) -> dict:
        with self._guard:
            return self._start(verb, cmd_args)

    def _start(self, verb: str, cmd_args: list[str]) -> dict:
        if self.running():
            return {"ok": False, "error": f"已有任务在运行（{self._verb}），请先等待或取消。",
                    "code": 409}
        busy = busy_locks(self.cfg)
        if busy:
            names = ", ".join(b["name"] for b in busy)
            return {"ok": False,
                    "error": f"数据锁被占用：{names}（另有 scls 进程在跑）。如确认没有，"
                             f"删掉 data/_state/ 下对应 .lock 再试。",
                    "code": 409, "busy_locks": busy}

        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        log_path = log_dir(self.cfg) / f"web_{stamp}_{verb}.log"
        self._fh = open(log_path, "w", encoding="utf-8", buffering=1)
        argv = cli_command(verb, cmd_args, self.cfg)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        if portable_root() is not None:
            # windowed 启动器在部分机器上没有 Python 标准流；子进程显式接管日志。
            env["SCLS_JOB_LOG"] = str(log_path.resolve())
        try:
            self._proc = subprocess.Popen(
                argv, stdout=self._fh, stderr=subprocess.STDOUT, env=env,
                cwd=str(portable_root() or REPO_ROOT),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            self._fh.close()
            self._fh = None
            return {"ok": False, "error": f"启动失败：{exc}", "code": 500}
        self._verb = verb
        self._args = list(cmd_args)
        self._log_path = log_path
        self._started_at = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._exit_code = None
        return {"ok": True, "pid": self._proc.pid}

    def cancel(self) -> dict:
        with self._guard:
            if not self.running():
                return {"ok": False, "error": "当前没有运行中的任务。"}
            try:
                _terminate_task(self._proc)
            except OSError as exc:
                return {"ok": False, "error": f"取消任务失败：{exc}"}
            return {"ok": True}

    # ---- 状态 ------------------------------------------------------------ #

    def running(self) -> bool:
        with self._guard:
            return self._running()

    def _running(self) -> bool:
        if self._proc is None:
            return False
        if self._proc.poll() is None:
            return True
        if self._exit_code is None:
            self._exit_code = self._proc.returncode
            if self._fh:
                self._fh.close()
                self._fh = None
        return False

    def status(self) -> dict:
        with self._guard:
            running = self._running()
            return {
                "running": running,
                "verb": self._verb,
                "args": list(self._args),
                "started_at": self._started_at,
                "exit_code": self._exit_code if not running else None,
                "log_file": self._log_path.name if self._log_path else "",
                "log_tail": self._log_tail(),
            }

    def _log_tail(self, n: int = 400) -> list[str]:
        if not self._log_path or not self._log_path.is_file():
            return []
        try:
            lines = self._log_path.read_text(
                encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        return lines[-n:]


def _terminate_task(proc: subprocess.Popen) -> None:
    if os.name != "nt":
        proc.terminate()
        return
    # 一并关闭本次任务启动的 Chromium、Playwright 驱动和 FFmpeg。
    taskkill = Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"
    result = subprocess.run([str(taskkill), "/pid", str(proc.pid), "/t", "/f"],
                            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode and proc.poll() is None:
        raise OSError(result.stderr.decode("mbcs", errors="replace").strip())


def busy_locks(cfg: Config) -> list[dict]:
    """state_dir/*.lock 中仍被活进程持有的锁（不清理，只报告）。"""
    out = []
    d = state_dir(cfg)
    for p in sorted(d.glob("*.lock")):
        try:
            pid = int(p.read_text(encoding="utf-8").strip() or 0)
        except (ValueError, OSError):
            continue
        if pid and _pid_alive(pid):
            out.append({"name": p.name, "pid": pid, "path": str(p)})
    return out
