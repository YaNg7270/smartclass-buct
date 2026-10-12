"""把同一天、同一门课的相邻小节合并成「整课」。

平台上一次课常被切成 2~3 个 45 分钟的小节（如 08:00-08:45 + 08:50-09:35），
小节之间只隔几分钟课间。这里按"相邻小节间隔 ≤ max_gap 分钟"把它们并成一节整课，
整课才是对外交付的单位（转写、作业要求、笔记都按整课出）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .discover import Session

DEFAULT_MAX_GAP_MINUTES = 30

RE_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
RE_TIMERANGE = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?\s*[-~—]\s*\d{1,2}:\d{2}(?::\d{2})?")


def course_key(text: str) -> str:
    """从课时标题里剥掉日期与时间，得到稳定的课程键（用于分组/去重）。

    接口一般会直接给 CourseName；没有时用这个兜底：
    「化工导论 方云明 A阶-304 2026-10-12 09:45:00-10:30:00」→「化工导论 方云明 A阶-304」
    """
    s = RE_DATE.sub(" ", text or "")
    s = RE_TIMERANGE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _hm(t: str) -> int:
    """'HH:MM:SS' → 当天的分钟数。"""
    parts = [int(x) for x in re.findall(r"\d+", t or "")[:2]] or [0]
    h = parts[0] if parts else 0
    m = parts[1] if len(parts) > 1 else 0
    return h * 60 + m


def _mm(v: int) -> str:
    return f"{v // 60:02d}{v % 60:02d}"


@dataclass
class Lecture:
    """一节整课 = 同课程同日期、彼此相邻的若干小节。"""

    course: str
    date: str
    parts: list[Session] = field(default_factory=list)

    @property
    def start_min(self) -> int:
        return min(_hm(p.start) for p in self.parts)

    @property
    def end_min(self) -> int:
        return max(_hm(p.end) for p in self.parts)

    @property
    def start(self) -> str:
        return f"{self.start_min // 60:02d}:{self.start_min % 60:02d}"

    @property
    def end(self) -> str:
        return f"{self.end_min // 60:02d}:{self.end_min % 60:02d}"

    @property
    def span(self) -> str:
        """时段区间命名，如 0945-1125（作目录名）。"""
        return f"{_mm(self.start_min)}-{_mm(self.end_min)}"

    @property
    def label(self) -> str:
        if len(self.parts) == 1:
            return f"{self.start}-{self.end}"
        return f"{self.start}-{self.end}（{len(self.parts)} 小节）"

    def sorted_parts(self) -> list[Session]:
        return sorted(self.parts, key=lambda p: _hm(p.start))


def group_sessions(sessions: list[Session],
                   max_gap_minutes: int = DEFAULT_MAX_GAP_MINUTES) -> list[Lecture]:
    """把课时按「同课程同日期 + 相邻间隔 ≤ max_gap」聚成整课。

    只在同一 (课程, 日期) 内分组，不同课程/不同天永不合并。
    """
    buckets: dict[tuple[str, str], list[Session]] = {}
    for s in sessions:
        key = (s.course or course_key(s.text), s.date)
        buckets.setdefault(key, []).append(s)

    lectures: list[Lecture] = []
    for (course, date), items in buckets.items():
        items = sorted(items, key=lambda p: _hm(p.start))
        cur: list[Session] = []
        for s in items:
            if cur and _hm(s.start) - _hm(cur[-1].end) > max_gap_minutes:
                lectures.append(Lecture(course=course, date=date, parts=cur))
                cur = []
            cur.append(s)
        if cur:
            lectures.append(Lecture(course=course, date=date, parts=cur))

    lectures.sort(key=lambda L: (L.date, L.start_min, L.course))
    return lectures
