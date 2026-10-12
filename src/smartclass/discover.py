"""发现课时：在「录播课堂」页拦截站点自身的 XHR，解析出当天/指定日期的课时。

站点接口 URL 里带一次性 csrkToken（由页面 JS 生成），直接 fetch 会返回
{"Success":false,"Message":"验证不通过"}，所以这里不直连接口，而是让页面自己发请求、
我们把响应截下来。截不到时退回解析 DOM。
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from urllib.parse import urljoin

from .config import Config

# 列表数据来自这两个接口
CAPTURE_URL_HINTS = ("GetPublicVideos", "GetMyVideoList", "GetVideos")

COURSE_LIST_PATH = "/UserSpace/CourseList.aspx"

RE_DATETIME = re.compile(
    r"(\d{4}-\d{2}-\d{2})[\sT]*(\d{2}:\d{2}:\d{2})\s*-\s*(\d{2}:\d{2}:\d{2})"
)
RE_NEWID = re.compile(r"NewID=([0-9a-fA-F-]{8,})")

# 接口返回里可能出现的字段名（大小写不一，做宽容映射）
ID_KEYS = ("NewID", "NewId", "newId", "newid", "Id", "VideoID", "VideoGuid")
TITLE_KEYS = ("Title", "title", "Name", "VideoName", "ScheduleName")
COURSE_KEYS = ("CourseName", "courseName", "Course", "SubjectName")
TEACHER_KEYS = ("TeacherName", "teacherName", "Teacher", "SpeakerName", "Speaker")
ROOM_KEYS = ("ClassroomName", "ClassRoomName", "Room", "RoomName", "BuildingName")
START_KEYS = ("StartTime", "startTime", "BeginTime", "StartDate")
END_KEYS = ("EndTime", "endTime", "StopTime", "EndDate")

# 星期筛选用：mon/tue/... → datetime.weekday()
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
            "周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6}


def _log(msg: str) -> None:
    print(f"[discover] {msg}", file=sys.stderr, flush=True)


@dataclass
class Session:
    new_id: str
    text: str = ""          # 原始卡片/条目文本（已去多余空白）
    date: str = ""          # YYYY-MM-DD
    start: str = ""         # HH:MM:SS
    end: str = ""
    course: str = ""        # 尽力而为
    raw: dict = field(default_factory=dict)

    @property
    def slot(self) -> str:
        return f"{self.start[:5].replace(':','')}-{self.end[:5].replace(':','')}"


class ApiCapture:
    """挂到 page 上，收集列表接口返回的 JSON。"""

    def __init__(self) -> None:
        self.payloads: list[dict] = []
        self.urls: list[str] = []

    def attach(self, page) -> None:
        page.on("response", self._on_response)

    def _on_response(self, response) -> None:
        try:
            url = response.url
            if not any(h in url for h in CAPTURE_URL_HINTS):
                return
            if response.status != 200:
                return
            data = response.json()
            self.payloads.append(data)
            self.urls.append(url)
        except Exception:
            pass


def _pick(d: dict, keys) -> str:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return str(d[k])
    return ""


def _walk_items(node, out: list[dict]) -> None:
    """递归找出所有「像课时」的字典：既要有 ID，也要有标题或时间，避免误收嵌套的教师/教室对象。"""
    if isinstance(node, dict):
        if _pick(node, ID_KEYS) and (_pick(node, START_KEYS) or _pick(node, TITLE_KEYS)):
            out.append(node)
        for v in node.values():
            _walk_items(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_items(v, out)


def sessions_from_payloads(payloads: list[dict]) -> list[Session]:
    items: list[dict] = []
    for p in payloads:
        _walk_items(p, items)

    seen: dict[str, Session] = {}
    for it in items:
        nid = _pick(it, ID_KEYS)
        if not nid or len(nid) < 8:
            continue
        start = _pick(it, START_KEYS)
        end = _pick(it, END_KEYS)
        date = start[:10] if len(start) >= 10 else ""

        def _hhmmss(v: str) -> str:
            m = re.search(r"(\d{2}:\d{2}:\d{2})", v or "")
            return m.group(1) if m else ""

        title = _pick(it, TITLE_KEYS)
        teacher = _pick(it, TEACHER_KEYS)
        room = _pick(it, ROOM_KEYS)
        slot = f"{_hhmmss(start)}-{_hhmmss(end)}"
        # 有些接口的 Title 本身已经是「课程 教师 教室 日期 时间」，避免重复拼接
        if title and re.search(r"\d{4}-\d{2}-\d{2}", title):
            text = re.sub(r"\s+", " ", title).strip()
        else:
            text = " ".join(x for x in (title, teacher, room, date, slot) if x)

        s = Session(
            new_id=nid, text=text, date=date,
            start=_hhmmss(start), end=_hhmmss(end),
            course=_pick(it, COURSE_KEYS), raw=it,
        )
        seen.setdefault(nid, s)
    return list(seen.values())


def sessions_from_dom(page) -> list[Session]:
    """兜底：解析页面上的课时卡片。"""
    rows = page.evaluate(
        """() => {
          const out = [];
          for (const a of document.querySelectorAll('a[href*="Video.aspx"]')) {
            const href = a.getAttribute('href') || '';
            const m = href.match(/NewID=([0-9a-fA-F-]{8,})/);
            if (!m) continue;
            let box = a.closest('li, .item, .course-item, div');
            let text = '';
            for (let i = 0; i < 4 && box; i++) {
              text = (box.innerText || '').replace(/\\s+/g, ' ').trim();
              if (/\\d{4}-\\d{2}-\\d{2}/.test(text)) break;
              box = box.parentElement;
            }
            out.push({href, newId: m[1], text});
          }
          return out;
        }"""
    )
    seen: dict[str, Session] = {}
    for r in rows:
        nid = r["newId"]
        text = r["text"]
        dt = RE_DATETIME.search(text)
        s = Session(
            new_id=nid,
            text=text,
            date=dt.group(1) if dt else "",
            start=dt.group(2) if dt else "",
            end=dt.group(3) if dt else "",
        )
        seen.setdefault(nid, s)
    return list(seen.values())


def set_date_range(page, start: str, end: str) -> bool:
    """尽力而为地设置日期范围控件；找不到就返回 False。"""
    try:
        filled = page.evaluate(
            """([s, e]) => {
              const cands = [...document.querySelectorAll('input')].filter(i => {
                const p = (i.placeholder || '') + (i.className || '') + (i.id || '');
                return /date|日期|开始|结束|start|end|time/i.test(p) && i.offsetParent !== null;
              });
              if (cands.length === 0) return 0;
              const set = (el, v) => {
                el.focus(); el.value = v;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
              };
              set(cands[0], s);
              if (cands[1]) set(cands[1], e);
              return cands.length;
            }""",
            [start, end],
        )
        return bool(filled)
    except Exception as exc:
        _log(f"设置日期控件失败：{exc}")
        return False


def click_my_related(page) -> bool:
    """切到「与我相关」标签（拿到本人的课程列表）。未登录时该标签是隐藏的。"""
    try:
        el = page.locator('li:has-text("与我相关")').first
        if el.count() and el.is_visible():
            el.click()
            page.wait_for_timeout(4000)
            _log("已切到「与我相关」")
            return True
    except Exception as exc:
        _log(f"切换「与我相关」失败：{exc}")
    return False


def _hhmm(t: str) -> str:
    m = re.search(r"(\d{1,2}):(\d{2})", t or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def filter_sessions(sessions: list[Session], date_from: str = "", date_to: str = "",
                    time_from: str = "", time_to: str = "",
                    weekdays: set[int] | None = None) -> list[Session]:
    """按日期区间 / 时段 / 星期过滤（客户端兜底，不依赖页面控件）。"""
    import datetime as _dt

    out = []
    for s in sessions:
        if date_from and s.date and s.date < date_from:
            continue
        if date_to and s.date and s.date > date_to:
            continue
        if time_from and _hhmm(s.start) and _hhmm(s.start) < time_from:
            continue
        if time_to and _hhmm(s.start) and _hhmm(s.start) > time_to:
            continue
        if weekdays is not None and s.date:
            try:
                if _dt.date.fromisoformat(s.date).weekday() not in weekdays:
                    continue
            except ValueError:
                pass
        out.append(s)
    return out


def discover(cfg: Config, page, date: str = "", course: str = "",
             date_from: str = "", date_to: str = "") -> list[Session]:
    """返回指定日期/日期区间（可选按课程名模糊过滤）的课时列表。"""
    if date:
        date_from = date_to = date
    if not date_from and not date_to:
        import datetime as _dt
        date_from = date_to = _dt.date.today().isoformat()

    cap = ApiCapture()
    cap.attach(page)

    url = urljoin(cfg.site.base_url, COURSE_LIST_PATH)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)

    sessions = sessions_from_payloads(cap.payloads)
    if cap.payloads:
        _log(f"拦截到 {len(cap.payloads)} 个列表接口响应，解析出 {len(sessions)} 个课时")

    def _covers(items) -> bool:
        return any(date_from <= (s.date or "") <= date_to for s in items)

    # 默认可能是「公开视频」，多半是空的；切到「与我相关」再收一次
    if not sessions or not _covers(sessions):
        cap.payloads.clear()
        if click_my_related(page):
            sessions = sessions_from_payloads(cap.payloads) or sessions_from_dom(page)

    if not sessions:
        _log("未截到接口数据，退回解析 DOM")
        sessions = sessions_from_dom(page)

    if not _covers(sessions):
        _log(f"当前列表不含 {date_from}~{date_to}，尝试设置日期范围后重取")
        cap.payloads.clear()
        if set_date_range(page, date_from, date_to):
            page.wait_for_timeout(4000)
            more = sessions_from_payloads(cap.payloads) or sessions_from_dom(page)
            if more:
                sessions = more
        if not _covers(sessions):
            _log("警告：页面日期控件未生效，仅在已拉取到的范围内过滤")

    sessions = filter_sessions(sessions, date_from, date_to)
    if course:
        sessions = [s for s in sessions if course in (s.text + s.course)]

    sessions.sort(key=lambda s: (s.date, s.start))
    return sessions
