"""原生桌面入口，命令行任务无需加载图形库。"""
from __future__ import annotations


def cmd_desktop(args) -> int:
    from .app import run_desktop
    return run_desktop(args.config)
