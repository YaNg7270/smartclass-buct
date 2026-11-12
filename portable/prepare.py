"""准备发行资源。只访问固定构建输入及公开发行源。"""
from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
NOTICES = BUILD / "portable-inputs" / "licenses"
CHROMIUM = BUILD / "chromium-1681099" / "chrome-win"
ARCHIVE = BUILD / "chromium-1681099.zip"
SNAPSHOT = "https://storage.googleapis.com/chromium-browser-snapshots/Win_x64/1681099/chrome-win.zip"
SHA256 = "e4d9f875915ebe3bb6ec5966de1850cfc4e76dcad459bf38b858d2529ca122b6"
MEDIA = {
    "ffmpeg": "09948d4cdd0650da6ff5a87577469f2a218dc2615ae379f8f734d24c49de0f73",
    "ffprobe": "a6618e99bb58869ded3c6f37b53aa1a8d701c3591dbb7b5b317d47369c112be2",
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(url: str, dest: Path) -> None:
    if dest.is_file():
        return
    partial = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=90) as source, partial.open("wb") as output:
        shutil.copyfileobj(source, output)
    partial.replace(dest)


def prepare() -> None:
    NOTICES.mkdir(parents=True, exist_ok=True)
    download(SNAPSHOT, ARCHIVE)
    if sha256(ARCHIVE) != SHA256:
        raise RuntimeError(f"Chromium 校验失败，请移除损坏的 {ARCHIVE} 后重试")
    # 从经过校验的归档重建目录，不携带交互测试套件和构建机资料。
    with zipfile.ZipFile(ARCHIVE) as z:
        for member in z.infolist():
            if Path(member.filename).name != "interactive_ui_tests.exe":
                z.extract(member, CHROMIUM.parent)

    for tool, expected in MEDIA.items():
        location = shutil.which(tool)
        if not location or sha256(Path(location)) != expected:
            raise RuntimeError(f"构建需在 PATH 提供 Gyan 8.1.1 full build 的 {tool}.exe")
    ffmpeg_root = Path(shutil.which("ffmpeg")).parents[1]
    shutil.copy2(ffmpeg_root / "LICENSE", NOTICES / "Gyan-LICENSE.txt")
    shutil.copy2(ffmpeg_root / "README.txt", NOTICES / "Gyan-README.txt")
    shutil.copy2(ROOT / "portable" / "THIRD-PARTY.md", NOTICES / "THIRD-PARTY.md")
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", NOTICES / "CPython-LICENSE.txt")
    download("https://raw.githubusercontent.com/chromium/chromium/"
             "78191f49f2fdc1da7adfa87a43d1bbdd5316bdf2/LICENSE", NOTICES / "Chromium-LICENSE.txt")
    for license_id in ("LGPL-3.0-only", "GPL-3.0-only", "LGPL-2.1-only"):
        download(f"https://raw.githubusercontent.com/spdx/license-list-data/v3.27.0/text/{license_id}.txt",
                 NOTICES / f"{license_id}.txt")
    # 官方 Chromium 快照的 credits 是占位页。使用相同 153.0.8010.0 版本
    # 官方 Chrome for Testing 的完整声明作为补充，只分发文本，不打包该浏览器。
    credits_zip = BUILD / "credits-153.0.8010.0.zip"
    download("https://storage.googleapis.com/chrome-for-testing-public/"
             "153.0.8010.0/win64/chrome-win64.zip", credits_zip)
    if sha256(credits_zip) != "66bc553c89d43c7c44f264dee48d05f42152f3f0f9bafe950d87d7b8eeb588d8":
        raise RuntimeError("浏览器许可归档校验失败")
    credit_root = BUILD / "credits-153.0.8010.0"
    with zipfile.ZipFile(credits_zip) as z:
        z.extractall(credit_root)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(credit_root / "chrome-win64" / "chrome.exe"))
        page = browser.new_page(locale="en-US")
        page.goto("chrome://credits/")
        page.locator("script").evaluate_all("(nodes) => nodes.forEach(el => el.remove())")
        page.add_style_tag(content=".license{display:block !important}.show,#print-link{display:none}")
        html = page.content()
        if len(html) < 10000 or "License" not in html:
            raise RuntimeError("未能从配套浏览器提取第三方声明")
        (NOTICES / "chromium-credits.html").write_text(html, encoding="utf-8")
        browser.close()

    versions = [f"CPython {sys.version}", f"Chromium 153.0.8010.0 / 1681099 / archive SHA256 {SHA256}"]
    for tool, value in MEDIA.items():
        versions.append(f"{tool} 8.1.1 SHA256 {value}")
    for dist in sorted(metadata.distributions(), key=lambda item: item.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        versions.append(f"{name} {dist.version}")
        for item in dist.files or []:
            is_license = any(w in item.name.lower() for w in ("license", "copying", "notice"))
            if is_license and (".dist-info" in item.parts[0] or item.parts[0] == "playwright"):
                dest = NOTICES / "python" / name / item
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dist.locate_file(item), dest)
    (NOTICES / "versions.txt").write_text("\n".join(versions) + "\n", encoding="utf-8")
    print("发行资源已就绪", CHROMIUM, NOTICES)


if __name__ == "__main__":
    prepare()
