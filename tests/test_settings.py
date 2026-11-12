"""验证便携配置落盘、旧配置兼容和跨服务凭据隔离。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass import gateway, settings                 # noqa: E402
from smartclass.config import load_config                # noqa: E402


def _cfg(tmp_path, monkeypatch):
    for variable in ("SCLS_CONFIG", "SCLS_GATEWAY_URL", "SCLS_GATEWAY_KEY"):
        monkeypatch.delenv(variable, raising=False)
    return load_config(str(tmp_path / "smartclass.toml"))


def test_settings_preserve_toml_and_never_return_keys(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    path = Path(cfg.config_path)
    path.write_text('# 备注原样保留\n[custom]\nvalue = 9\n[asr]\nchunk_seconds = 42\n', encoding="utf8")
    snapshot = settings.save_settings(cfg, {
        "asr": {"url": "https://a.example/v1", "protocol": "transcriptions",
                "key": "secret-a", "model": "speech", "fallback_models": ["b", "b", "c"]},
        "vision": {"url": "https://v.example/v1", "model": "vision", "key": "secret-v"},
        "digest": {"url": "", "model": "summary"},
        "watch": {"courses": ["有机化学", "有机化学", "线性代数"]},
    })
    assert "secret-" not in repr(snapshot)
    assert snapshot["digest"]["has_key"] and snapshot["vision"]["has_key"]
    assert snapshot["asr"]["fallback_models"] == ["b", "c"]
    assert snapshot["watch"]["courses"] == ["有机化学", "线性代数"]
    assert "# 备注原样保留" in path.read_text(encoding="utf8")
    assert "[custom]\nvalue = 9" in path.read_text(encoding="utf8")
    loaded = load_config(str(path))
    assert settings.settings_snapshot(loaded) == snapshot
    assert loaded.asr.chunk_seconds == 42
    assert gateway.endpoint(loaded, "asr") == ("https://a.example/v1/audio/transcriptions", "secret-a")
    settings.save_settings(cfg, {"vision": {"url": "https://elsewhere.example/v1", "key": ""}})
    assert gateway.endpoint(cfg, "vision")[1] == ""
    settings.save_settings(cfg, {"digest": {"clear_key": True}})
    assert gateway.endpoint(cfg, "digest")[1] == ""
    settings.save_settings(cfg, {"asr": {"model": "newname", "key": ""}})
    assert cfg.asr.gateway_key == "secret-a"
    settings.save_settings(cfg, {"asr": {"clear_key": True}})
    assert cfg.asr.gateway_key == ""


def test_bad_input_or_interrupted_save_preserves_file(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    path = Path(cfg.config_path)
    settings.save_settings(cfg, {"asr": {"model": "old"}})
    original = path.read_bytes()
    for bad in ({"asr": {"url": "https://user:password@host"}},
                {"vision": {"key": "new", "clear_key": True}}, {"watch": {"courses": "课程"}}):
        with pytest.raises(ValueError):
            settings.save_settings(cfg, bad)
        assert path.read_bytes() == original
    def fail_replace(*_):
        raise PermissionError("busy")
    monkeypatch.setattr(settings.os, "replace", fail_replace)
    with pytest.raises(PermissionError):
        settings.save_settings(cfg, {"asr": {"model": "new"}})
    assert path.read_bytes() == original
    assert cfg.asr.model == "old"


def test_environment_url_cannot_reuse_disk_credentials(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    settings.save_settings(cfg, {"asr": {"url": "https://provider-a/v1", "key": "old"},
                                 "vision": {"key": "vision-old"}})
    monkeypatch.setenv("SCLS_GATEWAY_URL", "https://provider-b/v1")
    fresh = load_config(cfg.config_path)
    assert fresh.asr.gateway_key == "" and fresh.vision.gateway_key == ""
    monkeypatch.setenv("SCLS_GATEWAY_KEY", "new")
    assert gateway.endpoint(load_config(cfg.config_path), "asr")[1] == "new"


def test_switch_to_dashscope_keeps_existing_chat_services(tmp_path, monkeypatch):
    """桌面只更换语音服务时，继承旧网关的课件和笔记仍能使用原来的模型。"""
    cfg = _cfg(tmp_path, monkeypatch)
    old_url = "https://old.example/v1"
    settings.save_settings(cfg, {
        "asr": {"url": old_url, "key": "old-access", "model": "old-asr"},
        "vision": {"url": old_url, "model": "ocr"}, "digest": {"model": "notes"},
    })
    before = {name: gateway.endpoint(cfg, name) for name in ("vision", "digest")}
    new_url = "https://example.cn-beijing.maas.aliyuncs.com/api/v1"
    snapshot = settings.save_settings(cfg, {
        "asr": {"url": new_url, "key": "new-access", "protocol": "dashscope",
                "model": "qwen-audio-3.1-asr-flash"},
        "vision": {"url": old_url}, "digest": {"url": ""},
    })
    loaded = load_config(cfg.config_path)
    assert snapshot["asr"]["protocol"] == "dashscope"
    assert gateway.endpoint(loaded, "asr") == (
        new_url + "/services/aigc/multimodal-generation/generation", "new-access")
    assert {name: gateway.endpoint(loaded, name) for name in before} == before
    assert "old-access" not in repr(snapshot) and "new-access" not in repr(snapshot)


def test_dashscope_profile_uses_explicit_chat_endpoints(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    settings.save_settings(cfg, {"asr": {"url": "https://old.example/v1", "key": "old"},
                                 "digest": {"model": "old-notes"}})
    profile = _profile()
    profile["asr"].update(protocol="dashscope", url="https://speech.example/api/v1")
    profile["digest"].update(url="https://notes.example/v1", key="notes-key")
    path = tmp_path / "学习配置.json"
    path.write_text(json.dumps(profile), encoding="utf8")
    assert settings.import_profile(cfg, path)["asr"]["protocol"] == "dashscope"
    assert gateway.endpoint(cfg, "digest") == ("https://notes.example/v1/chat/completions", "notes-key")
    assert gateway.endpoint(cfg, "vision")[1] == ""


def test_reload_merges_independent_edits(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    other = load_config(cfg.config_path)
    settings.save_settings(cfg, {"watch": {"courses": ["一班"]}})
    settings.save_settings(other, {"digest": {"model": "latest"}})
    loaded = load_config(cfg.config_path)
    assert loaded.watch.courses == ["一班"] and loaded.digest.model == "latest"


def _profile():
    return {
        "format": "smartclass-profile-v1",
        "asr": {"url": "https://speech.example:8443/v1", "model": "speech", "key": "student"},
        "vision": {"url": "https://vision.example/v1", "model": "ocr"},
        "digest": {"url": "", "model": "notes"},
    }


def test_import_pins_private_ca_to_each_origin_and_clears_old_access(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    settings.save_settings(cfg, {"asr": {"url": "https://old.example/v1", "key": "old-a"},
                                 "vision": {"key": "old-v"}, "watch": {"courses": ["有机"]}})
    # 从依赖自带的公开 CA 包取一张合法证书，仅验证解析与请求参数，不联网。
    certs = Path(requests.certs.where()).read_text(encoding="ascii")
    pem = re.search(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", certs, re.S)[0]
    profile = _profile()
    profile["ca_pem"] = pem
    path = tmp_path / "学生配置.json"
    path.write_text(json.dumps(profile), encoding="utf8")
    snapshot = settings.import_profile(cfg, path)
    assert all(snapshot[name]["has_ca"] for name in ("asr", "vision", "digest"))
    assert "BEGIN CERTIFICATE" not in repr(snapshot) and "student" not in repr(snapshot)
    assert snapshot["watch"] == {"courses": ["有机"]}
    assert gateway.endpoint(cfg, "vision")[1] == "" and gateway.endpoint(cfg, "digest")[1] == "student"
    assert cfg.asr.gateway_key == "student" and load_config(cfg.config_path).tls.ca_pem.strip() == pem
    certfiles = []
    def post(url, **kwargs):
        assert kwargs["allow_redirects"] is False
        certfile = Path(kwargs["verify"])
        assert certfile.read_text(encoding="ascii").strip() == pem
        certfiles.append(certfile)
        class Redirect:
            status_code = 307
            text = ""
        return Redirect()
    monkeypatch.setattr(gateway.requests, "post", post)
    _, why = gateway.raw_chat(cfg, modality="digest", model="notes", messages=[], max_tokens=10)
    assert "307" in why and not certfiles[0].exists()
    settings.save_settings(cfg, {"vision": {"url": "https://speech.example:8443/v1"}})
    assert not settings.settings_snapshot(cfg)["vision"]["has_ca"]
    cfg = load_config(cfg.config_path)
    assert not gateway.trusted_ca(cfg, "vision", cfg.vision.gateway_url)
    assert gateway.trusted_ca(cfg, "asr", cfg.asr.gateway_url)
    assert not gateway.trusted_ca(cfg, "asr", "https://speech.example:8444/v1")
    del profile["ca_pem"]
    path.write_text(json.dumps(profile), encoding="utf8")
    assert not settings.import_profile(cfg, path)["asr"]["has_ca"]


def test_reject_profile_secrets_paths_invalid_tls_and_unexpected_fields(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    original = settings.save_settings(cfg, {"watch": {"courses": ["已保存"]}})
    original_bytes = Path(cfg.config_path).read_bytes()
    for changes in ({"site": {"storage_state": "local.json"}}, {"ca_path": "C:/unsafe.pem"},
                    {"ca_pem": "-----BEGIN PRIVATE KEY-----"}, {"ca_pem": "坏证书"},
                    {"ca_pem": "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"},
                    {"asr": {"url": "http://insecure/v1", "model": "speech"}},
                    {"asr": {"url": "https://s", "model": "s", "whisper_bin": "bad.exe"}},
                    {"digest": {}}, {"format": "bad"}):
        profile = {**_profile(), **changes}
        path = tmp_path / "sample.json"
        path.write_text(json.dumps(profile), encoding="utf8")
        with pytest.raises(ValueError):
            settings.import_profile(cfg, path)
        assert Path(cfg.config_path).read_bytes() == original_bytes
        assert settings.settings_snapshot(cfg) == original
