"""scls 本机 Web 界面（Flask + Jinja2 服务端渲染，无 Node 构建链）。"""
from __future__ import annotations

from .server import create_app

__all__ = ["create_app"]
