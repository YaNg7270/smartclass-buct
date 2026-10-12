"""用 Windows 计划任务实现「每天晚上自动跑一遍」。"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .config import Config, REPO_ROOT
from .state import state_dir

WRAPPER_NAME = "run-daily.cmd"


def _log(msg: str) -> None:
    print(f"[schedule] {msg}", file=sys.stderr, flush=True)


def scls_exe() -> str:
    """解析 scls 的绝对路径——计划任务里的 PATH 和交互式终端不一样。"""
    exe = shutil.which("scls")
    if exe:
        return exe
    cand = Path(sys.executable).parent / "Scripts" / "scls.exe"
    if cand.is_file():
        return str(cand)
    raise RuntimeError("找不到 scls 可执行文件，请先 `python -m pip install -e .`")


def write_wrapper(cfg: Config) -> Path:
    """生成一个 .cmd 包装脚本：切到仓库根目录、日志重定向。"""
    wrapper = state_dir(cfg) / WRAPPER_NAME
    log = state_dir(cfg).parent / "_logs" / "schtasks.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(
        "@echo off\r\n"
        f'cd /d "{REPO_ROOT}"\r\n'
        f'"{scls_exe()}" daily >> "{log}" 2>&1\r\n'
        "exit /b %ERRORLEVEL%\r\n",
        encoding="gbk",
    )
    return wrapper


def install(cfg: Config, at: str | None = None, task_name: str | None = None) -> str:
    at = at or cfg.schedule.time
    task_name = task_name or cfg.schedule.task_name
    wrapper = write_wrapper(cfg)

    r = subprocess.run(
        ["schtasks", "/Create", "/TN", task_name, "/SC", "DAILY", "/ST", at,
         "/TR", f'"{wrapper}"', "/F"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"创建计划任务失败：{(r.stderr or r.stdout).strip()}")
    _log(f"已创建计划任务「{task_name}」，每天 {at} 执行 {wrapper}")
    _log("注意：计划任务只在已登录的桌面会话里触发，睡眠/关机时不跑。")
    return task_name


def remove(cfg: Config, task_name: str | None = None) -> None:
    task_name = task_name or cfg.schedule.task_name
    r = subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"删除失败：{(r.stderr or r.stdout).strip()}")
    _log(f"已删除计划任务「{task_name}」")


def show(cfg: Config, task_name: str | None = None) -> str:
    task_name = task_name or cfg.schedule.task_name
    r = subprocess.run(["schtasks", "/Query", "/TN", task_name, "/V", "/FO", "LIST"],
                       capture_output=True, text=True)
    return (r.stdout or r.stderr).strip()
