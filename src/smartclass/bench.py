"""网关基准：用已经缓存好的音频块 / 抽帧，测不同并发度与模型的墙钟时间。

不重新抽音频、不重新抽帧、不覆盖任何转写缓存；只把结果打到终端并写一份
`data/_logs/bench-<时间>.json`，供选默认值用。
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import sys
import time
from pathlib import Path

from . import asr as asr_mod
from . import audio as audio_mod
from . import scheduler
from . import vision as vision_mod
from .config import Config, runs_root


def _log(msg: str) -> None:
    print(f"[bench] {msg}", file=sys.stderr, flush=True)


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def pick_part(root: Path) -> Path:
    """接受整课包、数据根目录或单个小节目录。"""
    root = Path(root).resolve()
    if (root / "audio" / "chunks").is_dir():
        return root
    cands = sorted(p.parent.parent for p in root.rglob("audio/chunks/part_*.mp3"))
    if not cands:
        raise SystemExit(f"{root} 下没有找到 audio/chunks/part_*.mp3，先跑一次 scls run/resume")
    return cands[0]


def _fmt_table(rows: list[dict]) -> str:
    head = f"{'stage':4s} {'model':26s} {'conc':>4s} {'wall(s)':>8s} {'avg(s)':>7s} {'chars':>7s} {'sim':>5s} {'empty':>5s}"
    lines = [head, "-" * len(head)]
    for r in rows:
        lines.append(f"{r['stage']:4s} {r['model']:26s} {r['conc']:>4d} {r['wall']:>8.1f} "
                     f"{r['avg']:>7.1f} {r['chars']:>7d} {r.get('sim', 0):>5.2f} {r['empty']:>5d}")
    return "\n".join(lines)


def bench_asr(cfg: Config, part: Path, models: list[str], concs: list[int],
              n: int = 3) -> list[dict]:
    chunks = sorted((part / "audio" / "chunks").glob("part_*.mp3"))[:n]
    if not chunks:
        _log("没有音频块，跳过 ASR")
        return []
    durations = {ch: audio_mod.duration(cfg, ch) for ch in chunks}
    baseline = {}
    for ch in chunks:
        pj = part / "transcript" / (ch.stem + ".json")
        if pj.is_file():
            try:
                baseline[ch.name] = json.loads(pj.read_text(encoding="utf-8")).get("text", "")
            except Exception:
                pass

    rows = []
    for model in models:
        for conc in concs:
            cfg.concurrency.gateway = conc
            scheduler.configure(cfg)

            def one(ch: Path):
                text, why = asr_mod._post_gateway(cfg, model, ch, 0, durations[ch])
                return text, why

            _log(f"ASR {model} 并发 {conc}：{len(chunks)} 块")
            t0 = time.perf_counter()
            results = scheduler.gateway_map(one, chunks)
            wall = time.perf_counter() - t0
            texts = [t or "" for t, _ in results]
            empty = sum(1 for t, _ in results if not t)
            sim = (_similar("\n".join(texts), "\n".join(baseline.get(ch.name, "") for ch in chunks))
                   if baseline else 0.0)
            rows.append({"stage": "asr", "model": model, "conc": conc,
                         "wall": round(wall, 1), "avg": round(wall / len(chunks), 1),
                         "chars": sum(len(t) for t in texts), "sim": round(sim, 3),
                         "empty": empty,
                         "reasons": [w for _, w in results if w]})
    return rows


def bench_ocr(cfg: Config, part: Path, models: list[str], concs: list[int],
              n: int = 3) -> list[dict]:
    frames = sorted((part / "frames").glob("f_*.jpg"))
    if not frames:
        _log("没有抽帧，跳过 OCR")
        return []
    batch = max(1, int(cfg.vision.batch))
    groups = [frames[i:i + batch] for i in range(0, len(frames), batch)][:n]
    items = [[(f"{(j * cfg.vision.interval_seconds) // 60:02d}:"
               f"{(j * cfg.vision.interval_seconds) % 60:02d}", p)
              for j, p in enumerate(g)] for g in groups]

    rows = []
    baseline_texts: list[str] | None = None
    for model in models:
        cfg.vision.model = model
        for conc in concs:
            cfg.concurrency.gateway = conc
            scheduler.configure(cfg)

            def one(pair):
                return vision_mod._ocr_batch(cfg, pair[1], vision_mod.PROMPT_ASSIGNMENT)

            _log(f"OCR {model} 并发 {conc}：{len(items)} 批")
            t0 = time.perf_counter()
            texts = scheduler.gateway_map(one, list(enumerate(items)))
            wall = time.perf_counter() - t0
            sim = (_similar("\n".join(texts), "\n".join(baseline_texts))
                   if baseline_texts else 1.0)
            if baseline_texts is None:
                baseline_texts = texts
            rows.append({"stage": "ocr", "model": model, "conc": conc,
                         "wall": round(wall, 1), "avg": round(wall / len(items), 1),
                         "chars": sum(len(t) for t in texts), "sim": round(sim, 3),
                         "empty": sum(1 for t in texts if not t.strip())})
    return rows


def run(cfg: Config, root: str, stages: list[str], models_asr: list[str],
        models_ocr: list[str], concs: list[int], n: int = 3) -> int:
    asr_mod.check_gateway(cfg)
    part = pick_part(Path(root))
    _log(f"样本小节：{part}")
    rows: list[dict] = []
    if "asr" in stages:
        rows += bench_asr(cfg, part, models_asr, concs, n)
    if "ocr" in stages:
        rows += bench_ocr(cfg, part, models_ocr, concs, n)
    if not rows:
        return 1
    print("\n" + _fmt_table(rows) + "\n")
    outdir = runs_root(cfg, None) / "_logs"
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"bench-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"结果已写入 {out}")
    return 0
