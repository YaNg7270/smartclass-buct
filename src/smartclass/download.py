"""可续传下载（.part 临时文件 → 完成后改名），已下完则跳过。"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

CHUNK = 1 << 20  # 1 MiB


def _log(msg: str) -> None:
    print(f"[download] {msg}", file=sys.stderr, flush=True)


def human(n: int) -> str:
    f = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if f < 1024 or u == "GB":
            return f"{f:.1f}{u}"
        f /= 1024
    return f"{f:.1f}GB"


def remote_size(url: str, cookie: str | None = None) -> int | None:
    try:
        r = requests.head(url, headers={"Cookie": cookie} if cookie else None,
                          timeout=30, allow_redirects=True)
        if r.status_code == 200 and r.headers.get("Content-Length"):
            return int(r.headers["Content-Length"])
    except Exception as exc:
        _log(f"HEAD 失败：{exc}")
    return None


def download(url: str, dest: Path, cookie: str | None = None, force: bool = False) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")

    total = remote_size(url, cookie)
    if dest.is_file() and not force:
        size = dest.stat().st_size
        if total is None or size == total:
            _log(f"已存在，跳过：{dest.name} ({human(size)})")
            return dest
        _log(f"大小不符（本地 {human(size)} / 远端 {human(total)}），重新下载")

    headers = {}
    if cookie:
        headers["Cookie"] = cookie
    pos = part.stat().st_size if part.is_file() and not force else 0
    if force and part.is_file():
        part.unlink()
        pos = 0
    if pos:
        headers["Range"] = f"bytes={pos}-"
        _log(f"续传 {dest.name} 从 {human(pos)} 开始")

    if total is not None:
        _log(f"开始下载 {dest.name}（{human(total)}）")

    mode = "ab" if pos else "wb"
    with requests.get(url, headers=headers, stream=True, timeout=120) as r:
        r.raise_for_status()
        if pos and r.status_code != 206:
            _log("服务端不支持续传，从头开始")
            pos, mode = 0, "wb"
        done = pos
        last_pct = -1
        with open(part, mode) as fh:
            for chunk in r.iter_content(CHUNK):
                if not chunk:
                    continue
                fh.write(chunk)
                done += len(chunk)
                if total:
                    pct = int(done * 100 / total)
                    if pct >= last_pct + 5:
                        last_pct = pct
                        _log(f"  {dest.name} {pct}% ({human(done)}/{human(total)})")

    part.replace(dest)
    _log(f"完成 {dest.name} ({human(dest.stat().st_size)})")
    return dest
