# 第三方组件与源码

携带版解包目录的 `licenses/` 收录组件的许可文本、版本及出处。窗口打开期间可以复制保存这个目录。

| 组件 | 本发行版的来源 |
| --- | --- |
| CPython 及所附库 | [CPython](https://www.python.org/downloads/source/)，版本见 `versions.txt`。 |
| Qt、PySide6、Shiboken | Qt 公司和各贡献者提供的 LGPLv3 等许可动态库。对应版本见 `versions.txt`。[Qt 源码发行档案](https://download.qt.io/archive/qt/) · [Qt for Python 源码](https://code.qt.io/cgit/pyside/pyside-setup.git/) · [Qt 各模块与第三方许可](https://doc.qt.io/qt-6/licenses-used-in-qt.html)。Qt Multimedia 插件的 FFmpeg 库随 Qt 官方 wheel 分发，可在 [Qt Multimedia 源码](https://code.qt.io/cgit/qt/qtmultimedia.git/) 查询对应版本的 FFmpeg 配置。 |
| FFmpeg、FFprobe 命令行工具 | Gyan.dev 的 8.1.1 full build，以独立进程运行，采用 GPLv3。内附发行方的 `Gyan-README.txt`、`Gyan-LICENSE.txt`，含编译参数和组件清单。[对应 FFmpeg 源码](https://github.com/FFmpeg/FFmpeg/archive/239f2c733d.tar.gz) · [发行方构建和库链接](https://www.gyan.dev/ffmpeg/builds/)。 |
| Chromium | The Chromium Authors；Win_x64 1681099。内附该快照 LICENSE。随附的 `chromium-credits.html` 是相同版本号 153.0.8010.0 的官方 Chrome for Testing 生成的补充声明。[对应源码](https://chromium.googlesource.com/chromium/src/+/78191f49f2fdc1da7adfa87a43d1bbdd5316bdf2) · [对应官方快照](https://storage.googleapis.com/chromium-browser-snapshots/Win_x64/1681099/chrome-win.zip)。 |
| Playwright 与 Node 驱动 | Microsoft 和 Node.js 贡献者；附包内许可与第三方声明。[Playwright Python 源码](https://github.com/microsoft/playwright-python) · [Node.js 源码](https://github.com/nodejs/node)。 |
| Python 依赖、PyInstaller 启动器 | 各自发行包内的许可文本列在 `python/`。 |
| Koboyo Icons | 窗口使用 Koboyo 的手绘 SVG 图标。[官方图标库](https://koboyo.com/icons) · [Koboyo Icons 许可](https://koboyo.com/icons/license)。 |

如需替换 Qt、PySide6、Shiboken 及其插件，在 EXE 旁创建 `libraries/PySide6/` 和/或 `libraries/shiboken6/`，放入自行构建的 ABI 兼容 DLL、PYD 或插件。保持原 wheel 中的相对路径，例如 `libraries/PySide6/Qt6Core.dll`。退出程序后重新启动，携带版会先将这些文件复制到其临时解包目录，再加载 Qt。可为调试自行修改的 LGPL 库进行逆向工程。
