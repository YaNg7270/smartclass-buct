"""桌面 API 设置：只返回凭据状态，按字段修改用户已有的 TOML。"""
from __future__ import annotations

import json
import os
import re
import ssl
import tempfile
from pathlib import Path

from filelock import FileLock, Timeout
import tomlkit
from tomlkit.exceptions import TOMLKitError

from . import gateway
from .config import Config, load_config

MODALITIES = ("asr", "vision", "digest")
_CERTIFICATE = re.compile(r"-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\r\n\t ]+-----END CERTIFICATE-----")


def settings_snapshot(cfg: Config) -> dict:
    result = {}
    for name in MODALITIES:
        sec = getattr(cfg, name)
        try:
            endpoint, key = gateway.endpoint(cfg, name)
            has_ca = bool(gateway.trusted_ca(cfg, name, endpoint))
            url = sec.gateway_url
        except ValueError:
            # 允许从界面修正历史配置中的非法地址，避免显示其中内嵌的凭据。
            url, key, has_ca = "", sec.gateway_key, False
        result[name] = {
            "url": url, "model": sec.model, "fallback_models": list(sec.fallback_models),
            "protocol": cfg.asr.protocol if name == "asr" else "chat",
            "has_key": bool(key or sec.gateway_key), "has_ca": has_ca,
        }
    result["watch"] = {"courses": list(cfg.watch.courses)}
    return result


def _text(value, field: str) -> str:
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{field} 须为单行文本。")
    if "PRIVATE KEY-----" in value:
        raise ValueError("模型配置不能包含私钥。")
    return value.strip()


def _items(value, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} 须为列表。")
    values = (_text(item, field) for item in value)
    return list(dict.fromkeys(item for item in values if item))


def _updates(payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) - {*MODALITIES, "watch"}:
        raise ValueError("不支持的设置。")
    result = {}
    for name, fields in payload.items():
        if not isinstance(fields, dict):
            raise ValueError(f"{name} 设置须为对象。")
        changes = {}
        if name == "watch":
            if set(fields) - {"courses"}:
                raise ValueError("不支持的课程设置。")
            if "courses" in fields:
                changes["courses"] = _items(fields["courses"], "课程名")
        else:
            if set(fields) - {"url", "model", "protocol", "fallback_models",
                              "key", "clear_key", "has_key", "has_ca"}:
                raise ValueError("不支持的 API 设置字段。")
            if "url" in fields:
                changes["gateway_url"] = _text(fields["url"], "API 地址")
                gateway.origin(changes["gateway_url"])
            if "protocol" in fields:
                allowed = ("chat", "transcriptions", "dashscope") if name == "asr" else ("chat",)
                if fields["protocol"] not in allowed:
                    raise ValueError("请选择当前任务支持的 API 协议。")
                if name == "asr":
                    changes["protocol"] = fields["protocol"]
            if "model" in fields:
                changes["model"] = _text(fields["model"], "模型名")
            if "fallback_models" in fields:
                changes["fallback_models"] = _items(fields["fallback_models"], "备用模型")
            clear = fields.get("clear_key", False)
            key = _text(fields.get("key", ""), "密钥")
            if not isinstance(clear, bool) or (clear and key):
                raise ValueError("清除密钥与填写新密钥不能同时使用。")
            if clear or key:
                changes["gateway_key"] = key
                if name != "asr":
                    changes["inherit_key"] = False
        result[name] = changes
    return result


def _origins(doc) -> dict:
    shared = doc.get("asr", {}).get("gateway_url", "")
    result = {}
    for name in MODALITIES:
        url = doc.get(name, {}).get("gateway_url", "") or shared
        try:
            result[name] = gateway.origin(url)
        except ValueError:
            result[name] = None
    return result


def save_settings(cfg: Config, payload: dict) -> dict:
    """基于最新文件合并补丁，写入成功后刷新界面所持的配置。"""
    return _persist(cfg, _updates(payload))


def _keep_chat_services(doc, changes: dict, before: dict) -> None:
    """旧设置常由三个阶段共用一个网关。只切换到百炼 ASR 时保留原有聊天服务。"""
    asr = changes.get("asr", {})
    old = doc.get("asr", {})
    if asr.get("protocol") != "dashscope" or old.get("protocol") == "dashscope" or not before["asr"]:
        return
    for name in ("vision", "digest"):
        previous = doc.get(name, {})
        if not previous.get("model") and not previous.get("fallback_models"):
            continue
        edit = changes.setdefault(name, {})
        url = previous.get("gateway_url", "")
        if edit.get("gateway_url", url) != url:  # 用户已为该阶段选了新地址
            continue
        if not url:
            edit["gateway_url"] = old["gateway_url"]
        if (before[name] == before["asr"] and not previous.get("gateway_key")
                and previous.get("inherit_key", True) and old.get("gateway_key")):
            edit.setdefault("gateway_key", old["gateway_key"])


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("学生配置含重复字段。")
        result[key] = value
    return result


def _certificate(value) -> str:
    if not isinstance(value, str):
        raise ValueError("ca_pem 须为 PEM 证书文本。")
    pem = value.strip()
    if not pem:
        return ""
    if _CERTIFICATE.sub("", pem).strip() or not pem.isascii():
        raise ValueError("ca_pem 只接受 PEM 证书，不能包含私钥、路径或其它内容。")
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        for block in _CERTIFICATE.findall(pem):
            context.load_verify_locations(cadata=block)
    except (ValueError, ssl.SSLError):
        raise ValueError("无法解析学生配置中的 PEM 证书。") from None
    return pem + "\n"


def import_profile(cfg: Config, path: str | Path) -> dict:
    """从用户选定的 JSON 导入模型设置；导入前校验全部内容。"""
    with Path(path).open("rb") as src:
        raw = src.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024 or b"PRIVATE KEY-----" in raw:
        raise ValueError("学生配置过大或包含私钥。")
    try:
        profile = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_keys)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("学生配置须为 UTF-8 JSON 文件。") from None
    if (not isinstance(profile, dict) or profile.get("format") != "smartclass-profile-v1"
            or set(profile) - {*MODALITIES, "format", "ca_pem"}):
        raise ValueError("学生配置的版本或字段不受支持。")
    sections = {}
    for name in MODALITIES:
        fields = profile.get(name)
        if (not isinstance(fields, dict) or set(fields) - {"url", "key", "protocol", "model", "fallback_models"}
                or not _text(fields.get("model", ""), "模型名")):
            raise ValueError(f"学生配置中 {name} 的模型设置缺失或含不支持的字段。")
        sections[name] = {"protocol": "chat", "fallback_models": [], "url": "", **fields}
    changes = _updates(sections)
    for name in MODALITIES:
        section = changes[name]
        url = section["gateway_url"] or changes["asr"]["gateway_url"]
        if not gateway.origin(url).startswith("https://"):
            raise ValueError("学生配置的三个服务都须使用 HTTPS 地址。")
        if "gateway_key" not in section:
            section["gateway_key"] = ""
            if name != "asr":
                section["inherit_key"] = True
    changes["asr"]["provider"] = "gateway"
    return _persist(cfg, changes, ca_pem=_certificate(profile.get("ca_pem", "")))


def _persist(cfg: Config, changes: dict, ca_pem: str | None = None) -> dict:
    if not cfg.config_path:
        raise ValueError("没有选定配置文件，请重新打开应用。")
    path = Path(cfg.config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock = FileLock(str(path) + ".lock", timeout=5)
        with lock:
            doc = tomlkit.document()
            if path.is_file():
                with path.open(encoding="utf-8", newline="") as src:
                    doc = tomlkit.parse(src.read())
            before = _origins(doc)
            if ca_pem is None:  # 导入完整学习配置时，按导入文件指定的地址和凭据生效
                _keep_chat_services(doc, changes, before)
            for name, values in changes.items():
                if name not in doc:
                    doc[name] = tomlkit.table()
                for field, value in values.items():
                    if name == "asr" and field == "gateway_url":
                        if os.environ.get("SCLS_GATEWAY_URL"):
                            if value != cfg.asr.gateway_url:
                                raise ValueError("环境变量 SCLS_GATEWAY_URL 正在覆盖转写地址。")
                            continue
                        if (os.environ.get("SCLS_GATEWAY_KEY") and cfg.asr.gateway_url
                                and gateway.origin(value) != gateway.origin(cfg.asr.gateway_url)):
                            raise ValueError("请先重设环境变量 SCLS_GATEWAY_KEY，再切换转写服务。")
                    if name == "asr" and field == "gateway_key" and os.environ.get("SCLS_GATEWAY_KEY"):
                        raise ValueError("环境变量 SCLS_GATEWAY_KEY 正在覆盖转写密钥。")
                    doc[name][field] = value
            after = _origins(doc)
            for name in MODALITIES:
                if (before[name] != "" and before[name] != after[name] and doc.get(name, {}).get("gateway_key")
                        and "gateway_key" not in changes.get(name, {})):
                    doc[name]["gateway_key"] = ""
            if ca_pem is not None and (ca_pem or "tls" in doc):
                if "tls" not in doc:
                    doc["tls"] = tomlkit.table()
                doc["tls"]["origins"] = after if ca_pem else {}
                doc["tls"]["ca_pem"] = ca_pem
            elif "tls" in doc:
                bindings = doc["tls"].get("origins", {})
                for name in list(bindings):
                    if bindings[name] != after.get(name):
                        del bindings[name]
                if not bindings:
                    doc["tls"]["ca_pem"] = ""
            descriptor, temp_name = tempfile.mkstemp(dir=path.parent)
            temp = Path(temp_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as out:
                    out.write(tomlkit.dumps(doc))
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temp, path)
            finally:
                temp.unlink(missing_ok=True)
            updated = load_config(str(path))
            for name in (*MODALITIES, "watch", "tls"):
                setattr(cfg, name, getattr(updated, name))
    except Timeout as exc:
        raise OSError("另一个窗口正在保存配置，请稍后重试。") from exc
    except TOMLKitError:
        raise ValueError("配置文件格式有误，无法保存。") from None
    return settings_snapshot(cfg)
