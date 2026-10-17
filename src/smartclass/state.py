"""运行状态：互斥锁、登录失效留痕、每日日志。"""
from __future__ import annotations

import datetime as _dt
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from .config import Config, runs_root

STATE_DIR = "_state"
LOG_DIR = "_logs"


def state_dir(cfg: Config) -> Path:
    d = runs_root(cfg) / STATE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_dir(cfg: Config) -> Path:
    d = runs_root(cfg) / LOG_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


class Busy(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    """判断进程是否还活着。**绝不能**在 Windows 上用 os.kill(pid, 0)。

    CPython 在 Windows 上把 os.kill(pid, sig) 实现为 TerminateProcess(handle, sig)，
    所以 os.kill(pid, 0) 不是探活，而是**以退出码 0 杀掉该进程**——
    计划任务一旦读到别人留下的锁，就会把正在跑的抓取进程干掉。
    """
    if pid <= 0:
        return False

    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        WAIT_TIMEOUT = 0x102
        STILL_ACTIVE = 259

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.GetExitCodeProcess.restype = wintypes.BOOL
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]

        handle = k32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if k32.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != STILL_ACTIVE:
                return False
            return k32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            k32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@contextmanager
def solo_lock(cfg: Config, name: str = "run"):
    """同一时刻只允许一个 scls 抓取进程（避免两个进程抢同一个浏览器 profile）。"""
    path = state_dir(cfg) / f"{name}.lock"
    if path.is_file():
        try:
            old = int(path.read_text(encoding="utf-8").strip() or 0)
        except ValueError:
            old = 0
        if old and _pid_alive(old):
            raise Busy(f"已有 scls 在运行（pid {old}），跳过。如确认没有，删掉 {path} 再试。")
        path.unlink(missing_ok=True)
    path.write_text(str(os.getpid()), encoding="utf-8")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


# ---- 登录失效留痕 ------------------------------------------------------- #

def login_needed_path(cfg: Config) -> Path:
    return state_dir(cfg) / "login-needed.txt"


def mark_login_needed(cfg: Config, detail: str = "") -> None:
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    login_needed_path(cfg).write_text(
        f"{ts} 需要重新登录\n{detail}\n请在有头模式下运行：scls login\n", encoding="utf-8")
    _notify("smartclass-buct：需要重新登录", "请运行 scls login 后再试。")


def clear_login_needed(cfg: Config) -> None:
    login_needed_path(cfg).unlink(missing_ok=True)


def login_needed(cfg: Config) -> bool:
    return login_needed_path(cfg).is_file()


def _notify(title: str, body: str) -> None:
    """尽力弹一个 Windows 通知（失败不影响主流程）。"""
    try:
        import subprocess
        ps = ("Add-Type -AssemblyName System.Windows.Forms;"
              "$n=New-Object System.Windows.Forms.NotifyIcon;"
              "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
              f"$n.ShowBalloonTip(10000,'{title}','{body}',0);Start-Sleep -Seconds 11;$n.Dispose()")
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# ---- 日志 --------------------------------------------------------------- #

@contextmanager
def tee_log(cfg: Config, tag: str = "daily"):
    """把 stdout/stderr 同时写到 data/_logs/<日期>_<tag>.log。"""
    path = log_dir(cfg) / f"{_dt.date.today().isoformat()}_{tag}.log"
    fh = open(path, "a", encoding="utf-8")
    fh.write(f"\n{'=' * 20} {_dt.datetime.now():%H:%M:%S} {tag} {'=' * 20}\n")
    fh.flush()
    old_out, old_err = sys.stdout, sys.stderr

    class _Tee:
        def __init__(self, stream):
            self.stream = stream

        def write(self, data):
            self.stream.write(data)
            fh.write(data)
            fh.flush()

        def flush(self):
            self.stream.flush()
            fh.flush()

    sys.stdout = _Tee(old_out)
    sys.stderr = _Tee(old_err)
    try:
        yield path
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        fh.close()
