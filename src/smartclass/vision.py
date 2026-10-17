"""课件抽帧 + 多模态 OCR。

mode=assignment：只抄与作业/任务/教材相关的幻灯片（用于提取作业要求）
mode=full      ：抄录每张幻灯片的全部文字（用于生成课堂笔记）
"""
from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

import requests

from . import audio as audio_mod
from .config import Config
from .workspace import RunDir

PROMPT_ASSIGNMENT = (
    "下面是同一节中国大学课堂『屏幕录制』按固定间隔抽样得到的连续截图，按时间先后排列，"
    "每张前面标注了它在视频中的时间（mm:ss）和文件名。\n"
    "请逐张查看，凡是画面上出现与『作业 / 平时作业 / 大作业 / 实验报告 / 提交要求 / 截止时间 / "
    "分组 / 字数 / 格式 / 评分 / 课后任务 / 阅读材料 / 参考文献 / 课程任务』相关的内容，"
    "就把画面上的文字**逐字**抄录出来（含标题、条目编号、日期、ppt 页码），并注明来自哪张图。\n"
    "同一张幻灯片在多张图里重复出现的，合并成一条。与作业无关的普通课件内容（公式、图、讲解）不用抄。\n"
    "如果所有图里都没有作业相关内容，就只回复：NO_ASSIGNMENT\n"
    "只输出抄录内容，不要前言后语。"
)

PROMPT_FULL = (
    "下面是同一节中国大学课堂『屏幕录制』按固定间隔抽样得到的连续截图，按时间先后排列，"
    "每张前面标注了它在视频中的时间（mm:ss）和文件名。\n"
    "请逐张抄录画面上的**全部文字**（标题、要点、公式、图示中的文字、英文课件也照抄），"
    "并注明来自哪张图。同一张幻灯片重复出现的合并成一条。"
    "只输出抄录内容，不要总结、不要评价。"
)


def _log(msg: str) -> None:
    print(f"[vision] {msg}", file=sys.stderr, flush=True)


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def _ocr_batch(cfg: Config, images: list[tuple[str, Path]], prompt: str) -> str:
    content = [{"type": "text", "text": prompt}]
    for ts, p in images:
        content.append({"type": "text", "text": f"[{ts}] {p.name}"})
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + _b64(p)}})
    payload = {"model": cfg.vision.model,
               "messages": [{"role": "user", "content": content}],
               "max_tokens": cfg.vision.max_tokens, "temperature": 0}
    last = None
    for attempt in range(4):
        try:
            r = requests.post(cfg.asr.gateway_url,
                              headers={"Authorization": f"Bearer {cfg.asr.gateway_key}"},
                              json=payload, timeout=600)
            if r.status_code == 200:
                choices = (r.json().get("choices") or [])
                if not choices:
                    # 网关对某些输入会返回 HTTP 200 + 空 choices，别让 [0] 抛 IndexError
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
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"课件 OCR 失败：{last}")


def scan(cfg: Config, run: RunDir, video: Path, mode: str = "assignment",
         force: bool = False) -> Path:
    out = run.slides_path if mode == "assignment" else run.root / "slides_full.txt"
    if out.is_file() and not force and out.stat().st_size > 0:
        _log(f"{out.name} 已存在，跳过")
        return out

    frames = audio_mod.extract_frames(cfg, video, run.frames,
                                      cfg.vision.interval_seconds, cfg.vision.width)
    interval = cfg.vision.interval_seconds
    items = [(f"{(i * interval) // 60:02d}:{(i * interval) % 60:02d}", p)
             for i, p in enumerate(frames)]
    prompt = PROMPT_ASSIGNMENT if mode == "assignment" else PROMPT_FULL

    batch = cfg.vision.batch
    n_batches = (len(items) + batch - 1) // batch
    _log(f"OCR {len(items)} 帧 / {n_batches} 批（mode={mode}）")
    with open(out, "w", encoding="utf-8") as fh:
        for i in range(0, len(items), batch):
            grp = items[i:i + batch]
            _log(f"  批次 {i // batch + 1}/{n_batches} [{grp[0][0]}..{grp[-1][0]}]")
            fh.write(f"\n\n########## BATCH {i // batch + 1}  "
                     f"[{grp[0][0]} .. {grp[-1][0]}] ##########\n")
            fh.write(_ocr_batch(cfg, grp, prompt) + "\n")
            fh.flush()
    _log(f"课件 OCR 完成 → {out}")
    return out
