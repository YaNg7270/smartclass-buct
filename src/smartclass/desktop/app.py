"""SmartClass 课堂手记的原生主窗口。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPropertyAnimation, QSize, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout,
    QMainWindow, QMessageBox, QStackedWidget, QStyle, QVBoxLayout, QWidget,
)

from .. import catalog
from ..config import load_config, runs_root
from ..state import login_needed
from ..web.jobs import JobManager
from .library import Library
from .paper import ASSETS, LecturePlate, button, icon, label, scroll_page
from .preferences import Preferences
from .tasks import TaskPanel


class Home(QWidget):
    def __init__(self, window):
        super().__init__()
        self.setObjectName("page")
        self.window = window
        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 24, 30, 26)
        outer.setSpacing(16)
        dateline = QHBoxLayout()
        dateline.addWidget(label("北化智慧教室", "kicker"))
        dateline.addStretch()
        today = date.today()
        calendar = label(f"{today.year} 年 {today.month} 月 {today.day} 日   周{'一二三四五六日'[today.weekday()]}", "small")
        calendar.setWordWrap(False)
        dateline.addWidget(calendar)
        outer.addLayout(dateline)
        hero = QFrame()
        hero.setObjectName("hero")
        band = QHBoxLayout(hero)
        band.setContentsMargins(25, 20, 15, 20)
        intro = QVBoxLayout()
        intro.setSpacing(9)
        intro.addWidget(label("课堂工作台", "heroTitle"))
        intro.addWidget(label("按日期整理课堂录播，回看课件、笔记与作业。", "muted"))
        actions = QHBoxLayout()
        actions.addWidget(button("选择课程", lambda: window.open_task("run"), "play", "primary"))
        actions.addWidget(button("查看课表", lambda: window.open_task("list")))
        actions.addStretch()
        intro.addLayout(actions)
        band.addLayout(intro, 3)
        band.addWidget(LecturePlate(), 2)
        outer.addWidget(hero)
        outer.addWidget(label("首次使用", "section"))
        setup = QHBoxLayout()
        for symbol, title, detail, handler in (
            ("sliders-horizontal", "1   导入配置", "选择收到的学习配置文件", window.import_profile),
            ("key-round", "2   登录校园平台", "在校园登录页完成验证", lambda: window.open_task("login")),
        ):
            card = QFrame()
            card.setObjectName("card")
            box = QVBoxLayout(card)
            box.setContentsMargins(13, 10, 13, 11)
            box.addWidget(button(title, handler, symbol, "row"))
            box.addWidget(label(detail, "small"))
            setup.addWidget(card, 1)
        outer.addLayout(setup)
        header = QHBoxLayout()
        header.addWidget(label("我的课程", "section"))
        header.addStretch()
        header.addWidget(button("查看全部", lambda: window.navigate(1), "library"))
        outer.addLayout(header)
        self.inventory = QVBoxLayout()
        self.inventory.setSpacing(6)
        outer.addLayout(self.inventory)
        outer.addStretch()
        self.refresh()

    def refresh(self):
        while self.inventory.count():
            self.inventory.takeAt(0).widget().deleteLater()
        rows = []
        for name in catalog.list_courses(self.window.cfg):
            dates = catalog.list_dates(self.window.cfg, name)
            if dates:
                rows.append((dates[0].name, name))
        rows.sort(reverse=True)
        if not rows:
            self.inventory.addWidget(label("还没有课程。登录后点击「选择课程」，整理的录播和笔记会出现在这里。", "muted"))
        for last, name in rows[:5]:
            self.inventory.addWidget(button(f"{name}     ·     {last}", lambda checked=False, n=name: self.window.open_course(n),
                                            "notebook-pen", "row"))


class MainWindow(QMainWindow):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.jobs = JobManager(cfg)
        self.setWindowTitle("SmartClass · 课堂手记")
        self.resize(1100, 740)
        self.setMinimumSize(760, 500)
        root = QWidget()
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        rail = QWidget()
        rail.setObjectName("rail")
        rail.setFixedWidth(190)
        menu = QVBoxLayout(rail)
        menu.setContentsMargins(14, 25, 14, 16)
        menu.setSpacing(7)
        brand = QHBoxLayout()
        mark = label("")
        mark.setPixmap(icon("notebook-pen", "#944d34").pixmap(30, 30))
        brand.addWidget(mark)
        wordmark = label("SmartClass", "brand")
        wordmark.setWordWrap(False)
        brand.addWidget(wordmark)
        menu.addLayout(brand)
        menu.addWidget(label("课堂手记", "brandNote"))
        menu.addSpacing(21)
        self.group = QButtonGroup(self)
        for index, (title, art) in enumerate((("工作台", "desk-lamp"), ("课程资料", "book-open"),
                                             ("任务中心", "audio-lines"), ("学习配置", "sliders-horizontal"))):
            entry = button(title, lambda checked=False, i=index: self.navigate(i), art, "nav")
            entry.setCheckable(True)
            entry.setIconSize(QSize(22, 22))
            self.group.addButton(entry, index)
            menu.addWidget(entry)
        menu.addStretch()
        self.status = label("本机就绪", "status")
        self.status.setAccessibleName("运行状态")
        menu.addWidget(self.status)
        menu.addWidget(button("打开资料目录", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(runs_root(cfg).resolve()))), "folder-open", "nav"))
        licenses = Path(getattr(sys, "_MEIPASS", "")) / "licenses" if getattr(sys, "frozen", False) else ASSETS.parents[2] / "portable" / "THIRD-PARTY.md"
        if licenses.exists():
            menu.addWidget(button("第三方许可", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(licenses.resolve()))), role="nav"))
        row.addWidget(rail)
        self.stack = QStackedWidget()
        row.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.home = Home(self)
        self.library = Library(cfg, self.open_task)
        self.tasks = TaskPanel(cfg, self.jobs)
        self.preferences = Preferences(cfg)
        for widget in (scroll_page(self.home), self.library, self.tasks, scroll_page(self.preferences)):
            self.stack.addWidget(widget)
        self.group.button(0).setChecked(True)
        self.was_running = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(900)
        self.poll()

    def poll(self):
        state = self.jobs.status()
        self.tasks.refresh(state)
        if self.was_running and not state["running"]:
            self.home.refresh()
            if self.stack.currentIndex() == 1:
                self.library.refresh()
        self.was_running = state["running"]
        self.status.setText("任务运行中" if state["running"] else ("需登录校园平台" if login_needed(self.cfg) else "本机就绪"))

    def navigate(self, index, course=""):
        if index == 1:
            self.library.refresh(course)
        elif index == 0:
            self.home.refresh()
        self.group.button(index).setChecked(True)
        if self.stack.currentIndex() != index:
            self.stack.setCurrentIndex(index)
            if QApplication.style().styleHint(QStyle.StyleHint.SH_Widget_Animate):
                # 只给选中的导航图标加动效。整页淡入会让滚动区域残留上一页的像素。
                entry = self.group.button(index)
                motion = QPropertyAnimation(entry, b"iconSize", entry)
                motion.setStartValue(QSize(18, 18))
                motion.setEndValue(QSize(22, 22))
                motion.setDuration(160)
                motion.setEasingCurve(QEasingCurve.Type.OutCubic)
                motion.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def open_course(self, course):
        self.navigate(1, course)

    def open_task(self, verb, **fields):
        self.navigate(2)
        self.tasks.select_task(verb, **fields)

    def import_profile(self):
        self.navigate(3)
        self.preferences.choose_profile()

    def closeEvent(self, event):
        if self.jobs.running():
            self.navigate(2)
            QMessageBox.information(self, "任务还在运行", "请等待任务结束，或在任务中心取消后再退出。")
            event.ignore()
        else:
            super().closeEvent(event)


def run_desktop(config_path: str | None = None) -> int:
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName("SmartClass")
    app.setWindowIcon(QIcon(str(ASSETS / "icons" / "smartclass.svg")))
    app.setStyle("Fusion")
    app.setStyleSheet((ASSETS / "theme.qss").read_text(encoding="utf-8"))
    window = MainWindow(load_config(config_path))
    screen = app.primaryScreen().availableGeometry()
    window.resize(min(window.width(), screen.width()), min(window.height(), screen.height()))
    window.show()
    return app.exec()
