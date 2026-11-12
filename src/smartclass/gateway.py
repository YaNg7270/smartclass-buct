"""OpenAI 兼容 /chat/completions 客户端：单次请求、限流退避、跨模型替代。

为什么单独一个模块：ASR / 课件 OCR / 归纳三条链路原来各写了一份「发请求 +
重试 + 解析 choices + 判空」的代码，空结果判定、限流退避、失败原因各不相同，
vision 与 digest 甚至没有备用模型。这里只做「发一次请求 / 在多个模型之间
替代 / 判定失败原因」，**不含任何业务提示词**（提示词留在 asr/vision/digest）。

失败原因统一成下面几种前缀，调用方据此判断是否限流：

- `HTTP <code>: <前 200 字>`
- `非 JSON 响应：<前 200 字>`
- `模型返回空结果（choices 为空）`
- `模型返回空文本`
- `<异常类名>: <消息>`（连接失败、超时等）
"""
from __future__ import annotations

import random
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, urlsplit

import requests

from .config import Config

DEFAULT_TIMEOUT = 600
RETRY_SLEEP_BASE = 3.0        # 限流退避：3 + random()*5 秒


class GatewayError(RuntimeError):
    """所有候选模型都失败。

    `reasons` 每条形如 `<model>: <why>`；`rate_limited` 表示全部失败原因都是
    429/503（此时对半切开音频、换并发都没用，应该降并发等一会儿）。
    """

    def __init__(self, message: str, reasons: list[str] | None = None,
                 rate_limited: bool = False):
        super().__init__(message)
        self.reasons = list(reasons or [])
        self.rate_limited = bool(rate_limited)


def _log(msg: str) -> None:
    print(f"[gateway] {msg}", file=sys.stderr, flush=True)


def origin(url: str) -> str:
    """规范化协议、主机和端口，用来限制密钥的共享范围。"""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        if (parts.scheme not in ("https", "http") or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.fragment or parts.port == 0
                or "\\" in url or any(ord(c) < 33 or c.isspace() for c in url)):
            raise ValueError
        if any("key" in k.lower() or "token" in k.lower() or "secret" in k.lower()
               for k, _ in parse_qsl(parts.query, keep_blank_values=True)):
            raise ValueError
        host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
        port = parts.port or (443 if parts.scheme == "https" else 80)
        return f"{parts.scheme}://{host}:{port}"
    except ValueError:
        raise ValueError("API 地址须为 HTTP(S) 地址，密钥请填写在单独的密钥栏。") from None


def endpoint(cfg: Config, modality: str) -> tuple[str, str]:
    """接受 API 基址或完整端点，只允许同源请求继承 ASR 密钥。"""
    sec = getattr(cfg, modality)
    url = sec.gateway_url.strip() or cfg.asr.gateway_url.strip()
    if not url:
        return "", ""
    host = origin(url)
    key = sec.gateway_key
    if (modality != "asr" and not key and sec.inherit_key
            and host == origin(cfg.asr.gateway_url)):
        key = cfg.asr.gateway_key
    protocol = cfg.asr.protocol if modality == "asr" else "chat"
    if protocol not in ("chat", "transcriptions"):
        raise ValueError("语音协议须为 chat 或 transcriptions。")
    suffix = "/audio/transcriptions" if protocol == "transcriptions" else "/chat/completions"
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    for known in ("/audio/transcriptions", "/chat/completions"):
        if path.endswith(known):
            path = path[:-len(known)] + suffix
            break
    else:
        if not path:
            path = "/v1" + suffix
        elif path.endswith("/v1") or parts.path.endswith("/"):
            path += suffix
    return parts._replace(path=path).geturl(), key


def models_for(cfg: Config, modality: str) -> list[str]:
    """[主模型] + fallback_models，去空、去重、保序。"""
    if modality == "asr":
        sec = cfg.asr
    else:
        sec = getattr(cfg, modality, None)
    primary = str(getattr(sec, "model", "") or "").strip()
    fallbacks = getattr(sec, "fallback_models", None) or []
    out: list[str] = []
    for m in [primary, *[str(x or "").strip() for x in fallbacks]]:
        if m and m not in out:
            out.append(m)
    return out


def trusted_ca(cfg: Config, modality: str, url: str) -> str:
    """只把 PEM 交给该阶段导入时的同一个 HTTPS 源。"""
    host = origin(url)
    if host.startswith("https://") and host == cfg.tls.origins.get(modality):
        return cfg.tls.ca_pem
    return ""


def is_rate_limit(why: str) -> bool:
    """限流 / 上游过载：退避后重试同一个模型，而不是立刻换模型。"""
    return (why or "").startswith(("HTTP 429", "HTTP 503"))


def raw_chat(cfg: Config, *, modality: str, model: str, messages: list[dict],
             max_tokens: int, timeout: float = DEFAULT_TIMEOUT,
             temperature: float = 0, audio_file: Path | None = None) -> tuple[str | None, str]:
    """发一次请求。返回 (文本, "") 或 (None, 失败原因)。

    空 choices 一律按失败返回，绝不索引 [0]：网关对某些输入会返回
    HTTP 200 + `"choices": []`（completion_tokens=0）。
    """
    try:
        url, key = endpoint(cfg, modality)
    except ValueError as exc:
        return None, str(exc)
    if not url or not model.strip():
        return None, f"请在 API 设置中填写 {modality} 的端点和模型。"
    try:
        with ExitStack() as resources:
            params = {"headers": {"Authorization": f"Bearer {key}"} if key else {},
                      "timeout": timeout, "allow_redirects": False}
            pem = trusted_ca(cfg, modality, url)
            if pem:
                # requests 接收 CA 文件；每个请求独立使用，结束后即删除。
                with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as cert:
                    resources.callback(Path(cert.name).unlink, missing_ok=True)
                    cert.write(pem.encode("ascii"))
                params["verify"] = cert.name
            if audio_file is not None:
                audio = resources.enter_context(audio_file.open("rb"))
                params["data"] = {"model": model, "language": "zh"}
                params["files"] = {"file": (audio_file.name, audio, "audio/mpeg")}
            else:
                params["json"] = {"model": model, "messages": messages,
                                  "max_tokens": max_tokens, "temperature": temperature}
            r = requests.post(url, **params)
    except requests.exceptions.SSLError:
        return None, "TLS 证书验证失败，请检查地址是否匹配证书、证书有效期和导入的 CA。"
    except (OSError, requests.RequestException) as exc:
        return None, f"{type(exc).__name__}: 请检查网络及 API 地址"
    # 服务商可能在错误正文中回显凭据，日志只能使用脱敏文本。
    def excerpt():
        return (r.text.replace(key, "••••") if key else r.text)[:200]

    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {excerpt()}"
    try:
        body = r.json()
    except Exception:
        return None, f"非 JSON 响应：{excerpt()}"
    if not isinstance(body, dict):
        return None, "模型响应应为 JSON 对象"
    if audio_file is not None:
        text = body.get("text")
    else:
        choices = body.get("choices")
        if not choices or not isinstance(choices, list) or not isinstance(choices[0], dict):
            return None, "模型返回空结果（choices 为空）"
        message = choices[0].get("message")
        text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str) or not text.strip():
        return None, "模型返回空文本"
    return text, ""


def chat(cfg: Config, *, modality: str, messages: list[dict], max_tokens: int,
         timeout: float = DEFAULT_TIMEOUT, attempts: int = 3,
         validate: Callable[[str], str | None] | None = None,
         label: str = "", audio_file: Path | None = None) -> str:
    """按 `models_for` 顺序尝试，每个模型最多 `attempts` 次。

    - 限流（`is_rate_limit`）→ 退避 `RETRY_SLEEP_BASE + random()*5` 秒后重试同模型；
    - 其它失败 → 立即换下一个模型（空结果换模型通常就好了）；
    - 成功但 `validate(text)` 返回非空字符串 → 记为该次失败原因（不走限流退避），
      最多 2 次后换模型。

    全部失败抛 `GatewayError`，`reasons` 每条形如 `<model>: <why>`。
    """
    models = models_for(cfg, modality)
    if not models:
        raise GatewayError(f"请在 API 设置中填写 {modality} 的模型。")
    failures: list[tuple[str, str]] = []
    for idx, model in enumerate(models):
        rejected = 0
        for attempt in range(1, max(1, attempts) + 1):
            text, why = raw_chat(cfg, modality=modality, model=model, messages=messages,
                                 max_tokens=max_tokens, timeout=timeout, audio_file=audio_file)
            if text is not None:
                reason = validate(text) if validate else None
                if not reason:
                    if idx > 0:
                        _log(f"{label} 用备用模型 {model} 成功")
                    return text
                why = f"校验未通过：{reason}"
                failures.append((model, why))
                rejected += 1
                if rejected >= 2:
                    break
                continue
            failures.append((model, why))
            if not is_rate_limit(why):
                break  # 其它失败（空结果/HTTP 4xx/5xx）直接换模型
            if attempt >= max(1, attempts):
                break
            delay = RETRY_SLEEP_BASE + random.random() * 5
            _log(f"{label} 被限流，{delay:.1f}s 后重试（{model}）")
            time.sleep(delay)

    reasons = [f"{m}: {w}" for m, w in failures]
    rate_limited = bool(failures) and all(is_rate_limit(w) for _, w in failures)
    raise GatewayError(
        f"模型调用失败（{label}）：" + "；".join(reasons[-4:]),
        reasons=reasons, rate_limited=rate_limited)
