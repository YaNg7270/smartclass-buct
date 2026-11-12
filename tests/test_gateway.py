"""gateway 单测：一次请求的失败分类、跨模型替代、限流退避、验证器。

全程 monkeypatch `requests.post` 与 `time.sleep`，不联网、不真的等待。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass import gateway                          # noqa: E402
from smartclass.config import Config                    # noqa: E402


class FakeResp:
    def __init__(self, status=200, body=None, text=None):
        self.status_code = status
        self._body = {} if body is None else body
        self.text = text if text is not None else str(body)

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _patch(monkeypatch, responses):
    """按调用顺序返回假响应，并记录每次请求的模型名。"""
    calls: list[str] = []
    queue = list(responses)

    def fake_post(url, headers=None, json=None, timeout=None, allow_redirects=False):
        calls.append(json["model"])
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(gateway.requests, "post", fake_post)
    monkeypatch.setattr(gateway.time, "sleep", lambda s: None)
    return calls


def _cfg(primary="main", fallbacks=()):
    cfg = Config()
    cfg.asr.gateway_url = "https://example.invalid/v1"
    cfg.asr.model = primary
    cfg.asr.fallback_models = list(fallbacks)
    return cfg


def _msgs():
    return [{"role": "user", "content": "hi"}]


# ---- 单次请求 --------------------------------------------------------------- #

def test_raw_chat_empty_choices_is_failure(monkeypatch):
    _patch(monkeypatch, [FakeResp(body={"choices": []})])
    assert gateway.raw_chat(_cfg(), modality="asr", model="m", messages=_msgs(),
                            max_tokens=100) == (None, "模型返回空结果（choices 为空）")


def test_raw_chat_failure_reasons_are_distinct(monkeypatch):
    _patch(monkeypatch, [FakeResp(status=500, text="boom")])
    text, why = gateway.raw_chat(_cfg(), modality="asr", model="m", messages=_msgs(),
                                 max_tokens=100)
    assert text is None and why.startswith("HTTP 500: ") and "boom" in why

    _patch(monkeypatch, [FakeResp(body=ValueError("not json"), text="<html>")])
    text, why = gateway.raw_chat(_cfg(), modality="asr", model="m", messages=_msgs(),
                                 max_tokens=100)
    assert text is None and why.startswith("非 JSON 响应：")

    _patch(monkeypatch, [FakeResp(body={"choices": [{"message": {"content": "  "}}]})])
    assert gateway.raw_chat(_cfg(), modality="asr", model="m", messages=_msgs(),
                            max_tokens=100) == (None, "模型返回空文本")


def test_models_for_dedupes_and_preserves_order():
    cfg = Config()
    cfg.asr.model = "a"
    cfg.asr.fallback_models = ["b", "a", "", "  ", "c"]
    assert gateway.models_for(cfg, "asr") == ["a", "b", "c"]


def test_endpoint_inherits_asr_endpoint():
    cfg = Config()
    cfg.asr.gateway_url = "http://asr/v1/chat/completions"
    cfg.asr.gateway_key = "asr-key"
    assert gateway.endpoint(cfg, "vision") == (cfg.asr.gateway_url, cfg.asr.gateway_key)

    cfg.vision.gateway_url = "http://vision/v1/chat/completions"
    cfg.vision.gateway_key = "vision-key"
    assert gateway.endpoint(cfg, "vision") == (cfg.vision.gateway_url, "vision-key")
    # digest 仍然继承 asr
    assert gateway.endpoint(cfg, "digest") == (cfg.asr.gateway_url, cfg.asr.gateway_key)


def test_endpoint_inherits_per_field():
    """gateway_url 与 gateway_key 各自留空就各自继承，互不牵连。"""
    cfg = Config()
    cfg.asr.gateway_url = "http://asr/v1/chat/completions"
    cfg.asr.gateway_key = "asr-key"

    # 只换口令：同端点、另一把 key
    cfg.vision.gateway_url = ""
    cfg.vision.gateway_key = "vision-key"
    assert gateway.endpoint(cfg, "vision") == (cfg.asr.gateway_url, "vision-key")

    # 换供应商时不能把 ASR 的口令转发给新服务，同源的其它路径仍可使用它。
    cfg.vision.gateway_url = "http://vision/v1/chat/completions"
    cfg.vision.gateway_key = ""
    assert gateway.endpoint(cfg, "vision") == ("http://vision/v1/chat/completions", "")
    cfg.vision.gateway_url = "http://asr/other"
    assert gateway.endpoint(cfg, "vision") == ("http://asr/other", "asr-key")

    # digest 同理
    cfg.digest.gateway_key = "digest-key"
    assert gateway.endpoint(cfg, "digest") == (cfg.asr.gateway_url, "digest-key")


# ---- 跨模型替代 ------------------------------------------------------------- #

def test_chat_falls_back_to_next_model(monkeypatch):
    cfg = _cfg("主模型", ["备用模型"])
    calls = _patch(monkeypatch, [
        FakeResp(body={"choices": []}),
        FakeResp(body={"choices": [{"message": {"content": "备用结果"}}]}),
    ])
    assert gateway.chat(cfg, modality="asr", messages=_msgs(), max_tokens=100,
                        label="t") == "备用结果"
    assert calls == ["主模型", "备用模型"]


def test_chat_rate_limit_retries_same_model_then_fails(monkeypatch):
    cfg = _cfg("主模型", ["备用模型"])
    calls = _patch(monkeypatch, [FakeResp(status=429, text="slow down")])
    with pytest.raises(gateway.GatewayError) as ei:
        gateway.chat(cfg, modality="asr", messages=_msgs(), max_tokens=100,
                     attempts=2, label="t")
    assert ei.value.rate_limited is True
    assert calls == ["主模型", "主模型", "备用模型", "备用模型"]
    # 主模型限流、备用模型正常 → 成功，且主模型被重试了 attempts 次
    calls = _patch(monkeypatch, [
        FakeResp(status=503, text="overloaded"),
        FakeResp(status=503, text="overloaded"),
        FakeResp(body={"choices": [{"message": {"content": "ok"}}]}),
    ])
    assert gateway.chat(cfg, modality="asr", messages=_msgs(), max_tokens=100,
                        attempts=2, label="t") == "ok"
    assert calls == ["主模型", "主模型", "备用模型"]


def test_chat_validate_rejects_then_falls_back(monkeypatch):
    cfg = _cfg("主模型", ["备用模型"])
    calls: list[str] = []

    def fake_post(url, headers=None, json=None, timeout=None, allow_redirects=False):
        calls.append(json["model"])
        said = "复述说明" if json["model"] == "主模型" else "正常正文"
        return FakeResp(body={"choices": [{"message": {"content": said}}]})

    monkeypatch.setattr(gateway.requests, "post", fake_post)
    monkeypatch.setattr(gateway.time, "sleep", lambda s: None)

    out = gateway.chat(cfg, modality="asr", messages=_msgs(), max_tokens=100,
                       validate=lambda t: "串味" if "复述" in t else None, label="t")
    assert out == "正常正文"
    # 主模型被拒 2 次（validate 失败不按限流长退避）后换备用模型
    assert calls == ["主模型", "主模型", "备用模型"]


def test_chat_validate_reason_is_recorded(monkeypatch):
    cfg = _cfg("主模型")
    _patch(monkeypatch, [FakeResp(body={"choices": [{"message": {"content": "x"}}]})])
    with pytest.raises(gateway.GatewayError) as ei:
        gateway.chat(cfg, modality="asr", messages=_msgs(), max_tokens=100,
                     validate=lambda t: "始终不合格", label="t")
    assert ei.value.reasons[0] == "主模型: 校验未通过：始终不合格"


def test_chat_all_models_fail_raises_with_reasons(monkeypatch):
    cfg = _cfg("主模型", ["备用模型"])
    _patch(monkeypatch, [FakeResp(body={"choices": []})])
    with pytest.raises(gateway.GatewayError) as ei:
        gateway.chat(cfg, modality="asr", messages=_msgs(), max_tokens=100, label="x")
    assert ei.value.rate_limited is False
    assert ei.value.reasons == ["主模型: 模型返回空结果（choices 为空）",
                                "备用模型: 模型返回空结果（choices 为空）"]
    assert "模型调用失败（x）" in str(ei.value)


def test_is_rate_limit_only_429_503():
    assert gateway.is_rate_limit("HTTP 429: x")
    assert gateway.is_rate_limit("HTTP 503: x")
    assert not gateway.is_rate_limit("HTTP 500: x")
    assert not gateway.is_rate_limit("模型返回空文本")


def test_transcription_upload_and_chat_inheritance(monkeypatch, tmp_path):
    from smartclass import asr
    cfg = _cfg()
    cfg.asr.gateway_url = "https://speech.example/v1/audio/transcriptions"
    cfg.asr.protocol = "transcriptions"
    cfg.asr.gateway_key = "speech-key"
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"sample audio")
    monkeypatch.setattr(asr.audio_mod, "duration", lambda *_: 15)
    monkeypatch.setattr(gateway.requests, "get", lambda *a, **k: pytest.fail("不得预检 /models"))
    calls = []

    def post(url, headers, data, files, timeout, allow_redirects):
        name, stream, mime = files["file"]
        calls.append((url, headers, data, name, stream.read(), mime))
        return FakeResp(body={"text": "课堂原文"})

    monkeypatch.setattr(gateway.requests, "post", post)
    asr.check_gateway(cfg)
    assert asr._transcribe_segment(cfg, sample, 0) == "课堂原文"
    assert calls == [(cfg.asr.gateway_url, {"Authorization": "Bearer speech-key"},
                      {"model": "main", "language": "zh"}, "sample.mp3", b"sample audio", "audio/mpeg")]
    assert gateway.endpoint(cfg, "digest") == ("https://speech.example/v1/chat/completions", "speech-key")


def test_ocr_and_summary_route_to_independent_endpoints(monkeypatch, tmp_path):
    from smartclass import digest, vision
    cfg = _cfg()
    cfg.asr.gateway_key = "speech-only"
    cfg.vision.gateway_url, cfg.vision.model = "https://vision.example/v1", "vision-model"
    cfg.digest.gateway_url, cfg.digest.model = "https://text.example/custom", "text-model"
    cfg.digest.gateway_key = "summary-only"
    jpg = tmp_path / "test.jpg"
    jpg.write_bytes(b"jpg")
    calls = []

    def post(url, headers, json, timeout, allow_redirects):
        calls.append((url, headers, json))
        return FakeResp(body={"choices": [{"message": {"content": "模型结果"}}]})

    monkeypatch.setattr(gateway.requests, "post", post)
    assert vision._ocr_batch(cfg, [("00:00", jpg)], "抄录") == "模型结果"
    assert digest._chat(cfg, "摘要", "正文") == "模型结果"
    assert [(url, h, body["model"]) for url, h, body in calls] == [
        ("https://vision.example/v1/chat/completions", {}, "vision-model"),
        ("https://text.example/custom", {"Authorization": "Bearer summary-only"}, "text-model"),
    ]
    assert calls[0][2]["messages"][0]["content"][-1]["type"] == "image_url"


def test_missing_settings_never_issue_inference_request(monkeypatch):
    monkeypatch.setattr(gateway.requests, "post", lambda *a, **k: pytest.fail("不得发出请求"))
    cfg = Config()
    assert gateway.endpoint(cfg, "asr") == ("", "")
    with pytest.raises(gateway.GatewayError, match="API 设置"):
        gateway.chat(cfg, modality="digest", messages=_msgs(), max_tokens=10)
    assert gateway.raw_chat(cfg, modality="vision", model="m", messages=_msgs(), max_tokens=10)[0] is None
