"""Windows 无控制台版入口；无参数进入桌面，有参数用于处理任务。"""
from __future__ import annotations

import ctypes
import os
import shutil
import sys
import traceback
from pathlib import Path

from smartclass.runtime import bundled_file, portable_root


def _connect_output(root: Path) -> None:
    # 可从 GUI 进程传入日志；命令行重定向到文件时保留调用者的句柄。
    dest = os.environ.get("SCLS_JOB_LOG")
    if dest:
        sys.stdout = sys.stderr = open(dest, "a", encoding="utf-8", buffering=1)
    elif sys.stdout is None or sys.stderr is None:
        logs = root / "data" / "_logs"
        logs.mkdir(parents=True, exist_ok=True)
        stream = (logs / "desktop.log").open("a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream


def _use_replacement_libraries(root: Path) -> None:
    # 可选 LGPL 动态库替换：在首次导入 Qt 之前合并用户提供的兼容库。
    for package in ("PySide6", "shiboken6"):
        override = root / "libraries" / package
        if override.is_dir():
            shutil.copytree(override, bundled_file(package), dirs_exist_ok=True)


def launch() -> int:
    root = portable_root()
    try:
        _connect_output(root)
        if not sys.argv[1:] or sys.argv[1] == "desktop":
            _use_replacement_libraries(root)
        if not sys.argv[1:]:
            from smartclass.desktop.app import run_desktop
            return run_desktop()
        from smartclass.cli import main
        return main()
    except Exception as exc:
        if sys.stderr:
            traceback.print_exc()
        if not sys.argv[1:]:
            ctypes.windll.user32.MessageBoxW(
                None, f"启动失败。请将程序放在可写目录。\n\n{exc}", "SmartClass", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(launch())
