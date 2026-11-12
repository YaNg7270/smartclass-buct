"""原生任务表单和命令白名单，共用同一份字段描述。"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..discover import WEEKDAYS


@dataclass(frozen=True)
class Field:
    label: str
    kind: str = "text"
    choices: tuple[tuple[str, str], ...] = ()
    hint: str = ""


FIELDS = {
    "course": Field("课程名称", hint="例如 高等数学，留空表示全部课程"),
    "date": Field("上课日期", "date", hint="默认今天"),
    "date_from": Field("开始日期", "date"),
    "date_to": Field("结束日期", "date"),
    "time_from": Field("最早上课时间", "time"),
    "time_to": Field("最晚上课时间", "time"),
    "weekday": Field("星期", hint="例如 周一,周三"),
    "gap": Field("合并间隔（分钟）", "number"),
    "tracks": Field("录制机位", "select", (("vga", "课件屏幕"), ("video1", "教室摄像头"),
                                       ("vga,video1", "课件与教室"))),
    "unit": Field("输出粒度", "select", (("lecture", "按整课"), ("session", "按小节"),
                                      ("both", "整课和小节"))),
    "keep_video": Field("视频保留方式", "select", (("", "按设置"),
                       ("compressed", "压缩保存"), ("original", "保留原片"),
                       ("none", "移除视频、保留学习资料"))),
    "asr": Field("转写方式", "select", (("", "按设置"), ("gateway", "已配置的 API"),
                                     ("whisper", "本地 Whisper（需自行安装）"))),
    "jobs": Field("同时发送的请求数", "number"),
    "out": Field("输出目录", "directory", hint="默认资料库根目录"),
    "dir": Field("处理目录", "directory", hint="默认整个资料库，可选择课程或日期目录"),
    "run": Field("资料目录", "directory", hint="可选择课程或日期，也可填写小节目录"),
    "headed": Field("显示校园浏览器", "checkbox"),
    "dry_run": Field("预览执行计划", "checkbox"),
    "force": Field("忽略已有结果并重新处理", "checkbox"),
    "no_slides": Field("跳过课件识别", "checkbox"),
    "no_digest": Field("跳过作业和笔记", "checkbox"),
    "no_overview": Field("跳过课程总览", "checkbox"),
    "no_homework": Field("跳过作业汇总", "checkbox"),
    "no_notes": Field("跳过课堂笔记", "checkbox"),
    "mode": Field("识别范围", "select", (("assignment", "作业相关内容"), ("full", "完整课件"))),
    "stage": Field("测试接口", "select", (("asr,ocr", "转写与课件识别"),
                                      ("asr", "仅转写"), ("ocr", "仅课件识别"))),
    "asr_models": Field("转写模型", hint="用逗号分隔，默认按设置"),
    "ocr_models": Field("课件识别模型", hint="用逗号分隔，默认按设置"),
    "concurrency": Field("测试并发度", hint="例如 1,2,4"),
    "n": Field("每组样本数", "number", hint="默认 3"),
    "action": Field("计划操作", "select", (("show", "查看计划"), ("install", "创建或更新"),
                                      ("remove", "移除计划"))),
    "time": Field("每日执行时间", "time"),
    "task_name": Field("计划名称", hint="默认使用本应用的每日计划"),
}


@dataclass(frozen=True)
class Command:
    label: str
    description: str
    main: tuple[str, ...] = ()
    advanced: tuple[str, ...] = ()


_PERIOD = ("date_from", "date_to", "time_from", "time_to", "weekday", "gap")
_PROCESS = ("unit", "asr", "keep_video", "jobs", "no_slides", "no_digest", "force")
COMMANDS = {
    "run": Command("下载并整理课程", "下载所选课程的录播，生成逐字稿、作业和课堂笔记。",
                   ("course", "date"), _PERIOD + ("tracks", "out", "headed", "dry_run") + _PROCESS),
    "list": Command("查询课表", "从校园平台查询课时，同日相邻的小节按整课显示。",
                    ("course", "date"), _PERIOD + ("headed",)),
    "login": Command("登录校园平台", "在打开的校园平台窗口手动完成登录，随后回到本页查看结果。"),
    "resume": Command("续跑本地课程", "从已有课程视频继续处理，跳过已完成的步骤。",
                      ("dir",), ("dry_run",) + _PROCESS),
    "review": Command("生成复习资料", "将作业和笔记按讲次汇编，可直接阅读，也可放入 Obsidian。",
                      ("course", "no_overview"), ("dry_run", "force")),
    "daily": Command("执行每日任务", "按监控清单处理当天课程，也可指定课程和日期。",
                     ("course", "date"), ("time_from", "time_to", "weekday", "gap",
                                          "tracks", "out", "dry_run") + _PROCESS),
    "asr": Command("重新转写音频", "转写所选目录内的视频并生成带时间戳的文本。",
                   ("run", "asr"), ("force",)),
    "slides": Command("识别课件", "从视频中提取课件画面并识别文字。",
                      ("run", "mode"), ("force",)),
    "digest": Command("整理作业和笔记", "使用已有转写和课件文字重新归纳学习资料。",
                      ("run",), ("no_homework", "no_notes", "force")),
    "bench": Command("测试接口性能", "使用所选资料中的音频和画面，比较接口耗时与输出。",
                     ("run", "stage"), ("asr_models", "ocr_models", "concurrency", "n")),
    "schedule": Command("管理定时任务", "在 Windows 中查看或设置每日自动处理时间。",
                        ("action", "time"), ("task_name",)),
    "prune": Command("整理本地视频", "按保留方式处理所选目录下的视频。移除视频会删除相应文件。",
                     ("out", "keep_video"), ("force",)),
    "regroup": Command("合并旧版小节", "将旧版小节目录原地合并为整课。",
                       ("out",), ("no_digest",)),
    "migrate-layout": Command("迁移旧版目录", "将旧版课程目录迁移到当前布局，可先预览移动清单。",
                              ("out", "dry_run")),
}


def _directory(cfg: Config, value: str, *, may_create: bool) -> str:
    root = Path(cfg.paths.runs_root).expanduser().resolve()
    parsed = Path(value)
    if parsed.drive and parsed.drive.lower() != root.drive.lower():
        raise ValueError("请选择当前资料库中的目录。")
    candidate = (root / parsed).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("请选择当前资料库中的目录。")
    if any(p.startswith(("_", ".")) or ":" in p for p in candidate.relative_to(root).parts):
        raise ValueError("不能选择内部配置、登录或状态目录。")
    if not candidate.is_dir() and (candidate.exists() or not may_create):
        raise ValueError("所选目录不存在或不是目录。")
    return str(candidate)


def _value(key: str, raw, cfg: Config, verb: str) -> str:
    field = FIELDS[key]
    if field.kind == "checkbox":
        if not isinstance(raw, bool):
            raise ValueError(f"「{field.label}」需要布尔值。")
        return "1" if raw else ""
    if field.kind == "number" and type(raw) is int:
        raw = str(raw)
    if not isinstance(raw, str):
        raise ValueError(f"「{field.label}」格式不正确。")
    value = raw.strip()
    if not value:
        return ""
    if value.startswith("-") or any(ord(c) < 32 for c in value):
        raise ValueError(f"「{field.label}」包含无效字符。")
    if field.choices and value not in dict(field.choices):
        raise ValueError(f"请选择有效的「{field.label}」。")
    if field.kind == "directory":
        return _directory(cfg, value, may_create=key == "out" and verb in ("run", "daily"))
    if field.kind == "date":
        try:
            if dt.date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except ValueError:
            raise ValueError(f"「{field.label}」应为有效的 YYYY-MM-DD 日期。") from None
    if field.kind == "time" and not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise ValueError(f"「{field.label}」应为 HH:MM。")
    if field.kind == "number" and not re.fullmatch(r"[1-9][0-9]*", value):
        raise ValueError(f"「{field.label}」应为正整数。")
    if key in ("weekday", "concurrency"):
        parts = [p.strip().lower() for p in value.replace("，", ",").split(",")]
        if key == "weekday" and any(p not in WEEKDAYS for p in parts):
            raise ValueError("星期请使用 周一 到 周日，或 mon 到 sun，以逗号分隔。")
        if key == "concurrency" and any(not re.fullmatch(r"[1-9][0-9]*", p) for p in parts):
            raise ValueError("测试并发度应为以逗号分隔的正整数。")
        value = ",".join(parts)
    if key == "task_name" and value != cfg.schedule.task_name:
        if not re.fullmatch(r"smartclass-buct-[\w-]+", value):
            raise ValueError("自定义任务名需以 smartclass-buct- 开头，只能使用字母、数字、汉字、横线或下划线。")
    return value


def build_args(data: dict, cfg: Config):
    """只组装当前任务的已知字段；配置文件由启动器选择。"""
    if not isinstance(data, dict) or not isinstance(data.get("verb"), str):
        return "请选择运行任务。"
    verb = data["verb"]
    command = COMMANDS.get(verb)
    if not command:
        return "未知任务。"
    values = {}
    try:
        for key in command.main + command.advanced:
            if key in data:
                value = _value(key, data[key], cfg, verb)
                if value:
                    values[key] = value
    except (ValueError, OSError, RuntimeError) as exc:
        return str(exc)
    if values.get("date") and ("date_from" in values or "date_to" in values):
        return "单日与日期范围只能填写一种。"
    for start, end in (("date_from", "date_to"), ("time_from", "time_to")):
        if start in values and end in values and values[start] > values[end]:
            return f"「{FIELDS[end].label}」不能早于「{FIELDS[start].label}」。"
    if verb == "digest" and "no_homework" in values and "no_notes" in values:
        return "至少选择生成作业或笔记中的一项。"
    if "run" in command.main and "run" not in values:
        return "请选择处理的资料目录。"
    argv = []
    if verb == "schedule":
        action = values.pop("action", "show")
        argv.append(action)
        if action != "install":
            values.pop("time", None)
    for key, value in values.items():
        name = {"date_from": "--from", "date_to": "--to"}.get(key, "--" + key.replace("_", "-"))
        argv.append(name)
        if FIELDS[key].kind != "checkbox":
            argv.append(value)
    return verb, argv
