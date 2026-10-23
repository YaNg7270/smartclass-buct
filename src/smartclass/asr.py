"""分块语音转写：默认走本地 OpenAI 兼容网关（Gemini 多模态），可切本地 whisper。

每块结果单独落盘（transcript/part_NNN.json），重跑只补失败/缺失的块。
"""
from __future__ import annotations

import base64
import json
import random
import re
import sys
import time
from pathlib import Path

import requests

from . import audio as audio_mod
from . import scheduler
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

_CJK = "\\u4e00-\\u9fff"


def _clean_text(text: str) -> str:
    """个别快模型会在汉字之间插空格（"缺 29 个 。"），去掉但不碰英文/数字。

    只做格式清理，不改字词；对已经是正常中文的模型输出几乎无影响。
    """
    t = re.sub(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])", "", text)
    t = re.sub(rf"(?<=[{_CJK}])\s+(?=[，。！？；：、）》”’])", "", t)
    t = re.sub(rf"(?<=[，。！？；：、）》”’])\s+(?=[{_CJK}])", "", t)
    t = re.sub(rf"(?<=[（《“‘])\s+(?=[{_CJK}])", "", t)
    t = re.sub(r"(?<=\d)\s+(?=[个分秒年月日天次元倍人])", "", t)
    return t


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


def _post_gateway(cfg: Config, model: str, audio_path: Path, offset: int,
                  duration: float) -> tuple[str | None, str]:
    """发一次转写请求。返回 (文本, 失败原因)；失败时文本为 None。

    必须显式判断 choices 是否为空：网关对某些音频会返回 HTTP 200 +
    `"choices": []`（completion_tokens=0），旧代码直接取 [0] 就抛
    IndexError，既没重试也没降级。
    """
    b64 = base64.b64encode(audio_path.read_bytes()).decode()
    head = (f"（本段从第 {offset // 60} 分 {offset % 60} 秒开始，时长约 {duration:.0f} 秒）"
            f"请转写这段课堂录音。时间戳按本段内部相对时间写。")
    payload = {
        "model": model,
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
    try:
        r = requests.post(cfg.asr.gateway_url,
                          headers={"Authorization": f"Bearer {cfg.asr.gateway_key}"},
                          json=payload, timeout=900)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:200]}"
    try:
        body = r.json()
    except Exception:
        return None, f"非 JSON 响应：{r.text[:200]}"
    choices = body.get("choices") or []
    if not choices:
        usage = body.get("usage") or {}
        return None, (f"模型返回空结果（prompt={usage.get('prompt_tokens')} tokens，"
                      f"completion=0）模型={model}")
    text = (choices[0].get("message") or {}).get("content") or ""
    if not text.strip():
        return None, f"模型返回空文本，模型={model}"
    text = _clean_text(text)
    if _looks_like_echo(text):
        return None, "模型复述了说明（疑似串味）"
    return text, ""


def _split_audio(cfg: Config, src: Path, dest_dir: Path) -> list[Path]:
    """把一段音频对半切开（二分法），用于绕过模型对某段音频的空返回。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dur = audio_mod.duration(cfg, src)
    if dur <= 0:
        return []
    half = dur / 2
    out = []
    for i, start in enumerate((0.0, half)):
        dst = dest_dir / f"{src.stem}_{i}.mp3"
        if not (dst.is_file() and dst.stat().st_size > 0):
            audio_mod._run([cfg.paths.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                            "-ss", f"{start:.3f}", "-t", f"{half:.3f}", "-i", str(src),
                            "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame",
                            "-b:a", cfg.asr.bitrate, str(dst)])
        out.append(dst)
    return out


def _is_rate_limit(why: str) -> bool:
    return why.startswith(("HTTP 429", "HTTP 503"))


def _transcribe_segment(cfg: Config, path: Path, offset: int, model: str,
                        depth: int = 0) -> str:
    """转写一段音频：按主模型 → 回退模型 → 对半切开 的顺序尝试。"""
    dur = audio_mod.duration(cfg, path)
    models = [model] + [m for m in (cfg.asr.fallback_models or []) if m and m != model]
    reasons = []
    for m in models:
        attempt = 0
        while attempt < 3:
            attempt += 1
            text, why = _post_gateway(cfg, m, path, offset, dur)
            if text is not None:
                if m != model:
                    _log(f"  {path.name} 用回退模型 {m} 成功")
                return text
            reasons.append(f"{m}: {why}")
            if _is_rate_limit(why):
                # 限流：同一模型退避重试；并发下要给其他请求让路
                if attempt >= 3:
                    break
                delay = 3 + random.random() * 5
                _log(f"  {path.name} 被限流，{delay:.1f}s 后重试（{m}）")
                time.sleep(delay)
                continue
            if why.startswith(("HTTP 4", "HTTP 5")):
                break  # 其它 HTTP 错误直接换模型
            # 空结果 / 复述说明：最多试 2 次
            if attempt >= 2:
                break
            time.sleep(2)

    rate_limited = bool(reasons) and all(
        ("HTTP 429" in r or "HTTP 503" in r) for r in reasons)
    can_split = (dur > max(cfg.asr.min_split_seconds, 10) * 2 and depth < 3
                 and not rate_limited)
    if can_split:
        halves = _split_audio(cfg, path, path.parent / "split")
        _log(f"  {path.name} 各模型均失败，对半切开重试（深度 {depth + 1}）")
        texts = []
        for i, h in enumerate(halves):
            sub_off = offset + (0 if i == 0 else int(dur / 2))
            texts.append(_transcribe_segment(cfg, h, sub_off, model, depth + 1))
        joined = "\n".join(t.strip() for t in texts if t.strip())
        if joined.strip():
            return joined

    hint = "（疑似限流：把 [concurrency] gateway 降到 2）" if rate_limited else ""
    raise RuntimeError(
        f"网关转写失败（{path.name}）：" + "；".join(reasons[-4:]) + hint
    )


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
        scheduler.configure(cfg)

    chunks, total = audio_mod.split_chunks(cfg, audio_path, run.audio / "chunks", cfg.asr.chunk_seconds)
    _log(f"共 {len(chunks)} 块，合计 {total:.0f}s，provider={provider}")

    slots: list[dict | None] = [None] * len(chunks)
    todo: list[tuple[int, audio_mod.Chunk, Path]] = []
    for i, ch in enumerate(chunks):
        pj = run.transcript / f"part_{ch.offset // cfg.asr.chunk_seconds:03d}.json"
        if pj.is_file() and not force:
            try:
                data = json.loads(pj.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            if data.get("text"):
                _log(f"复用 {pj.name}")
                slots[i] = data
                continue
        todo.append((i, ch, pj))

    if todo:
        run.transcript.mkdir(parents=True, exist_ok=True)

        def work(item: tuple[int, audio_mod.Chunk, Path]):
            i, ch, pj = item
            t0 = time.perf_counter()
            _log(f"转写 {ch.path.name}（offset={ch.offset}s）")
            text = (_transcribe_segment(cfg, ch.path, ch.offset, cfg.asr.model)
                    if provider == "gateway" else _whisper_transcribe(cfg, ch, run.audio / "chunks"))
            data = {"index": ch.offset // cfg.asr.chunk_seconds, "offset": ch.offset,
                    "dur": ch.dur, "text": text}
            pj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            _log(f"  {ch.path.name} 完成（{time.perf_counter() - t0:.1f}s）")
            return i, data

        # whisper 是本地进程、很吃 CPU，不并发；网关请求走全局并发池
        results = (scheduler.gateway_map(work, todo, label="ASR")
                   if provider == "gateway" and len(todo) > 1
                   else [work(it) for it in todo])
        for i, data in results:
            slots[i] = data

    parts = [d for d in slots if d is not None]
    out = run.full_transcript
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"# 转写 {audio_path.name}  total={total:.0f}s provider={provider}\n")
        for d in parts:
            mm, ss = divmod(int(d["offset"]), 60)
            fh.write(f"\n\n===== [{mm:02d}:{ss:02d} +{int(d['dur'] // 60)}m] =====\n")
            fh.write((d["text"] or "").strip() + "\n")
    _log(f"转写完成 → {out}")
    return out
