"""整课分组测试（用今天的真实课时构造）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass.discover import Session                     # noqa: E402
from smartclass.grouping import course_key, group_sessions   # noqa: E402


def S(nid, course, date, start, end, text=""):
    return Session(new_id=nid, course=course, date=date, start=start, end=end,
                   text=text or f"{course} {date} {start}-{end}")


def test_merge_two_adjacent_parts():
    """线性代数A 08:00-08:45 + 08:50-09:35（间隔 5 分钟）应并成一节整课。"""
    sessions = [
        S("a" * 16, "线性代数A", "2026-10-12", "08:00:00", "08:45:00"),
        S("b" * 16, "线性代数A", "2026-10-12", "08:50:00", "09:35:00"),
    ]
    lecs = group_sessions(sessions)
    assert len(lecs) == 1
    assert lecs[0].span == "0800-0935"
    assert lecs[0].start == "08:00" and lecs[0].end == "09:35"
    assert len(lecs[0].parts) == 2


def test_merge_with_ten_minute_gap():
    """化工导论 09:45-10:30 + 10:40-11:25（间隔 10 分钟）应并成一节整课。"""
    sessions = [
        S("a" * 16, "化工导论", "2026-10-12", "09:45:00", "10:30:00"),
        S("b" * 16, "化工导论", "2026-10-12", "10:40:00", "11:25:00"),
    ]
    lecs = group_sessions(sessions)
    assert len(lecs) == 1 and lecs[0].span == "0945-1125"


def test_three_parts_merge():
    sessions = [
        S("a" * 16, "物理化学(Ⅰ)", "2026-10-09", "08:00:00", "08:45:00"),
        S("b" * 16, "物理化学(Ⅰ)", "2026-10-09", "08:50:00", "09:35:00"),
        S("c" * 16, "物理化学(Ⅰ)", "2026-10-09", "09:45:00", "10:30:00"),
    ]
    lecs = group_sessions(sessions)
    assert len(lecs) == 1 and lecs[0].span == "0800-1030"


def test_different_courses_not_merged():
    """同一时段的线性代数A 与化工导论不能并到一起。"""
    sessions = [
        S("a" * 16, "线性代数A", "2026-10-12", "08:00:00", "08:45:00"),
        S("b" * 16, "化工导论", "2026-10-12", "08:50:00", "09:35:00"),
    ]
    assert len(group_sessions(sessions)) == 2


def test_big_gap_splits():
    """间隔超过阈值（默认 30 分钟）应拆成两节。"""
    sessions = [
        S("a" * 16, "化工导论", "2026-10-12", "08:00:00", "08:45:00"),
        S("b" * 16, "化工导论", "2026-10-12", "10:00:00", "10:45:00"),
    ]
    assert len(group_sessions(sessions)) == 2
    assert len(group_sessions(sessions, max_gap_minutes=90)) == 1


def test_different_days_not_merged():
    sessions = [
        S("a" * 16, "化工导论", "2026-10-12", "09:45:00", "10:30:00"),
        S("b" * 16, "化工导论", "2026-10-11", "10:40:00", "11:25:00"),
    ]
    assert len(group_sessions(sessions)) == 2


def test_course_key_strips_time():
    assert course_key("化工导论 方云明 A阶-304 2026-10-12 09:45:00-10:30:00") == \
        "化工导论 方云明 A阶-304"
    assert course_key("线性代数A 李晓翠 B阶-103 2026-10-12 08:00:00-08:45:00") == \
        course_key("线性代数A 李晓翠 B阶-103 2026-10-12 08:50:00-09:35:00")


def test_grouping_uses_text_when_course_empty():
    """接口没给 CourseName 时，用标题剥掉时间后的键来分组。"""
    sessions = [
        Session(new_id="a" * 16, course="", date="2026-10-12", start="09:45:00", end="10:30:00",
                text="化工导论 方云明 A阶-304 2026-10-12 09:45:00-10:30:00"),
        Session(new_id="b" * 16, course="", date="2026-10-12", start="10:40:00", end="11:25:00",
                text="化工导论 方云明 A阶-304 2026-10-12 10:40:00-11:25:00"),
    ]
    lecs = group_sessions(sessions)
    assert len(lecs) == 1 and lecs[0].span == "0945-1125"
