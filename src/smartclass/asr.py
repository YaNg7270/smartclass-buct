"""分块语音转写：默认走本地 OpenAI 兼容网关（Gemini 多模态），可切本地 whisper。

每块结果单独落盘（transcript/part_NNN.json），重跑只补失败/缺失的块。
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import requests

from . import audio as audio_mod
from .config import Config
from .workspace import RunDir

SYS_PROMPT = (
    "你是课堂录音转写员。把音频里听到的中文讲话**逐字转写**，不要总结、不要省略、不要翻译，"
    "每 30～60 秒给一个 [mm:ss] 时间戳。老师说到的作业、实验报告、提交要求、截止时间、分组、"
    "字数、格式、评分标准等，务必原样完整转写，不要概括。听不清的地方用 ??? 表示。\n"
    "**严禁复述、翻译或评论本说明本身**；只输出转写正文。"
    "整段都是静音或只有环境噪声时，只回复：SILENCE"
)

# 模型若复述说明，命中这些片段就判为串味，重试
_ECHO_MARKERS = (
    "逐字转写", "不要总结", "只输出转写正文", "听不清的地方用", "你是课堂录音转写员",
)


def _log(msg: str) -> None:
    print(f"[asr] {msg}", file=sys.stderr, flush=True)


def check_gateway(cfg: Config) -> None:
    try:
        r = requests.get(cfg.asr.gateway_url.replace("/chat/completions", "/models"),
                         headers={"Authorization": f"Bearer {cfg.asr.gateway_key}"}, timeout=8)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
    except Exception as exc:
        raise RuntimeError(
            f"转写网关不可用（{cfg.asr.gateway_url}）：{exc}\n"
            f"请确认 cc-switch 本地代理已启动，或用 `--asr whisper` 走本地模型。"
        ) from exc


def _looks_like_echo(text: str) -> bool:
    head = (text or "")[:400]
    return any(m in head for m in _ECHO_MARKERS) and len(head) < 500


def _gateway_transcribe(cfg: Config, chunk: audio_mod.Chunk) -> str:
    b64 = base64.b64encode(chunk.path.read_bytes()).decode()
    head = (f"（本段从第 {chunk.offset // 60} 分 {chunk.offset % 60} 秒开始）"
            f"请转写这段课堂录音。时间戳按本段内部相对时间写。")
    payload = {
        "model": cfg.asr.model,
        "messages": [
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": head},
                {"type": "input_audio", "input_audio": {"data": b64, "format": "mp3"}},
            ]},
        ],
        "max_tokens": cfg.asr.max_tokens,
        "temperature": 0,
    }
    last = None
    for attempt in range(4):
        try:
            r = requests.post(cfg.asr.gateway_url,
                              headers={"Authorization": f"Bearer {cfg.asr.gateway_key}"},
                              json=payload, timeout=600)
            if r.status_code == 200:
                text = r.json()["choices"][0]["message"]["content"]
                if not _looks_like_echo(text):
                    return text
                last = "模型复述了说明（疑似串味）"
                time.sleep(3)
                continue
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"网关转写失败（{chunk.path.name}）：{last}")


def _whisper_transcribe(cfg: Config, chunk: audio_mod.Chunk, outdir: Path) -> str:
    import subprocess
    txt = outdir / (chunk.path.stem + ".whisper.txt")
    if not txt.is_file():
        proc = subprocess.run(
            [cfg.asr.whisper_bin, str(chunk.path), "--model", cfg.asr.whisper_model,
             "--language", "zh", "--output_format", "txt", "--output_dir", str(outdir),
             "--verbose", "False"],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"whisper 失败：{proc.stderr[-500:]}")
        produced = outdir / (chunk.path.stem + ".txt")
        if produced.is_file():
            produced.replace(txt)
    return txt.read_text(encoding="utf-8").strip() if txt.is_file() else ""


def transcribe(cfg: Config, run: RunDir, audio_path: Path, provider: str | None = None,
               force: bool = False) -> Path:
    provider = provider or cfg.asr.provider
    if provider == "gateway":
        check_gateway(cfg)

    chunks, total = audio_mod.split_chunks(cfg, audio_path, run.audio / "chunks", cfg.asr.chunk_seconds)
    _log(f"共 {len(chunks)} 块，合计 {total:.0f}s，provider={provider}")

    parts: list[dict] = []
    for ch in chunks:
        pj = run.transcript / f"part_{ch.offset // cfg.asr.chunk_seconds:03d}.json"
        if pj.is_file() and not force:
            data = json.loads(pj.read_text(encoding="utf-8"))
            if data.get("text"):
                _log(f"复用 {pj.name}")
                parts.append(data)
                continue
        _log(f"转写 {ch.path.name}（offset={ch.offset}s）")
        text = (_gateway_transcribe(cfg, ch) if provider == "gateway"
                else _whisper_transcribe(cfg, ch, run.audio / "chunks"))
        data = {"index": ch.offset // cfg.asr.chunk_seconds, "offset": ch.offset,
                "dur": ch.dur, "text": text}
        pj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        parts.append(data)

    out = run.full_transcript
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"# 转写 {audio_path.name}  total={total:.0f}s provider={provider}\n")
        for d in parts:
            mm, ss = divmod(int(d["offset"]), 60)
            fh.write(f"\n\n===== [{mm:02d}:{ss:02d} +{int(d['dur'] // 60)}m] =====\n")
            fh.write((d["text"] or "").strip() + "\n")
    _log(f"转写完成 → {out}")
    return out
