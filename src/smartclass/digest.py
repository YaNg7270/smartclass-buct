"""把转写 + 课件 OCR 归纳成「作业要求汇总」与「课堂笔记」。

原则：只依据给定材料，不许编造；结论尽量附原文引用；材料没写到的标注「材料未提及」。
同时保留规则命中的原文行作为「证据区」，方便人工核对（模型可能漏读或串味）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

from .config import Config
from .workspace import RunDir

KEYWORDS = [
    "作业", "要求", "提交", "截止", "上交", "报告", "字数", "格式", "评分", "打分",
    "分组", "小组", "读后感", "论文", "选题", "教材", "目录", "借阅", "阅读", "参考文献",
    "课程任务", "课内", "下次", "下周", "期末", "考试", "占比", "挂科",
]

HOMEWORK_TEMPLATE = """你是一位严谨的助教。下面是某节中国大学课堂的**逐字转写**和**课件文字**。
请据此整理出这节课布置的**作业要求**，严格按下面的小节输出 Markdown：

## 1. 作业形式与硬性要求
## 2. 选题范围
## 3. 写作/完成框架
## 4. 附带任务（如读书、预习）
## 5. 老师会另行下发的资料
## 6. 时间安排与提交方式
## 7. 存疑或材料未提及的点

硬性规则：
- **只能依据材料**，不得编造、不得外推常识。材料没提到的，写「材料未提及」。
- 每一条结论后面用 `> ` 附上支撑它的**原文片段**（可以是转写里的原话，或课件上的原字）。
- 老师强调的禁令（例如不许抄网络文章、不许用 AI 代写）要**原样保留**其措辞。
- 课件文字若明显来自缩略图、字迹可疑，要在第 7 节标注「需以下周正式通知为准」。
- 不要输出材料之外的任何内容。
"""

NOTES_TEMPLATE = """你是课堂笔记整理助手。下面是某节中国大学课堂的**逐字转写**与**课件文字**。
请整理成结构化课堂笔记，严格按下面格式输出 Markdown：

# 课堂笔记

对每一章（按内容自然分段，通常 3～7 章）：
## <章节标题>
- **要点**：3～6 条，简洁陈述
- **关键概念/公式/案例**：列出出现的术语、公式、人名、公司、案例名
- **时间戳**：该章在视频中的大致时间（转写里有 [mm:ss]，用它）

硬性规则：
- **只能依据材料**，不得补充材料以外的知识。
- 术语、人名、公司名、数字要忠实于材料（例如年份、诺贝尔奖得主）。
- 不要写"这节课讲了……"这类空话，直接给要点。
"""


def _log(msg: str) -> None:
    print(f"[digest] {msg}", file=sys.stderr, flush=True)


def keyword_lines(text: str, keywords: list[str]) -> list[str]:
    hits = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or len(line) > 600:
            continue
        if any(k in line for k in keywords):
            hits.append(line)
    return hits


def _chat(cfg: Config, system: str, user: str) -> str:
    model = getattr(cfg.digest, "model", "") or cfg.asr.model
    max_tokens = int(getattr(cfg.digest, "max_tokens", 8000) or 8000)
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    last = None
    for _ in range(3):
        try:
            r = requests.post(cfg.asr.gateway_url,
                              headers={"Authorization": f"Bearer {cfg.asr.gateway_key}"},
                              json=payload, timeout=600)
            if r.status_code == 200:
                body = r.json()
                choices = body.get("choices") or []
                if not choices:
                    last = "模型返回空结果（choices 为空）"
                    continue
                text = (choices[0].get("message") or {}).get("content") or ""
                if text.strip():
                    return text
                last = "模型返回空文本"
                continue
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
    raise RuntimeError(f"归纳调用失败（{model}）：{last}")


def _material(run: RunDir, transcript: str, slides: str) -> str:
    parts = []
    if transcript:
        parts.append("# 逐字转写\n\n" + transcript)
    if slides:
        parts.append("# 课件文字（OCR）\n\n" + slides)
    return "\n\n".join(parts)


def _title(run: RunDir) -> str:
    """优先用 meta 里的课程/日期/时段拼标题，退回目录名。"""
    meta = run.load_meta()
    course = meta.get("course") or "课程"
    date = meta.get("date") or run.root.parent.name
    start = (meta.get("start") or "")[:5]
    end = (meta.get("end") or "")[:5]
    slot = f"{start}-{end}".strip("-")
    return f"{course} {date} {slot}".strip() if slot else f"{course} {date}"


def build_homework(cfg: Config, run: RunDir, transcript: str, slides: str,
                   force: bool = False) -> Path:
    out = run.homework_path
    if out.is_file() and not force and out.stat().st_size > 0:
        _log(f"{out.name} 已存在，跳过")
        return out
    title = _title(run)
    body = _chat(cfg, HOMEWORK_TEMPLATE,
                 f"（课时：{title}）\n\n" + _material(run, transcript, slides))
    evidence = keyword_lines(transcript, KEYWORDS) + keyword_lines(slides, KEYWORDS)
    out = run.homework_path
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"# 作业要求汇总 · {title}\n\n")
        fh.write("> 本文件由 `scls` 自动归纳（转写 + 课件 OCR）。结论请对照文末**证据区**核对。\n\n")
        fh.write(body.strip() + "\n\n---\n\n## 证据区（含关键词的原文行）\n\n")
        fh.write("```\n" + "\n".join(evidence[:400]) + "\n```\n")
    _log(f"作业要求 → {out}")
    return out


def build_notes(cfg: Config, run: RunDir, transcript: str, slides: str,
                force: bool = False) -> Path:
    out = run.notes_path
    if out.is_file() and not force and out.stat().st_size > 0:
        _log(f"{out.name} 已存在，跳过")
        return out
    title = _title(run)
    body = _chat(cfg, NOTES_TEMPLATE,
                 f"（课时：{title}）\n\n" + _material(run, transcript, slides))
    out = run.notes_path
    out.write_text(f"# 课堂笔记 · {title}\n\n> 由 `scls` 自动整理，仅供复习参考。\n\n"
                   + body.strip() + "\n", encoding="utf-8")
    _log(f"课堂笔记 → {out}")
    return out
