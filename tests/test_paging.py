"""翻页 / 日期区间 的回归测试。

背景：平台列表接口 `GetMyVideoList` 默认 `PageSize=12&PageNumber=1`，
`scls` 过去只截到这一页，于是 09-01~10-16 这个区间只看到 12 个小节、
数出来 15 节里的大部分被漏掉（真实 TotalCount 是 115）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass.discover import (  # noqa: E402
    LIST_PAGE_SIZE,
    ListPager,
    _data_list,
    _endpoint_name,
    _total_count,
    _with_params,
)

BASE = ("https://buct.smartclass.cn/Webapi/V1/Video/GetMyVideoList"
        "?csrkToken=013kk&Sort=StartTime&StartDate=2026-10-10&EndDate=2026-10-17"
        "&Order=1&PageSize=12&PageNumber=1")


def _page(total: int, items_per_page: int = LIST_PAGE_SIZE):
    """假页面：按 PageNumber 返回分页结果，模拟服务端。"""
    calls: list[str] = []

    class FakePage:
        def evaluate(self, _js, url):
            calls.append(url)
            page_no = int(url.split("PageNumber=")[1].split("&")[0])
            start = (page_no - 1) * items_per_page
            n = max(0, min(items_per_page, total - start))
            return {
                "Value": {"TotalCount": total,
                          "Data": [{"NewID": f"{start + i:032x}"} for i in range(n)]},
                "Success": True,
            }

    return FakePage(), calls


def test_with_params_keeps_others_and_replaces():
    u = _with_params(BASE, PageNumber=3, PageSize=LIST_PAGE_SIZE, StartDate="2026-09-01")
    assert "PageNumber=3" in u
    assert "PageSize=50" in u
    assert "StartDate=2026-09-01" in u
    assert "EndDate=2026-10-17" in u          # 未被改动
    assert "csrkToken=013kk" in u             # token 保留
    assert u.count("PageNumber=") == 1


def test_endpoint_name_and_total_count():
    assert _endpoint_name(BASE) == "GetMyVideoList"
    assert _total_count({"Value": {"TotalCount": 115, "Data": []}}) == 115
    assert _total_count({"data": {"total": 7, "records": []}}) == 7
    assert _total_count({"Value": {"Data": []}}) == 0
    assert _data_list({"Value": {"Data": [1, 2]}}) == [1, 2]
    assert _data_list({"data": {"records": [3]}}) == [3]


def test_collect_walks_all_pages():
    """115 条 → 3 页；必须全部拿回，不能停在第一页。"""
    page, calls = _page(115)
    got = ListPager(page).collect([(BASE, {})], "2026-09-01", "2026-10-16")
    assert len(got) == 115
    assert len(calls) == 3
    assert "StartDate=2026-09-01" in calls[0] and "EndDate=2026-10-16" in calls[0]
    assert [c.split("PageNumber=")[1].split("&")[0] for c in calls] == ["1", "2", "3"]


def test_collect_stops_when_total_reached():
    page, calls = _page(50)
    got = ListPager(page).collect([(BASE, {})], "2026-09-01", "2026-10-16")
    assert len(got) == 50
    assert len(calls) == 1


def test_collect_handles_empty_and_failed():
    class Dead:
        def evaluate(self, _js, _url):
            return None

    assert ListPager(Dead()).collect([(BASE, {})], "2026-09-01", "2026-10-16") == []


def test_collect_does_not_query_same_endpoint_twice():
    page, calls = _page(10)
    payloads = [(BASE, {}), (_with_params(BASE, PageNumber=2), {})]
    got = ListPager(page).collect(payloads, "2026-09-01", "2026-10-16")
    assert len(got) == 10
    assert len(calls) == 1


def test_discover_rejects_date_plus_range():
    """--date 和 --from/--to 同时给过，曾经导致区间被静默缩成一天。"""
    from smartclass.discover import discover

    with pytest.raises(ValueError):
        discover(None, None, date="2026-10-09",
                 date_from="2026-09-01", date_to="2026-10-16")
