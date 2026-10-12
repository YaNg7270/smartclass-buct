"""从点播页取出各机位的 mp4 地址。

优先读 <video><source src>（播放器初始化成功时最准）。

但播放器有时不初始化（页面里 8 个空 <video>，src 始终为空），此时退回
**从页面自身的 VOD 请求反推目录前缀**：
页面一定会去请求 `.../<scheduleId>/1/000.jpg`、`Caption.json`、`srt.js`，
从这些 URL 里取出 `https://buctvodNNN.smartclass.cn:8010/vod4/YYYY/MM/DD/<scheduleId>/1/`，
再拼出 `VGA.mp4` / `Video1.mp4`，最后用 HEAD 验证确实存在。

机位含义：VGA = 课件/屏幕，Video1 = 教室摄像机。
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from urllib.parse import urljoin

from .config import Config
from .download import remote_size

VIDEO_PAGE = "/PlayPages/Video.aspx?NewID={new_id}"
RE_SCHEDULE = re.compile(r"scheduleID=(\d+)")
# 形如 https://buctvod211.smartclass.cn:8010/vod4/2026/10/12/64147779/1/ 的目录前缀
RE_VOD_BASE = re.compile(r"(https://buctvod[^/]+/vod\d+/\d{4}/\d{2}/\d{2}/\d+/\d+/)")

KIND_VGA = "vga"
KIND_VIDEO1 = "video1"
CANDIDATE_FILES = {KIND_VGA: "VGA.mp4", KIND_VIDEO1: "Video1.mp4"}


def _log(msg: str) -> None:
    print(f"[streams] {msg}", file=sys.stderr, flush=True)


@dataclass
class Track:
    kind: str
    url: str

    @property
    def filename(self) -> str:
        return os.path.basename(self.url.split("?")[0]) or f"{self.kind}.mp4"


@dataclass
class StreamInfo:
    new_id: str
    title: str = ""
    schedule_id: str = ""
    tracks: list[Track] = field(default_factory=list)
    source: str = ""  # player | vod_base

    def by_kind(self, kind: str) -> Track | None:
        for t in self.tracks:
            if t.kind == kind:
                return t
        return None


def classify(url: str) -> str:
    name = os.path.basename(url.split("?")[0]).lower()
    if "vga" in name:
        return KIND_VGA
    if name.startswith("video"):
        return KIND_VIDEO1
    return "other"


class VodSniffer:
    """收集页面发往 VOD 主机的 URL，用于反推目录前缀。"""

    def __init__(self) -> None:
        self.bases: list[str] = []

    def attach(self, page) -> None:
        def on_request(req):
            m = RE_VOD_BASE.search(req.url)
            if m and m.group(1) not in self.bases:
                self.bases.append(m.group(1))

        page.on("request", on_request)


def _player_srcs(page) -> tuple[list[str], list[str], str]:
    data = page.evaluate(
        """() => ({
             title: document.title,
             srcs: [...document.querySelectorAll('video, source')]
                     .map(v => v.src || v.getAttribute('src') || '')
                     .filter(Boolean),
             iframes: [...document.querySelectorAll('iframe')].map(f => f.src || '')
           })"""
    )
    srcs = [s for s in dict.fromkeys(data["srcs"])
            if s.startswith("http") and "/PlayPages/" not in s]
    return srcs, data["iframes"], data["title"]


def _from_vod_base(base: str, cookie: str | None) -> list[Track]:
    tracks: list[Track] = []
    for kind, fname in CANDIDATE_FILES.items():
        url = urljoin(base, fname)
        if remote_size(url, cookie):
            tracks.append(Track(kind=kind, url=url))
    return tracks


def fetch_streams(cfg: Config, page, new_id: str, wait_seconds: int = 30,
                  cookie: str | None = None) -> StreamInfo:
    sniffer = VodSniffer()
    sniffer.attach(page)

    url = urljoin(cfg.site.base_url, VIDEO_PAGE.format(new_id=new_id))
    page.goto(url, wait_until="domcontentloaded")

    info = StreamInfo(new_id=new_id)
    waited = 0.0
    while waited < wait_seconds:
        page.wait_for_timeout(1500)
        waited += 1.5
        srcs, iframes, title = _player_srcs(page)
        info.title = title
        for f in iframes:
            m = RE_SCHEDULE.search(f)
            if m:
                info.schedule_id = m.group(1)
                break
        if srcs:
            info.tracks = [Track(kind=classify(s), url=s) for s in srcs]
            info.source = "player"
            _log(f"{new_id}: 播放器取到 {len(info.tracks)} 个机位")
            return info
        # 播放器没起来，但已经嗅到 VOD 目录前缀了，不必再等
        if sniffer.bases and info.schedule_id:
            break

    # 退回：用嗅到的目录前缀拼接
    bases = sniffer.bases
    if bases:
        # 优先挑含本课时 scheduleId 的前缀
        if info.schedule_id:
            bases = sorted(bases, key=lambda b: info.schedule_id not in b)
        for base in bases:
            tracks = _from_vod_base(base, cookie)
            if tracks:
                info.tracks = tracks
                info.source = "vod_base"
                if not info.schedule_id:
                    m = re.search(r"/(\d+)/\d+/$", base)
                    if m:
                        info.schedule_id = m.group(1)
                _log(f"{new_id}: 由 VOD 前缀推出 {len(tracks)} 个机位（{base}）")
                return info

    _log(f"{new_id}: 未取到视频地址（可能没有录播/权限不足）")
    return info
