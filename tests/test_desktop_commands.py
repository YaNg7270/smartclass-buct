"""原生表单与真实 CLI 保持一致，界面测试只使用替身任务和临时资料库。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass.cli import build_parser  # noqa: E402
from smartclass.config import Config  # noqa: E402
from smartclass.desktop.commands import COMMANDS, build_args  # noqa: E402


def test_all_native_tasks_parse_as_cli_arguments(tmp_path):
    cfg = Config()
    cfg.paths.runs_root = str(tmp_path)
    (tmp_path / "化工实践").mkdir()
    samples = {
        "login": {}, "run": {"course": "物理化学（Ⅰ）", "date_from": "2026-09-01",
                            "date_to": "2026-09-17", "weekday": "周一，wed", "headed": True,
                            "unit": "both", "jobs": 2, "tracks": "vga,video1"},
        "list": {"date": "2026-09-10", "time_from": "08:30", "gap": "20"},
        "daily": {"course": "化工实践", "dry_run": True, "out": "新课程"},
        "resume": {"dir": "化工实践", "no_slides": True, "keep_video": "original"},
        "review": {"course": "化工实践", "no_overview": True},
        "asr": {"run": ".", "asr": "whisper"}, "slides": {"run": ".", "mode": "full"},
        "digest": {"run": ".", "no_notes": True},
        "bench": {"run": ".", "stage": "asr,ocr", "asr_models": "a,b", "ocr_models": "c",
                  "concurrency": "1, 3", "n": 2},
        "schedule": {"action": "install", "time": "22:10", "task_name": "smartclass-buct-测试"},
        "prune": {"out": ".", "keep_video": "compressed"},
        "regroup": {"out": ".", "no_digest": True}, "migrate-layout": {"dry_run": True},
    }
    assert set(samples) == set(COMMANDS)
    parser = build_parser()
    parsed = {}
    for verb, fields in samples.items():
        built = build_args({"verb": verb, **fields, "config": "别的配置"}, cfg)
        assert not isinstance(built, str), (verb, built)
        actual, argv = built
        assert actual == verb and "--config" not in argv
        parsed[verb] = parser.parse_args([verb, *argv])
    assert parsed["run"].course == "物理化学（Ⅰ）" and parsed["run"].weekday == "周一,wed"
    assert parsed["run"].jobs == 2 and parsed["run"].headed
    assert parsed["daily"].out == str(tmp_path / "新课程") and parsed["daily"].dry_run
    assert parsed["schedule"].action == "install" and parsed["schedule"].time == "22:10"
    assert parsed["resume"].dir == str(tmp_path / "化工实践") and parsed["resume"].no_slides
    assert parsed["bench"].concurrency == "1,3" and parsed["bench"].n == 2
    assert parsed["slides"].mode == "full" and parsed["asr"].asr == "whisper"


def test_native_form_rejects_invalid_or_unsafe_values(tmp_path):
    cfg = Config()
    cfg.paths.runs_root = str(tmp_path)
    for fields in (
        {"verb": "serve"}, {"verb": "list", "course": "--config"},
        {"verb": "run", "date": "2026-02-30"}, {"verb": "run", "jobs": -2},
        {"verb": "run", "date": "2026-09-09", "date_to": "2026-09-12"},
        {"verb": "list", "date_from": "2026-09-12", "date_to": "2026-09-09"},
        {"verb": "list", "weekday": "不确定"}, {"verb": "daily", "time_from": "24:15"},
        {"verb": "run", "tracks": "vga,../../other"}, {"verb": "run", "out": "../new"},
        {"verb": "prune", "out": str(tmp_path.parent)}, {"verb": "resume", "dir": "不存在"},
        {"verb": "asr", "run": "_profile"}, {"verb": "bench", "concurrency": "1,0"},
        {"verb": "digest", "run": ".", "no_notes": True, "no_homework": True},
        {"verb": "schedule", "action": "remove", "task_name": "别的软件"},
    ):
        assert isinstance(build_args(fields, cfg), str), fields
    assert build_args({"verb": "schedule"}, cfg) == ("schedule", ["show"])
    assert build_args({"verb": "resume"}, cfg) == ("resume", [])


def test_native_task_panel_uses_controls_for_login_review_and_cancel(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from smartclass.desktop.tasks import TaskPanel

    class Jobs:
        def __init__(self):
            self.calls = []
            self.active = False

        def start(self, verb, args):
            self.calls.append((verb, args))
            self.active = True
            return {"ok": True}

        def status(self):
            return {"verb": self.calls[-1][0] if self.calls else "", "running": self.active,
                    "log_tail": ["本地任务记录"], "exit_code": None if self.active else 130}

        def cancel(self):
            self.active = False
            return {"ok": True}

    app = QApplication.instance() or QApplication([])
    cfg = Config()
    cfg.paths.runs_root = str(tmp_path)
    jobs = Jobs()
    panel = TaskPanel(cfg, jobs)
    try:
        panel.resize(820, 580)
        panel.show()
        assert panel.selector.findData("bench") < 0 and panel.selector.findData("list") >= 0
        panel.more_tools.click()
        assert panel.selector.findData("bench") >= 0 and panel.selector.findData("list") < 0
        for verb, command in COMMANDS.items():
            panel.select_task(verb)
            assert tuple(panel.editors) == command.main + command.advanced
        panel.select_task("login")
        assert not jobs.calls
        panel.start_button.click()
        assert jobs.calls == [("login", [])] and not panel.start_button.isEnabled()
        assert not panel.progress.isHidden()
        assert panel.log_view.toPlainText() == "本地任务记录"
        panel.cancel_button.click()
        assert panel.start_button.isEnabled() and panel.progress.isHidden() and "已取消" in panel.status_label.text()
        panel.select_task("review", course="化工实践", no_overview=True)
        panel.start_button.click()
        assert jobs.calls[-1] == ("review", ["--course", "化工实践", "--no-overview"])
        panel.cancel_button.click()
        panel.select_task("prune", out="..", keep_video="none")
        panel.start_button.click()
        assert "资料库" in panel.feedback.text() and len(jobs.calls) == 2
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
        panel.select_task("prune", out=".", keep_video="none")
        panel.start_button.click()
        assert len(jobs.calls) == 2
        panel.refresh({"verb": "daily", "exit_code": 2, "running": False, "log_tail": ["登录已失效"]})
        assert not panel.login_again.isHidden()
        panel.login_again.click()
        assert jobs.calls[-1] == ("login", [])
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_task_form_background_stays_opaque_when_navigating(tmp_path, monkeypatch):
    """工作台切到任务中心时，表单背景从第一帧起就要盖住下层像素。"""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, QRect, QSize
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QPushButton
    from smartclass.desktop.app import ASSETS, MainWindow

    app = QApplication.instance() or QApplication([])
    cfg = Config()
    cfg.paths.runs_root = str(tmp_path)
    window = MainWindow(cfg)
    # 以鲜明底色代表旧页的残留像素，让离屏渲染也能检出内容区整体半透明。
    window.centralWidget().setObjectName("testUnderlay")
    window.setStyleSheet((ASSETS / "theme.qss").read_text(encoding="utf-8")
                         + "\nQWidget#testUnderlay { background: #ff00ff; }")

    def assert_opaque_form():
        assert window.tasks.isVisible() and not window.home.isVisible()
        view = window.tasks.scroll.viewport()
        point = QPoint(4, view.height() - 8)
        size = QSize(1, 1)
        expected = view.grab(QRect(point, size)).toImage().pixelColor(0, 0)
        composed = window.grab(QRect(view.mapTo(window, point), size)).toImage()
        assert composed.pixelColor(0, 0) == expected

    try:
        window.show()
        app.processEvents()
        window.group.button(2).click()
        assert_opaque_form()
        QTest.qWait(30)
        assert_opaque_form()
        # 动画尚未结束即返回工作台，再从首页快捷入口进入同一张表单。
        window.group.button(0).click()
        next(b for b in window.home.findChildren(QPushButton) if b.text() == "选择课程").click()
        assert_opaque_form()
        QTest.qWait(250)
        assert_opaque_form()
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
