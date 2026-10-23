"""进程内并发调度：一个有界网关线程池 + 通用并行 map。

为什么要单独一个模块：

- ASR 的每个音频块、OCR 的每个图片批、归纳的每次请求，都是「等 HTTP」型任务。
  串行做会把 wall time 拉满，而 CPU/网络其实都闲着。
- 但又不能无限并发——本地网关和上游都会限流，所以统一走一个有界池。
- **网关池只跑单个 HTTP 请求**：不要把 `transcribe()` / `scan()` 这种内部还会
  再提交任务的函数丢进来，否则池被占满时会自锁。
- 结果一律按输入顺序返回，调用方可以放心按 index 回填/落盘。
"""
from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")
R = TypeVar("R")

_pool: ThreadPoolExecutor | None = None
_size = 0
_lock = threading.Lock()


def _log(msg: str) -> None:
    print(f"[pool] {msg}", file=sys.stderr, flush=True)


def configure(cfg) -> int:
    """按 `[concurrency] gateway` 初始化全局网关池，返回实际并发数。"""
    global _pool, _size
    n = max(1, int(getattr(cfg.concurrency, "gateway", 1) or 1))
    with _lock:
        if _pool is not None and _size == n:
            return n
        if _pool is not None:
            _pool.shutdown(wait=False)
        _pool = ThreadPoolExecutor(max_workers=n, thread_name_prefix="gw")
        _size = n
    if n > 1:
        _log(f"网关并发 = {n}")
    return n


def _get_pool() -> ThreadPoolExecutor:
    global _pool, _size
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gw")
            _size = 1
        return _pool


def shutdown() -> None:
    """测试或进程退出时释放线程池。"""
    global _pool, _size
    with _lock:
        if _pool is not None:
            _pool.shutdown(wait=False)
        _pool = None
        _size = 0


def _run_all(fn: Callable[[T], R], items: list[T], pool: ThreadPoolExecutor,
             label: str = "") -> list[R]:
    if not items:
        return []
    futures = {pool.submit(fn, it): i for i, it in enumerate(items)}
    out: list[R | None] = [None] * len(items)
    errors: list[tuple[int, BaseException]] = []
    done = 0
    for fut in as_completed(futures):
        i = futures[fut]
        try:
            out[i] = fut.result()
        except Exception as exc:  # 单个任务失败不打断其他任务，最后统一抛
            errors.append((i, exc))
        done += 1
        if label:
            _log(f"{label} [{done}/{len(items)}]")
    if errors:
        first = errors[0][1]
        if len(errors) == 1:
            raise first
        raise RuntimeError(f"{len(errors)} 个任务失败，首个：{first}") from first
    return out  # type: ignore[return-value]


def gateway_map(fn: Callable[[T], R], items: Iterable[T], label: str = "") -> list[R]:
    """把一批「发往网关」的任务并发跑完，按输入顺序返回结果。"""
    return _run_all(fn, list(items), _get_pool(), label)


def parallel_map(fn: Callable[[T], R], items: Iterable[T], workers: int = 2,
                 label: str = "") -> list[R]:
    """通用并行 map（下载 / 压缩等），用独立的临时线程池。"""
    items = list(items)
    if not items:
        return []
    workers = max(1, min(int(workers or 1), len(items)))
    # 始终用独立池：不能占用网关池的名额（压缩/下载可能和转写同时进行）
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="par") as pool:
        return _run_all(fn, items, pool, label)
