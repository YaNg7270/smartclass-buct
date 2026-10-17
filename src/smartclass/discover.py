"""发现课时：在「录播课堂」页拦截站点自身的 XHR，解析出当天/指定日期的课时。

站点接口 URL 里带一次性 csrkToken（由页面 JS 生成），直接 fetch 会返回
{"Success":false,"Message":"验证不通过"}，所以这里不直连接口，而是让页面自己发请求、
我们把响应截下来。截不到时退回解析 DOM。
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from .config import Config

# 列表数据来自这几个接口
CAPTURE_URL_HINTS = ("GetPublicVideos", "GetMyVideoList", "GetVideos")

COURSE_LIST_PATH = "/UserSpace/CourseList.aspx"

# 服务端对 PageSize 有上限（实测要 200 只给 50），按 50 翻页即可
LIST_PAGE_SIZE = 50
LIST_MAX_PAGES = 80

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

# 课程名容错：全角括号、罗马数字（Ⅰ/Ⅱ…）、空格、大小写
_FOLD = {"（": "(", "）": ")", "【": "[", "】": "]", "　": ""}
for _i, _r in enumerate(["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii"]):
    _FOLD[chr(0x2160 + _i)] = _r   # Ⅰ Ⅱ Ⅲ …
    _FOLD[chr(0x2170 + _i)] = _r   # ⅰ ⅱ ⅲ …


def normalize_key(s: str) -> str:
    """把课程名折叠成可比较的键。

    让 `物理化学(Ⅰ)` / `物理化学(I)` / `物理化学（Ⅰ）` / `物理化学` 都能对上，
    省得用户为了一个罗马数字反复试。
    """
    s = (s or "").translate(str.maketrans(_FOLD))
    return re.sub(r"\s+", "", s).lower()


def course_matches(query: str, *candidates: str) -> bool:
    """课程名模糊匹配（先按折叠后的键，再退回原文包含）。"""
    if not query:
        return True
    q = normalize_key(query)
    for c in candidates:
        if not c:
            continue
        if q in normalize_key(c) or query in c:
            return True
    return False


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
        self.pairs: list[tuple[str, dict]] = []   # (url, payload)，保留 URL 以便按接口取 TotalCount

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
            self.pairs.append((url, data))
        except Exception:
            pass

    def clear(self) -> None:
        self.payloads.clear()
        self.urls.clear()
        self.pairs.clear()

    def totals(self) -> dict[str, tuple[int, int]]:
        """接口 → (TotalCount, 已收条数)，用于判断是否还需要翻页。"""
        out: dict[str, tuple[int, int]] = {}
        for url, data in self.pairs:
            name = _endpoint_name(url)
            total = _total_count(data)
            n = len(_data_list(data))
            prev_total, prev_n = out.get(name, (0, 0))
            out[name] = (max(total, prev_total), prev_n + n)
        return out


def _endpoint_name(url: str) -> str:
    """从接口 URL 里取出接口名（去掉 query）。"""
    return urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]


def _total_count(payload) -> int:
    v = payload.get("Value") if isinstance(payload, dict) else None
    if isinstance(v, dict):
        for k in ("TotalCount", "total", "Total"):
            if isinstance(v.get(k), int):
                return v[k]
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        t = payload["data"].get("total")
        if isinstance(t, int):
            return t
    return 0


def _data_list(payload) -> list:
    if not isinstance(payload, dict):
        return []
    v = payload.get("Value")
    if isinstance(v, dict) and isinstance(v.get("Data"), list):
        return v["Data"]
    if isinstance(v, list):
        return v
    if isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("records"), list):
        return payload["data"]["records"]
    return []


def _with_params(url: str, **updates) -> str:
    """改写 URL 的 query 参数（保持其余参数不变）。"""
    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    for k, v in updates.items():
        q[k] = str(v)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


class ListPager:
    """在页面上下文里复用页面自己的 csrkToken 翻页，把列表拉全。

    关键事实（实测）：
      · Base:             `StartDate=YYYY-MM-DD&EndDate=YYYY-MM-DD`，两端都含。
      · `PageNumber` 翻页、`PageSize` 有服务端上限（要 200 只回 50）。
      · URL 里那次性 `csrkToken` 在同一个页面会话内**可以复用**，所以用
        `page.evaluate(fetch)` 就能翻页，不必去点页面上那套 easyui 控件
        （它的 pageToolbar 是空壳，之前 set_date_range 填的是无关输入框）。
    """

    def __init__(self, page) -> None:
        self.page = page

    def fetch(self, url: str) -> dict | None:
        try:
            return self.page.evaluate(
                """async (u) => {
                    const r = await fetch(u, {credentials: 'include'});
                    const t = await r.text();
                    try { return JSON.parse(t); } catch (e) { return null; }
                }""",
                url,
            )
        except Exception as exc:
            _log(f"翻页请求失败：{exc}")
            return None

    def collect(self, payloads: list[tuple[str, dict]],
                date_from: str, date_to: str) -> list[dict]:
        """对每个列表接口，按日期区间 + 翻页拉到 TotalCount 条为止。"""
        out: list[dict] = []
        done: set[str] = set()
        for url, _payload in payloads:
            name = _endpoint_name(url)
            if name in done:
                continue
            done.add(name)
            out.extend(self._collect_one(url, date_from, date_to))
        return out

    def _collect_one(self, url: str, date_from: str, date_to: str) -> list[dict]:
        base = _with_params(url, StartDate=date_from, EndDate=date_to,
                            PageNumber=1, PageSize=LIST_PAGE_SIZE)
        got: list[dict] = []
        total = 0
        for page_no in range(1, LIST_MAX_PAGES + 1):
            u = _with_params(base, PageNumber=page_no)
            payload = self.fetch(u)
            if not payload:
                if page_no == 1:
                    _log("翻页接口不可用，退回只解析页面已加载的那一页")
                    return []
                break
            total = _total_count(payload) or total
            items = _data_list(payload)
            if not items:
                break
            got.extend(it for it in items if isinstance(it, dict))
            if total and len(got) >= total:
                break
            if not total and len(items) < LIST_PAGE_SIZE:
                break
        _log(f"翻页完成：{_endpoint_name(url)} 共 {total or len(got)} 条，实收 {len(got)} 条"
             f"（{date_from}~{date_to}）")
        return got


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
    """返回指定日期/日期区间（可选按课程名模糊过滤）的课时列表。

    流程：打开录播课堂页 → 让页面自己发一次列表请求拿到 csrkToken →
    在页面上下文里**按日期区间 + 翻页**把列表拉全 → 解析成 Session。
    """
    if date:
        if date_from or date_to:
            # 静默让 date 覆盖区间会让人以为"筛了整段"，其实只查了一天
            raise ValueError(
                f"date={date} 与 date_from/date_to 冲突；区间查询不要再传 date")
        date_from = date_to = date
    if not date_from and not date_to:
        import datetime as _dt
        date_from = date_to = _dt.date.today().isoformat()
    if not date_from:
        date_from = date_to
    if not date_to:
        date_to = date_from

    cap = ApiCapture()
    cap.attach(page)

    url = urljoin(cfg.site.base_url, COURSE_LIST_PATH)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)

    sessions = sessions_from_payloads(cap.payloads)
    if cap.pairs:
        _log(f"拦截到 {len(cap.pairs)} 个列表接口响应；页面首屏只有 {len(sessions)} 条，开始翻页")

    def _covers(items) -> bool:
        return any(date_from <= (s.date or "") <= date_to for s in items)

    # 默认页签可能是「公开视频」（全站公开课），必须先切到「与我相关」才拿到自己的课。
    # 已经截到 GetMyVideoList 就不用再切，省一次点击和 4 秒等待。
    need_mine = not any("GetMyVideoList" in _endpoint_name(u) for u in cap.urls)
    if need_mine:
        cap.clear()
        if click_my_related(page):
            sessions = sessions_from_payloads(cap.payloads) or sessions_from_dom(page)

    if not cap.pairs:
        _log("未截到接口数据，退回解析 DOM")
        sessions = sessions_from_dom(page)

    # ★ 核心修复：页面只加载第一页（PageSize=12），不翻页就会漏课。
    if cap.pairs:
        extra = ListPager(page).collect(cap.pairs, date_from, date_to)
        if extra:
            full = sessions_from_payloads(extra)
            if full:
                sessions = full
        if not _covers(sessions):
            _log(f"警告：翻页后仍未取到 {date_from}~{date_to} 的课时，"
                 f"结果可能不全（请检查日期区间或登录态）")
    elif not _covers(sessions):
        # 没有接口可复用（例如改版），退回填写页面日期控件
        _log(f"当前列表不含 {date_from}~{date_to}，尝试设置日期范围后重取")
        cap.clear()
        if set_date_range(page, date_from, date_to):
            page.wait_for_timeout(4000)
            more = sessions_from_payloads(cap.payloads) or sessions_from_dom(page)
            if more:
                sessions = more

    sessions = filter_sessions(sessions, date_from, date_to)
    if course:
        sessions = [s for s in sessions if course_matches(course, s.text, s.course)]

    sessions.sort(key=lambda s: (s.date, s.start))
    return sessions
