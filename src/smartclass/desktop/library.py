"""原生课程阅读器、录播播放及本地全文检索。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer, QUrl
from PySide6.QtGui import QPixmap, QTextDocument, QTextCursor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QSlider, QSplitter, QStackedWidget, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from .. import catalog
from ..config import runs_root
from ..web.scan import list_parts, search_text
from ..workspace import LectureDir
from .paper import button, icon, label


class Document(QTextBrowser):
    def __init__(self):
        super().__init__()
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.document().setDocumentMargin(15)

    def loadResource(self, resource_type, url):
        # 讲义 Markdown 中的外部图片及文件不应自动加载。
        return QByteArray()

    def show_file(self, path, empty="尚未生成，可以在任务中心继续处理。"):
        self.setUpdatesEnabled(False)
        try:
            if not path or not path.is_file():
                self.setPlainText(empty)
                return
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            if path.suffix.lower() == ".md":
                self.document().setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownNoHTML)
            else:
                self.setPlainText(text)
            self.moveCursor(QTextCursor.MoveOperation.Start)
        except OSError as exc:
            self.setPlainText(f"无法读取文件。{exc}")
        finally:
            self.setUpdatesEnabled(True)


class MediaShelf(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        self.files = QComboBox()
        self.stack = QStackedWidget()
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video = QVideoWidget()
        self.stack.addWidget(self.preview)
        self.stack.addWidget(self.video)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.play = button("播放", self.toggle, "play")
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setAccessibleName("媒体进度")
        self.player.durationChanged.connect(lambda value: self.seek.setRange(0, value))
        self.player.positionChanged.connect(lambda value: self.seek.setValue(value) if not self.seek.isSliderDown() else None)
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.player.playbackStateChanged.connect(lambda state: self.play.setText(
            "暂停" if state == QMediaPlayer.PlaybackState.PlayingState else "播放"))
        self.status = label("", "small")
        self.player.errorOccurred.connect(lambda *args: self.status.setText(self.player.errorString()))
        controls = QHBoxLayout()
        controls.addWidget(self.play)
        controls.addWidget(self.seek, 1)
        layout.addWidget(self.files)
        layout.addWidget(self.stack, 1)
        layout.addLayout(controls)
        layout.addWidget(self.status)
        self.files.currentIndexChanged.connect(self.selected)

    def load(self, parts):
        self.player.stop()
        self.files.blockSignals(True)
        self.files.clear()
        for part in parts:
            for path in [*part["videos"], *sorted((part["dir"] / "audio").glob("*.mp3")), *part["frames"]]:
                self.files.addItem(f"{part['slot']}   /   {path.name}", str(path))
        self.files.blockSignals(False)
        self.selected()

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def selected(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self.status.clear()
        path = Path(self.files.currentData() or "")
        playable = path.suffix.lower() in {".mp4", ".mp3"}
        self.play.setEnabled(playable)
        self.seek.setEnabled(playable)
        self.stack.setCurrentWidget(self.video if playable else self.preview)
        if playable:
            self.player.setSource(QUrl.fromLocalFile(str(path.resolve())))
        elif path.is_file():
            self.draw_frame()
        else:
            self.preview.setText("本讲尚无录播或课件帧。")

    def draw_frame(self):
        image = QPixmap(self.files.currentData() or "")
        if not image.isNull():
            self.preview.setPixmap(image.scaled(
                self.stack.size().boundedTo(image.size()), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        else:
            self.preview.setText("无法读取这张课件帧。")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.files.currentData() and self.stack.currentWidget() is self.preview:
            self.draw_frame()

    def hideEvent(self, event):
        self.player.pause()
        super().hideEvent(event)


class SearchDialog(QDialog):
    def __init__(self, query, cfg, parent):
        super().__init__(parent)
        self.setWindowTitle("全文检索")
        self.resize(790, 465)
        self.root = runs_root(cfg)
        layout = QVBoxLayout(self)
        self.status = label("正在搜索本地课程…", "status")
        layout.addWidget(self.status)
        split = QSplitter()
        self.hits = QListWidget()
        self.doc = Document()
        split.addWidget(self.hits)
        split.addWidget(self.doc)
        split.setStretchFactor(1, 2)
        layout.addWidget(split)
        self.hits.currentItemChanged.connect(self.read_hit)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="课程检索")
        self.future = self.pool.submit(search_text, cfg, query)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.finish_search)
        self.timer.start(100)

    def finish_search(self):
        if not self.future.done():
            return
        self.timer.stop()
        try:
            rows = self.future.result()
        except OSError as exc:
            self.status.setText(f"无法搜索课程目录。{exc}")
            return
        self.status.setText(f"找到 {len(rows)} 处匹配" + ("（仅显示前 200 处）" if len(rows) == 200 else ""))
        for row in rows:
            item = QListWidgetItem(f"{row['line'][:46]}\n{row['file']} · 第 {row['line_no']} 行")
            item.setData(Qt.ItemDataRole.UserRole, row)
            self.hits.addItem(item)
        self.hits.setCurrentRow(0)

    def read_hit(self, item):
        if item:
            row = item.data(Qt.ItemDataRole.UserRole)
            # 按原始行号定位，Markdown 的显示行与磁盘行数可能不同。
            try:
                self.doc.setPlainText((self.root / row["file"]).read_text(encoding="utf-8-sig", errors="replace"))
            except OSError as exc:
                self.doc.setPlainText(str(exc))
                return
            block = self.doc.document().findBlockByLineNumber(row["line_no"] - 1)
            if block.isValid():
                cursor = QTextCursor(block)
                cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                self.doc.setTextCursor(cursor)
                self.doc.ensureCursorVisible()

    def done(self, result):
        self.timer.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        super().done(result)


class Library(QWidget):
    def __init__(self, cfg, start_task):
        super().__init__()
        self.setObjectName("page")
        self.cfg = cfg
        self.start_task = start_task
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 18)
        layout.setSpacing(12)
        header = QHBoxLayout()
        header.addWidget(label("课程资料", "pageTitle"), 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("全文搜索")
        self.search.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.returnPressed.connect(self.search_library)
        header.addWidget(self.search)
        header.addWidget(button("检索", self.search_library))
        layout.addLayout(header)
        pickers = QHBoxLayout()
        self.courses = QComboBox()
        self.courses.setAccessibleName("选择课程")
        self.lectures = QComboBox()
        self.lectures.setAccessibleName("选择讲次")
        pickers.addWidget(self.courses, 2)
        pickers.addWidget(self.lectures, 3)
        layout.addLayout(pickers)
        self.tabs = QTabWidget()
        self.docs = {}
        for title, field in (("笔记", "notes_path"), ("作业", "homework_path"), ("转写", "transcript_path"),
                             ("课件", "slides_path"), ("元数据", "meta_path")):
            self.docs[field] = Document()
            self.tabs.addTab(self.docs[field], title)
        self.media = MediaShelf()
        self.tabs.addTab(self.media, "音视频与课件帧")
        review = QWidget()
        panel = QVBoxLayout(review)
        self.pages = QComboBox()
        self.review_text = Document()
        self.pages.currentIndexChanged.connect(self.review_selected)
        panel.addWidget(self.pages)
        panel.addWidget(self.review_text)
        self.tabs.addTab(review, "复习")
        layout.addWidget(self.tabs, 1)
        actions = QHBoxLayout()
        self.resume = button("继续处理", self.resume_lecture, "play")
        self.review = button("生成复习资料", lambda: start_task("review", course=self.courses.currentText()), "book-open")
        actions.addWidget(self.resume)
        actions.addWidget(self.review)
        actions.addStretch()
        layout.addLayout(actions)
        self.courses.currentIndexChanged.connect(self.course_selected)
        self.lectures.currentIndexChanged.connect(self.lecture_selected)

    def refresh(self, course=""):
        name = course or self.courses.currentText()
        self.courses.blockSignals(True)
        self.courses.clear()
        self.courses.addItems(catalog.list_courses(self.cfg))
        index = self.courses.findText(name)
        self.courses.setCurrentIndex(max(0, index))
        self.courses.blockSignals(False)
        self.course_selected()

    def course_selected(self):
        name = self.courses.currentText()
        self.lectures.blockSignals(True)
        self.lectures.clear()
        self.pages.clear()
        if name:
            for date_dir in catalog.list_dates(self.cfg, name):
                for entry in catalog.lectures_in(self.cfg, name, date_dir.name):
                    self.lectures.addItem(f"{date_dir.name}    {entry['span']}", (str(date_dir), entry["uid"]))
            review_dir = runs_root(self.cfg) / name / self.cfg.review.folder
            for path in sorted(review_dir.glob("*.md"), key=lambda p: (p.name != "README.md", p.name)):
                self.pages.addItem("复习索引" if path.name == "README.md" else path.stem, str(path))
        self.lectures.blockSignals(False)
        self.review_selected()
        self.lecture_selected()
        self.review.setEnabled(bool(name))

    def review_selected(self):
        path = self.pages.currentData()
        self.review_text.show_file(Path(path) if path else None, "尚无复习资料。选定课程后可生成每讲的复习页。")

    def lecture_selected(self):
        data = self.lectures.currentData()
        self.resume.setEnabled(bool(data))
        lesson = LectureDir(Path(data[0]), data[1]) if data else None
        for field, doc in self.docs.items():
            doc.show_file(getattr(lesson, field) if lesson else None,
                          "当前没有可阅读的讲次。" if not lesson else "尚未生成，可点击下方「继续处理」。")
        self.media.load(list_parts(lesson.root, lesson.uid) if lesson else [])

    def resume_lecture(self):
        if data := self.lectures.currentData():
            self.start_task("resume", dir=data[0])

    def search_library(self):
        if query := self.search.text().strip():
            dialog = SearchDialog(query, self.cfg, self)
            dialog.exec()
            dialog.deleteLater()
