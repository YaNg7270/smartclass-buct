# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 单文件。使用明确的资源目录，禁止遍历仓库和用户配置。"""
import shutil
import os
import sys
import zipfile
from pathlib import Path
from importlib.util import find_spec

from PIL import Image
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

root = Path(SPECPATH).parent
source = root / "src" / "smartclass"
browser = root / "build" / "chromium-1681099" / "chrome-win"
notices = root / "build" / "portable-inputs" / "licenses"
if not (browser / "chrome.exe").is_file() or not (notices / "versions.txt").is_file():
    raise SystemExit("请先运行 portable/prepare.py")

assets = [(str(notices), "licenses"),
          (str(source / "web" / "templates"), "smartclass/web/templates"),
          (str(source / "web" / "static"), "smartclass/web/static")]
for path in (source / "desktop").rglob("*"):
    if path.is_file() and path.suffix.lower() in {".svg", ".qss", ".png", ".ttf", ".otf", ".txt", ".md"}:
        assets.append((str(path), str(path.relative_to(root / "src").parent)))
with zipfile.ZipFile(root / "build" / "chromium-1681099.zip") as archive:
    for member in archive.infolist():
        rel = Path(member.filename).relative_to("chrome-win")
        if not member.is_dir() and rel.name != "interactive_ui_tests.exe":
            assets.append((str(browser / rel), str(Path("chromium") / rel.parent)))
media_files = [Path(shutil.which(tool)) for tool in ("ffmpeg", "ffprobe")]
for path in media_files:
    assets.append((str(path), "media"))

# 限定 DLL 搜索来源，避免构建机 PATH 中其他应用的 CRT/OpenSSL 被打入程序。
qt_dir = Path(find_spec("PySide6").origin).parent
python_dir = Path(sys.base_prefix)
windows = Path(os.environ["SystemRoot"])
os.environ["PATH"] = os.pathsep.join(map(str, [
    qt_dir, Path(sys.executable).parent, python_dir, python_dir / "DLLs",
    windows / "System32", windows,
]))
a = Analysis([str(root / "portable" / "launcher.py")], pathex=[str(root / "src")],
             datas=assets, binaries=[], hiddenimports=["smartclass.desktop.app",
             "playwright.sync_api", "flask", "tomlkit", "filelock", "PySide6.QtWidgets", "PySide6.QtSvg",
             "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets"],
             excludes=["tkinter", "IPython", "pytest"], noarchive=False)
# 桌面表单使用 Windows 输入法；不分发未使用的 Qt 虚拟键盘模块。
a.binaries = [entry for entry in a.binaries if "virtualkeyboard" not in entry[0].lower()]
a.datas = [entry for entry in a.datas if "virtualkeyboard" not in entry[0].lower()]
# Python 先加载顶层 CRT；使用 Qt wheel 同附的兼容新版 MSVC 运行库。
crt = {p.name.lower(): p for p in qt_dir.glob("*.dll")
       if p.name.lower().startswith(("vcruntime140", "msvcp140"))}
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() not in crt]
a.binaries += [(path.name, str(path), "BINARY") for path in crt.values()]
approved = [Path(sys.prefix).resolve(), python_dir, windows, browser]
for _, filename, _ in a.binaries:
    path = Path(filename).resolve()
    if path not in media_files and not any(path.is_relative_to(folder) for folder in approved):
        raise SystemExit(f"检测到构建环境以外的 DLL：{path}")
# 在每档 Windows 图标尺寸直接绘制桌面端使用的 SVG。
renderer = QSvgRenderer(str(source / "desktop" / "icons" / "smartclass.svg"))
if not renderer.isValid():
    raise SystemExit("无法读取 SmartClass 应用图标")
frames = []
for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
    canvas = QImage(size, size, QImage.Format.Format_RGBA8888)
    canvas.fill(0)
    with QPainter(canvas) as painter:
        renderer.render(painter)
    frames.append(Image.frombytes("RGBA", (size, size), canvas.constBits()))
icon = Path(workpath) / "smartclass.ico"
frames[-1].save(icon, sizes=[frame.size for frame in frames], append_images=frames[:-1])

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="SmartClass-Windows-x64",
          debug=False, strip=False, upx=False, console=False, icon=str(icon))
