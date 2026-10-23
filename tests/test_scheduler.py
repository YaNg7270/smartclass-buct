"""并发调度：结果顺序、并发上限、异常传播。"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass import scheduler            # noqa: E402
from smartclass.config import Config       # noqa: E402


def _cfg(n: int) -> Config:
    c = Config()
    c.concurrency.gateway = n
    return c


def test_gateway_map_preserves_input_order():
    scheduler.configure(_cfg(4))
    try:
        # 后面的任务先完成，但输出必须仍按输入顺序
        out = scheduler.gateway_map(
            lambda x: (time.sleep(0.01 * (6 - x)), x)[1], list(range(6)))
        assert out == list(range(6))
    finally:
        scheduler.shutdown()


def test_gateway_map_respects_concurrency_cap():
    scheduler.configure(_cfg(3))
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def work(i: int) -> int:
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.08)
        with lock:
            state["active"] -= 1
        return i

    try:
        out = scheduler.gateway_map(work, range(9))
    finally:
        scheduler.shutdown()
    assert out == list(range(9))
    assert 2 <= state["peak"] <= 3


def test_gateway_map_waits_for_all_then_raises():
    scheduler.configure(_cfg(3))
    seen: list[int] = []

    def work(i: int) -> int:
        seen.append(i)
        if i == 2:
            raise ValueError("boom")
        time.sleep(0.02)
        return i

    try:
        with pytest.raises(ValueError, match="boom"):
            scheduler.gateway_map(work, range(5))
    finally:
        scheduler.shutdown()
    assert sorted(seen) == list(range(5)), "一个任务失败不应取消其他任务（缓存要尽量补齐）"


def test_parallel_map_uses_independent_pool():
    """parallel_map 不能占用网关池的名额。"""
    scheduler.configure(_cfg(1))
    try:
        out = scheduler.parallel_map(lambda x: x * 2, [1, 2, 3], workers=3)
        assert out == [2, 4, 6]
    finally:
        scheduler.shutdown()


def test_pipeline_overlaps_fetch_and_process(monkeypatch):
    """处理第 N 节时，下载第 N+1 节应该已经在跑（总耗时 ≈ max 而不是 sum）。"""
    import argparse

    from smartclass import cli

    cfg = Config()
    cfg.concurrency.pipeline = True

    def fake_iter(*a, **k):
        for _ in range(3):
            time.sleep(0.2)
            yield ("lec", "dir", {})

    def fake_process(*a, **k):
        time.sleep(0.2)

    monkeypatch.setattr(cli, "_iter_lectures", fake_iter)
    monkeypatch.setattr(cli, "_process_lecture", fake_process)
    monkeypatch.setattr(cli.scheduler, "configure", lambda c: None)

    args = argparse.Namespace(from_dir="", dry_run=False)
    t0 = time.perf_counter()
    rc = cli._run_pipeline(cfg, args, "", "")
    elapsed = time.perf_counter() - t0

    assert rc == 0
    assert elapsed < 1.0, f"串行应约 1.2s，实际 {elapsed:.2f}s（流水线没生效？）"

