"""筛选（日期区间 / 时段 / 星期）测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass.discover import Session, filter_sessions   # noqa: E402


def S(nid, date, start, end, course="化工导论"):
    return Session(new_id=nid, course=course, date=date, start=start, end=end,
                   text=f"{course} {date} {start}-{end}")


# 2026-10-12 是周一，2026-10-11 是周日
DATA = [
    S("a" * 16, "2026-10-12", "08:00:00", "08:45:00", "线性代数A"),
    S("b" * 16, "2026-10-12", "08:50:00", "09:35:00", "线性代数A"),
    S("c" * 16, "2026-10-12", "09:45:00", "10:30:00", "化工导论"),
    S("d" * 16, "2026-10-12", "10:40:00", "11:25:00", "化工导论"),
    S("e" * 16, "2026-10-09", "08:00:00", "08:45:00", "物理化学(Ⅰ)"),
]


def test_no_filter_returns_all():
    assert len(filter_sessions(DATA)) == 5


def test_date_range():
    got = filter_sessions(DATA, date_from="2026-10-12", date_to="2026-10-12")
    assert {s.new_id for s in got} == {"a" * 16, "b" * 16, "c" * 16, "d" * 16}
    got = filter_sessions(DATA, date_from="2026-10-08", date_to="2026-10-10")
    assert [s.new_id for s in got] == ["e" * 16]


def test_time_window_keeps_only_morning():
    """时段过滤只看开始时间，**不限制日期**（所以 10-09 那节 08:00 的课也在）。"""
    got = filter_sessions(DATA, time_from="08:00", time_to="10:00")
    assert {s.new_id for s in got} == {"a" * 16, "b" * 16, "c" * 16, "e" * 16}


def test_time_from_only():
    got = filter_sessions(DATA, time_from="09:45")
    assert {s.new_id for s in got} == {"c" * 16, "d" * 16}


def test_weekday_filter():
    """2026-10-12 是周一。"""
    got = filter_sessions(DATA, weekdays={0})
    assert {s.new_id for s in got} == {"a" * 16, "b" * 16, "c" * 16, "d" * 16}
    got = filter_sessions(DATA, weekdays={6})   # 周日
    assert got == []


def test_combined():
    got = filter_sessions(DATA, date_from="2026-10-12", date_to="2026-10-12",
                          time_from="09:00", time_to="12:00", weekdays={0})
    assert {s.new_id for s in got} == {"c" * 16, "d" * 16}
