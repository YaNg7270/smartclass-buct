# Windows x64 单文件构建

使用 64 位 Python 3.12 和 Windows，进入仓库根目录。将 [Gyan FFmpeg 8.1.1 full build](https://www.gyan.dev/ffmpeg/builds/) 的 `bin` 加入构建机 PATH。以下命令只在构建机执行。

```powershell
python -m venv build\portable-env
& .\build\portable-env\Scripts\python -m pip install -e . "pyinstaller==6.22.3" "playwright==1.63.0"
& .\build\portable-env\Scripts\python portable\prepare.py
& .\build\portable-env\Scripts\python -m PyInstaller --noconfirm --workpath build\portable-pyi --distpath dist portable\SmartClass.spec
```

准备脚本下载并校验 Chromium 官方快照，收集浏览器及其他运行库的许可。构建仅打包源码模块、桌面与 Web 资源、媒体工具、浏览器和运行时。产物为 `dist/SmartClass-Windows-x64.exe`。给同学发送程序时复制这个 EXE。获准使用共享 API 的同学还需单独收到相应的学生 JSON 配置，并从桌面首页导入。学生配置可能含有访问密钥，请按获准的使用范围分发。

双击后程序临时解包并打开原生桌面窗口。选择有写入权限的文件夹存放 EXE；课程、操作日志、浏览器登录状态保存在同目录的 `data/`，模型设置存储在同目录的 `smartclass.toml`。浏览器单独打开校园登录页面。移动自己的资料时，退出后一起移动上述文件和目录；移动后需要重新登记曾启用的 Windows 计划任务。首次使用请在原生设置页配置可访问的模型接口。

随附组件的许可及源代码信息见 [第三方组件说明](THIRD-PARTY.md)。
