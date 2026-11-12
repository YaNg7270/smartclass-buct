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


def test_dashscope_posts_one_mp3_and_uses_word_timestamps(monkeypatch, tmp_path):
    """百炼的非流式请求携带原始音频，返回的长句在真实词起点分段。"""
    cfg = _cfg("qwen-audio-3.1-asr-flash")
    cfg.asr.protocol = "dashscope"
    cfg.asr.gateway_url = "https://workspace.example/api/v1/"
    cfg.asr.gateway_key = "test-key"
    path = tmp_path / "录音.mp3"
    path.write_bytes(b"mp3")
    calls = []
    phrase = "第一段。Next week，再看实验。"

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResp(body={"output": {"text": phrase, "sentence": {
            "text": phrase, "begin_time": 1650, "sentence_end": True,
            "words": [{"text": "第一段", "begin_time": 1650, "punctuation": "。"},
                      {"text": "Next", "begin_time": 35010},
                      {"text": " week", "begin_time": 36000, "punctuation": "，"},
                      {"text": "再看实验", "begin_time": 36050, "punctuation": "。"}],
        }}})

    monkeypatch.setattr(gateway.requests, "post", post)
    result = gateway.raw_chat(cfg, modality="asr", model=cfg.asr.model, messages=[],
                              max_tokens=100, timeout=9, audio_file=path)
    assert result == ("[00:01] 第一段。\n[00:35] Next week，再看实验。", "")
    assert len(calls) == 1
    url, params = calls[0]
    assert url == "https://workspace.example/api/v1/services/aigc/multimodal-generation/generation"
    assert params == {
        "headers": {"Authorization": "Bearer test-key", "X-DashScope-SSE": "disable"},
        "timeout": 9, "allow_redirects": False,
        "json": {"model": cfg.asr.model, "input": {"messages": [{"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": "data:audio/mpeg;base64,bXAz"}},
        ]}]}, "parameters": {"format": "mp3", "sample_rate": "16000"}},
    }


def test_dashscope_accepts_full_endpoint_and_preserves_other_routes():
    cfg = _cfg()
    cfg.asr.protocol = "dashscope"
    cfg.asr.gateway_key = "asr-key"
    host = "https://workspace.example"
    url = host + "/api/v1/services/aigc/multimodal-generation/generation"
    for base in (host, host + "/api/v1", url, url + "/"):
        cfg.asr.gateway_url = base
        assert gateway.endpoint(cfg, "asr") == (url, "asr-key")
        assert gateway.endpoint(cfg, "vision") == (host + "/compatible-mode/v1/chat/completions", "asr-key")
    cfg.vision.gateway_url = "https://vision.example/v1"
    assert gateway.endpoint(cfg, "vision") == ("https://vision.example/v1/chat/completions", "")
    cfg.asr.gateway_url = host + "/compatible-mode/v1"
    with pytest.raises(ValueError, match="DashScope"):
        gateway.endpoint(cfg, "asr")


def test_dashscope_keeps_full_text_when_only_last_sentence_is_timed():
    """单数 sentence 可能对应末句。即使有同文重句，也要保留完整的累计正文。"""
    text = "举手。等一下。举手。"
    output = {"text": text, "sentence": {"text": "举手。", "begin_time": 64500}}
    assert gateway._dashscope_result(FakeResp(body={"output": output})) == (
        "举手。等一下。\n[01:04] 举手。", "")

    output["sentences"] = [{"text": "举手。", "begin_time": 0}, {"text": "等一下。", "begin_time": 3100},
                           {"text": "举手。", "begin_time": 64500}]
    assert gateway._dashscope_result(FakeResp(body={"output": output})) == (
        "[00:00] 举手。\n[00:03] 等一下。\n[01:04] 举手。", "")


def test_dashscope_word_alignment_keeps_normalized_text():
    """无标点长句也应有导航点；词表与规范化正文分歧后的同文词不可用于定位。"""
    phrase = "看这里45度和45度"
    sentence = {"text": phrase, "begin_time": 0, "words": [
        {"text": "看", "begin_time": 0}, {"text": "这里", "begin_time": 45000},
        {"text": "四十五", "begin_time": 60000}, {"text": "度", "begin_time": 90000},
    ]}
    output = {"text": phrase, "sentence": sentence}
    assert gateway._dashscope_result(FakeResp(body={"output": output})) == (
        "[00:00] 看\n[00:45] 这里45度和45度", "")


def test_dashscope_distinguishes_silence_empty_response_and_failure():
    """只接受百炼的明确无词终态；鉴权错误、半截响应和空文本都不能缓存为静音。"""
    silent = {"code": "CLIENT_ERROR", "message": "ASR_RESPONSE_HAVE_NO_WORDS"}
    assert gateway._dashscope_result(FakeResp(status=400, body=silent)) == ("SILENCE", "")
    bad_responses = [
        FakeResp(status=403, body=silent),
        FakeResp(status=400, body={"code": "CLIENT_ERROR", "message": "private audio or key"}),
        FakeResp(status=200, body={"code": "CLIENT_ERROR", "message": "private audio or key"}),
        FakeResp(body=ValueError("private audio or key")), FakeResp(body={"output": {"text": ""}}),
        FakeResp(body={"output": {"text": "课堂", "sentence": {"text": "课堂"}}}),
        FakeResp(body={"output": {"text": "课堂", "sentence": {
            "text": "课堂", "begin_time": 0, "sentence_end": False}}}),
    ]
    for response in bad_responses:
        result, why = gateway._dashscope_result(response)
        assert result is None and why and "private" not in why
