"""原生任务面板。交互在 Qt 主线程，处理任务沿用独立 CLI 子进程。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDate, QPoint, QSignalBlocker, Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QCalendarWidget, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
    QScrollArea, QSplitter, QToolButton, QVBoxLayout, QWidget,
    QWidgetAction,
)

from .commands import COMMANDS, FIELDS, build_args
from .paper import button, icon, label


_STUDY_TASKS = ("run", "list", "login", "review", "resume", "daily")


def _calendar(edit: QLineEdit) -> None:
    menu = QMenu(edit)
    calendar = QCalendarWidget(menu)
    date = QDate.fromString(edit.text(), Qt.DateFormat.ISODate)
    calendar.setSelectedDate(date if date.isValid() else QDate.currentDate())
    action = QWidgetAction(menu)
    action.setDefaultWidget(calendar)
    menu.addAction(action)

    def choose(selected: QDate) -> None:
        edit.setText(selected.toString(Qt.DateFormat.ISODate))
        menu.close()

    calendar.clicked.connect(choose)
    calendar.activated.connect(choose)
    menu.exec(edit.mapToGlobal(QPoint(0, edit.height())))
    menu.deleteLater()


class TaskPanel(QWidget):
    """可从首页或课程页预选任务，实际执行须点击任务按钮。"""

    def __init__(self, cfg, jobs, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.cfg = cfg
        self.jobs = jobs
        self.editors = {}
        self._rows = {}
        self._last_log = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(16)
        layout.addWidget(label("任务中心", "pageTitle"))
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter, 1)

        form_panel = QWidget()
        form_layout = QVBoxLayout(form_panel)
        form_layout.setSpacing(12)
        form_layout.addWidget(label("选择任务", "section"))
        self.selector = QComboBox()
        self.selector.setAccessibleName("选择任务")
        self.more_tools = QToolButton()
        self.more_tools.setText("分项与维护工具")
        self.more_tools.setCheckable(True)
        self.more_tools.setToolTip("显示单独转写、课件识别、定时计划、测速及资料库维护")
        task_row = QHBoxLayout()
        task_row.addWidget(self.selector, 1)
        task_row.addWidget(self.more_tools)
        form_layout.addLayout(task_row)
        self.description = label("", "muted")
        form_layout.addWidget(self.description)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setMinimumHeight(80)
        form_layout.addWidget(self.scroll, 1)
        self.feedback = label("", "warning")
        self.feedback.setVisible(False)
        form_layout.addWidget(self.feedback)
        buttons = QHBoxLayout()
        self.start_button = button("", self._start, "play", "primary")
        self.cancel_button = button("取消任务", self._cancel)
        self.cancel_button.setEnabled(False)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        form_layout.addLayout(buttons)
        self.splitter.addWidget(form_panel)

        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setSpacing(10)
        log_layout.addWidget(label("执行记录", "section"))
        self.status_label = label("就绪", "status")
        log_layout.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(5)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("任务正在执行")
        self.progress.hide()
        log_layout.addWidget(self.progress)
        self.login_again = button("重新登录校园平台", self._relogin, "key-round")
        self.login_again.hide()
        log_layout.addWidget(self.login_again, 0, Qt.AlignmentFlag.AlignLeft)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setAccessibleName("执行日志")
        self.log_view.setPlaceholderText("运行任务后，进度和结果会显示在这里。")
        self.log_view.setMinimumHeight(60)
        log_layout.addWidget(self.log_view, 1)
        self.splitter.addWidget(log_panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.selector.currentIndexChanged.connect(self._rebuild)
        self.more_tools.toggled.connect(self._task_choices)
        self._task_choices(False)

    def _task_choices(self, extended):
        current = self.selector.currentData()
        choices = [verb for verb in COMMANDS if verb not in _STUDY_TASKS] if extended else _STUDY_TASKS
        self.more_tools.setText("返回常用任务" if extended else "分项与维护工具")
        with QSignalBlocker(self.selector):
            self.selector.clear()
            for verb in choices:
                self.selector.addItem(COMMANDS[verb].label, verb)
            self.selector.setCurrentIndex(max(0, self.selector.findData(current)) if current else 0)
        if current != self.selector.currentData() or not self.editors:
            self._rebuild()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        direction = Qt.Orientation.Horizontal if event.size().width() >= 760 else Qt.Orientation.Vertical
        if self.splitter.orientation() != direction:
            self.splitter.setOrientation(direction)
            self.splitter.setSizes([380, 190] if direction == Qt.Orientation.Vertical else [480, 320])

    def select_task(self, verb: str, **fields) -> None:
        if verb in COMMANDS:
            self.more_tools.setChecked(verb not in _STUDY_TASKS)
        index = self.selector.findData(verb)
        if index < 0:
            raise ValueError(f"未知任务：{verb}")
        if index == self.selector.currentIndex():
            self._rebuild()
        else:
            self.selector.setCurrentIndex(index)
        for key, value in fields.items():
            edit = self.editors.get(key)
            if isinstance(edit, QComboBox):
                pos = edit.findData(value)
                if pos >= 0:
                    edit.setCurrentIndex(pos)
            elif isinstance(edit, QCheckBox):
                edit.setChecked(bool(value))
            elif isinstance(edit, QLineEdit):
                edit.setText(str(value))
        if any(key in COMMANDS[verb].advanced for key in fields):
            self.advanced_toggle.setChecked(True)

    def _rebuild(self):
        verb = self.selector.currentData()
        command = COMMANDS[verb]
        self.description.setText(command.description)
        self.start_button.setText(command.label)
        self.feedback.hide()
        self.editors.clear()
        self._rows.clear()
        pane = QWidget()
        body = QVBoxLayout(pane)
        body.setContentsMargins(0, 2, 8, 4)
        body.setSpacing(12)
        main_form = QFormLayout()
        self._add_fields(main_form, command.main)
        body.addLayout(main_form)
        if command.advanced:
            self.advanced_toggle = QToolButton()
            self.advanced_toggle.setText("高级选项")
            self.advanced_toggle.setCheckable(True)
            self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
            body.addWidget(self.advanced_toggle, 0, Qt.AlignmentFlag.AlignLeft)
            advanced = QWidget()
            advanced_form = QFormLayout(advanced)
            advanced_form.setContentsMargins(0, 0, 0, 0)
            self._add_fields(advanced_form, command.advanced)
            advanced.hide()
            self.advanced_toggle.toggled.connect(advanced.setVisible)
            self.advanced_toggle.toggled.connect(self._expand_arrow)
            body.addWidget(advanced)
        body.addStretch()
        self.scroll.setWidget(pane)
        if verb == "schedule":
            self.editors["action"].currentIndexChanged.connect(self._schedule_action)
            self._schedule_action()

    def _expand_arrow(self, opened):
        self.advanced_toggle.setArrowType(Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow)

    def _schedule_action(self):
        form, row = self._rows["time"]
        action = self.editors["action"].currentData()
        form.setRowVisible(row, action == "install")
        self.start_button.setText({"show": "查看计划", "install": "保存计划", "remove": "移除计划"}[action])

    def _add_fields(self, form, keys):
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setSpacing(8)
        for key in keys:
            field = FIELDS[key]
            if field.kind == "checkbox":
                edit = QCheckBox(field.label)
                form.addRow(edit)
            elif field.kind == "select":
                edit = QComboBox()
                for value, title in field.choices:
                    edit.addItem(title, value)
                form.addRow(field.label, edit)
            else:
                edit = QLineEdit()
                edit.setClearButtonEnabled(True)
                hint = field.hint
                if field.kind == "number":
                    edit.setValidator(QIntValidator(1, 2_147_483_647, edit))
                if field.kind == "time":
                    hint = self.cfg.schedule.time if key == "time" else "HH:MM"
                if field.kind == "date":
                    hint = hint or "YYYY-MM-DD"
                    choose = edit.addAction(icon("calendar-days"), QLineEdit.ActionPosition.TrailingPosition)
                    choose.setToolTip("选择日期")
                    choose.triggered.connect(lambda _=False, target=edit: _calendar(target))
                if key == "task_name":
                    hint = self.cfg.schedule.task_name
                edit.setPlaceholderText(hint)
                if field.kind == "directory":
                    row = QHBoxLayout()
                    row.addWidget(edit, 1)
                    choose = QToolButton()
                    choose.setIcon(icon("folder-open"))
                    choose.setAccessibleName("选择文件夹")
                    choose.setToolTip("选择资料库中的文件夹")
                    choose.clicked.connect(lambda _=False, target=edit: self._pick_folder(target))
                    row.addWidget(choose)
                    form.addRow(field.label, row)
                else:
                    form.addRow(field.label, edit)
            self.editors[key] = edit
            edit.setAccessibleName(field.label)
            self._rows[key] = form, form.rowCount() - 1

    def _pick_folder(self, edit):
        root = Path(self.cfg.paths.runs_root).expanduser()
        start = root / edit.text() if edit.text() else root
        chosen = QFileDialog.getExistingDirectory(self, "选择资料目录", str(start if start.is_dir() else root))
        if chosen:
            edit.setText(chosen)

    def _message(self, text: str):
        self.feedback.setText(text)
        self.feedback.setVisible(bool(text))

    def _start(self):
        verb = self.selector.currentData()
        data = {"verb": verb}
        for key, edit in self.editors.items():
            if isinstance(edit, QCheckBox):
                data[key] = edit.isChecked()
            elif isinstance(edit, QComboBox):
                data[key] = edit.currentData()
            else:
                data[key] = edit.text()
        built = build_args(data, self.cfg)
        if isinstance(built, str):
            self._message(built)
            return
        if not self._confirm(data):
            return
        try:
            result = self.jobs.start(*built)
        except OSError as exc:
            self._message(f"无法启动任务：{exc}")
            return
        self._message("" if result.get("ok") else result.get("error", "任务启动失败。"))
        self.refresh(self.jobs.status())

    def _confirm(self, data: dict) -> bool:
        verb = data["verb"]
        message = ""
        if data.get("dry_run"):
            return True
        if verb == "schedule" and data["action"] == "remove":
            message = f"移除定时任务「{data.get('task_name') or self.cfg.schedule.task_name}」？"
        elif verb in ("prune", "run", "daily", "resume") and (
                data.get("keep_video") or self.cfg.retention.keep_video) == "none":
            scope = data.get("dir") or data.get("out") or self.cfg.paths.runs_root
            message = f"完成处理后将移除符合条件的视频文件。继续处理「{scope}」？"
        elif verb in ("regroup", "migrate-layout"):
            scope = data.get("out") or self.cfg.paths.runs_root
            message = f"将在「{scope}」中原地调整已有资料的目录布局。继续操作？"
        elif data.get("force"):
            message = "这项操作会重新生成并覆盖已有结果。继续操作？"
        if not message:
            return True
        buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        return QMessageBox.question(self, "确认任务", message, buttons,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def _cancel(self):
        result = self.jobs.cancel()
        self._message("正在取消任务。" if result.get("ok") else result.get("error", "取消失败。"))
        self.refresh(self.jobs.status())

    def _relogin(self):
        self.select_task("login")
        self._start()

    def refresh(self, status: dict):
        running = bool(status.get("running"))
        self.start_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.progress.setVisible(running)
        verb = status.get("verb")
        title = COMMANDS[verb].label if verb in COMMANDS else ""
        code = status.get("exit_code")
        if running:
            state = "运行中"
        elif code is None:
            state = "就绪"
        else:
            state = {0: "完成", 1: "未能完成，请查看下方记录，调整课程、日期或相关设置后重试。",
                     2: "登录已过期，请重新登录后再运行这项任务。",
                     3: "其他任务正在使用资料库，等它结束后再试。",
                     130: "已取消"}.get(code, f"退出码 {code}")
        lines = status.get("log_tail") or []
        expired = code == 2 or (code == 1 and any("登录已失效" in line or "需要重新登录" in line
                                                for line in lines[-12:]))
        self.login_again.setVisible(not running and expired)
        self.status_label.setText(f"{title} · {state}" if title else state)
        marker = (status.get("log_file"), lines)
        if marker != self._last_log:
            self._last_log = marker
            scroll = self.log_view.verticalScrollBar()
            before = scroll.value()
            follow = before >= scroll.maximum() - 1
            self.log_view.setPlainText("\n".join(lines))
            scroll.setValue(scroll.maximum() if follow else before)
