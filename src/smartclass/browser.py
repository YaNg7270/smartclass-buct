"""浏览器层：持久化上下文 + 登录状态检测/引导。"""
from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urljoin

from .config import Config

# 未登录时顶部导航里可见的登录区域
LOGIN_ITEM = "li.login_area"


def _log(msg: str) -> None:
    print(f"[browser] {msg}", file=sys.stderr, flush=True)


@contextmanager
def browser_session(cfg: Config, headed: bool = False):
    """打开持久化浏览器上下文（复用 profile 里的登录态）。

    yield (context, page)
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=cfg.site.profile_dir,
            headless=not headed,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1440, "height": 900},
        )
        try:
            _load_storage_state(ctx, cfg)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_timeout(30000)
            yield ctx, page
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def _load_storage_state(ctx, cfg: Config) -> None:
    """把 storage state 文件里的 cookie + localStorage 注入上下文。

    注意：本站接口的鉴权还依赖 localStorage 里的 platform_token / csrkKeyNew，
    只注入 cookie 是不够的。
    """
    path = cfg.site.storage_state
    if not path:
        return
    p = Path(path)
    if not p.is_file():
        _log(f"storage_state 文件不存在，忽略：{p}")
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        _log(f"读取 storage_state 失败：{exc}")
        return

    cookies = data.get("cookies") or []
    if cookies:
        ctx.add_cookies(cookies)
        _log(f"已注入 {len(cookies)} 个 cookie（来自 {p.name}）")

    for origin in data.get("origins") or []:
        items = origin.get("localStorage") or []
        if not items:
            continue
        try:
            pg = ctx.new_page()
            pg.goto(origin["origin"], wait_until="domcontentloaded")
            pg.evaluate(
                "items => { for (const it of items) localStorage.setItem(it.name, it.value); }",
                items,
            )
            pg.close()
            _log(f"已注入 {len(items)} 项 localStorage（{origin['origin']}）")
        except Exception as exc:
            _log(f"注入 localStorage 失败：{exc}")


def _header_ready(page) -> None:
    """顶部导航由 JS 渲染，等它出来再判断登录态（否则会误判）。"""
    try:
        page.wait_for_selector(LOGIN_ITEM, state="attached", timeout=20000)
    except Exception:
        pass


def _is_logged_out(page) -> bool:
    """未登录时 <li class="login_area"> 可见（display:list-item），已登录时被隐藏。

    找不到该区域（例如已跳到统一身份认证页、或页面还没渲染）时**保守判为未登录**，
    宁可多问一次也不要带着失效会话往下跑。
    """
    try:
        loc = page.locator(LOGIN_ITEM)
        if loc.count() > 0:
            return loc.first.is_visible()
    except Exception:
        pass
    return True


def is_logged_in(page, cfg: Config) -> bool:
    """访问首页，判断顶部导航是否仍显示「登录」。"""
    page.goto(urljoin(cfg.site.base_url, "/Default.aspx"), wait_until="domcontentloaded")
    _header_ready(page)
    try:
        return not _is_logged_out(page)
    except Exception as exc:
        _log(f"登录检测异常：{exc}")
        return False


def ensure_logged_in(page, cfg: Config, headed: bool, interactive: bool = True) -> None:
    """确认登录；未登录时（有头模式下）等待用户手工登录。"""
    if is_logged_in(page, cfg):
        _log("登录态有效")
        return

    if not interactive:
        raise RuntimeError(
            "登录已失效。请先运行 `scls login` 在弹出的浏览器里手工登录。"
        )

    _log("登录已失效，请在弹出的浏览器窗口里手工完成登录（不会代填账号密码）。")
    if not headed:
        raise RuntimeError("需要重新登录，但当前是无头模式，请加 --headed 或运行 `scls login`。")

    _log("等待登录中——期间不会刷新页面，请慢慢操作。")
    host = urljoin(cfg.site.base_url, "/").split("//")[-1].split("/")[0]
    deadline = time.time() + cfg.site.login_timeout
    while time.time() < deadline:
        time.sleep(2)
        try:
            # 只读检测：绝不 page.goto，否则会把用户正在操作的登录页刷掉
            if host in page.url and not _is_logged_out(page):
                _log("检测到已登录，继续。")
                page.wait_for_timeout(1500)
                return
        except Exception:
            pass
    raise TimeoutError(f"等待登录超时（{cfg.site.login_timeout}s）。")


def cookie_header(ctx) -> str:
    """把上下文 cookie 拼成 Cookie 头，供 requests 下载时带上。"""
    return "; ".join(f"{c['name']}={c['value']}" for c in ctx.cookies())
