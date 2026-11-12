"""课件抽帧 + 多模态 OCR。

mode=assignment：只抄与作业/任务/教材相关的幻灯片（用于提取作业要求）
mode=full      ：抄录每张幻灯片的全部文字（用于生成课堂笔记）
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

from . import audio as audio_mod
from . import gateway
from . import scheduler
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


def _ocr_batch(cfg: Config, images: list[tuple[str, Path]], prompt: str,
               model: str | None = None) -> str:
    """一批图片送一次 OCR。

    `model` 非空时只测这一个模型（bench 用，直连 `raw_chat`，不走替代链）；
    留空则走 `gateway.chat`（主模型 → fallback_models）。
    """
    content = [{"type": "text", "text": prompt}]
    for ts, p in images:
        content.append({"type": "text", "text": f"[{ts}] {p.name}"})
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + _b64(p)}})
    messages = [{"role": "user", "content": content}]
    if model:
        text, why = gateway.raw_chat(cfg, modality="vision", model=model,
                                     messages=messages,
                                     max_tokens=cfg.vision.max_tokens)
        if text is None:
            raise RuntimeError(f"课件 OCR 失败（{model}）：{why}")
        return text
    return gateway.chat(cfg, modality="vision", messages=messages,
                        max_tokens=cfg.vision.max_tokens, label="OCR")


def scan(cfg: Config, run: RunDir, video: Path, mode: str = "assignment",
         force: bool = False) -> Path:
    out = run.slides_path if mode == "assignment" else run.root / "slides_full.txt"
    if out.is_file() and not force and out.stat().st_size > 0:
        _log(f"{out.name} 已存在，跳过")
        return out

    frames = audio_mod.extract_frames(cfg, video, run.frames,
                                      cfg.vision.interval_seconds, cfg.vision.width)
    interval = cfg.vision.interval_seconds
    # 去重只减少「几乎一样的相邻帧」，原始序号保留 → 时间戳仍然准确
    if getattr(cfg.vision, "dedupe", False):
        picked = audio_mod.dedupe_frames(frames, cfg.vision.dedupe_threshold)
    else:
        picked = list(enumerate(frames))
    items = [(f"{(idx * interval) // 60:02d}:{(idx * interval) % 60:02d}", p)
             for idx, p in picked]
    prompt = PROMPT_ASSIGNMENT if mode == "assignment" else PROMPT_FULL

    batch = max(1, int(cfg.vision.batch))
    batches = [items[i:i + batch] for i in range(0, len(items), batch)]
    _log(f"OCR {len(items)} 帧 / {len(batches)} 批（mode={mode}）")

    scheduler.configure(cfg)

    def work(pair: tuple[int, list[tuple[str, Path]]]):
        n, grp = pair
        _log(f"  批次 {n + 1}/{len(batches)} [{grp[0][0]}..{grp[-1][0]}] 开始")
        return n, _ocr_batch(cfg, grp, prompt)

    jobs = list(enumerate(batches))
    results = (scheduler.gateway_map(work, jobs, label="OCR")
               if len(jobs) > 1 else [work(it) for it in jobs])
    results.sort(key=lambda x: x[0])

    with open(out, "w", encoding="utf-8") as fh:
        for n, text in results:
            grp = batches[n]
            fh.write(f"\n\n########## BATCH {n + 1}  "
                     f"[{grp[0][0]} .. {grp[-1][0]}] ##########\n")
            fh.write(text + "\n")
    _log(f"课件 OCR 完成 → {out}")
    return out
