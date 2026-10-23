"""文本渲染：Markdown 与转写/课件文字 → HTML。

只做展示层转义，所有用户数据（课程名、正文）都经过 html.escape。
"""
from __future__ import annotations

import html
import re

from markdown_it import MarkdownIt


def render_md(text: str) -> str:
    """作业要求/课堂笔记的 Markdown → HTML。markdown-it-py 不可用时退化为转义 <pre>。"""
    try:
        return MarkdownIt().render(text or "")
    except Exception:
        return f"<pre>{html.escape(text or '')}</pre>"


_SEC_RE = re.compile(r"^\s*=+\s*【(.+?)】\s*=+\s*$")
_TS_RE = re.compile(r"^\[(\d{1,3}):(\d{2})\]\s?(.*)$")
_BATCH_RE = re.compile(r"^\s*#+\s*BATCH\s+(\d+)\s*\[([^\]]*)\]\s*#+\s*$")


def render_transcript(text: str) -> str:
    """整课转写 → HTML。真实格式（已核实）：

    - `# ` 开头是头注释，跳过；
    - `====...【08:00-08:45】====...` 开新小节标题；
    - `[mm:ss] 正文` 行 → 带锚点的段落（id=t-<mm><ss>，供将来时间轴联动）；
    - 其余行原样段落。
    """
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip() or line.startswith("# "):
            continue
        m = _SEC_RE.match(line)
        if m:
            out.append(f'<h3 class="sec">【{html.escape(m.group(1))}】</h3>')
            continue
        m = _TS_RE.match(line)
        if m:
            mm, ss, body = m.group(1), m.group(2), m.group(3)
            out.append(
                f'<p class="line" id="t-{mm}{ss}">'
                f'<span class="ts">[{html.escape(mm)}:{ss}]</span> {html.escape(body)}</p>'
            )
            continue
        out.append(f"<p>{html.escape(line)}</p>")
    return "\n".join(out)


def render_slides(text: str) -> str:
    """课件文字 → HTML。结构与转写一致，额外处理：

    - `########## BATCH n  [.. .. ..] ##########` → 批次小标题；
    - `NO_ASSIGNMENT` → 弱化灰字。
    """
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip() or line.startswith("# "):
            continue
        m = _BATCH_RE.match(line)
        if m:
            out.append(f'<h4 class="batch">批次 {int(m.group(1))} '
                       f'<span class="batch-range">[{html.escape(m.group(2).strip())}]</span></h4>')
            continue
        m = _SEC_RE.match(line)
        if m:
            out.append(f'<h3 class="sec">【{html.escape(m.group(1))}】</h3>')
            continue
        if line.strip() == "NO_ASSIGNMENT":
            out.append('<p class="no-assignment">NO_ASSIGNMENT</p>')
            continue
        m = _TS_RE.match(line)
        if m:
            mm, ss, body = m.group(1), m.group(2), m.group(3)
            out.append(
                f'<p class="line" id="t-{mm}{ss}">'
                f'<span class="ts">[{html.escape(mm)}:{ss}]</span> {html.escape(body)}</p>'
            )
            continue
        out.append(f"<p>{html.escape(line)}</p>")
    return "\n".join(out)
