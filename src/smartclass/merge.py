"""把各小节的中间产物合并成整课级的转写与课件文字。

关键点：小节内的 [mm:ss] 是相对该小节的时间戳，合并时必须按小节在整课中的
起点平移，否则整课转写里会出现两个 [02:13]。
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .workspace import RunDir

RE_TS = re.compile(r"\[(\d{1,2}):(\d{2})\]")


def _log(msg: str) -> None:
    print(f"[merge] {msg}", file=sys.stderr, flush=True)


def shift_timestamps(text: str, base_seconds: int) -> str:
    """把文本里的 [mm:ss] 整体平移 base_seconds。"""
    def repl(m: re.Match) -> str:
        total = base_seconds + int(m.group(1)) * 60 + int(m.group(2))
        return f"[{total // 60:02d}:{total % 60:02d}]"

    return RE_TS.sub(repl, text or "")


@dataclass
class PartRef:
    label: str       # "08:00-08:45"
    run: RunDir
    base_seconds: int  # 该小节起点相对整课起点的秒数


def _part_text(part: PartRef) -> str:
    """优先用分块 JSON（能拿到块内 offset），退回小节 full.txt。"""
    jsons = sorted(part.run.transcript.glob("part_*.json"))
    if jsons:
        chunks = []
        for p in jsons:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            base = part.base_seconds + int(d.get("offset") or 0)
            chunks.append(shift_timestamps(d.get("text") or "", base))
        if chunks:
            return "\n".join(chunks).strip()
    if part.run.full_transcript.is_file():
        return shift_timestamps(part.run.full_transcript.read_text(encoding="utf-8"),
                                part.base_seconds).strip()
    return ""


def merge_transcripts(parts: list[PartRef], out: Path, title: str = "") -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"# 整课转写 · {title}\n")
        fh.write(f"# 共 {len(parts)} 小节；时间戳已按整课时间轴对齐\n")
        for p in parts:
            fh.write(f"\n\n{'=' * 24} 【{p.label}】 {'=' * 24}\n\n")
            fh.write(_part_text(p) or "（本小节无转写）")
            fh.write("\n")
    _log(f"整课转写 → {out}")
    return out


def merge_slides(parts: list[PartRef], out: Path, title: str = "") -> Path | None:
    body = []
    for p in parts:
        if p.run.slides_path.is_file():
            body.append((p.label, p.run.slides_path.read_text(encoding="utf-8").strip()))
    if not body:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"# 整课课件文字 · {title}\n")
        for label, text in body:
            fh.write(f"\n\n{'=' * 24} 【{label}】 {'=' * 24}\n\n")
            fh.write(text + "\n")
    _log(f"整课课件文字 → {out}")
    return out
