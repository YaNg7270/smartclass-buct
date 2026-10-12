"""运行目录布局与元数据。

data/<课程>/<日期>/<首节开始>-<末节结束>/      ← 整课包（对外交付）
├── lecture.json      整课元数据（课程/日期/起止/成员小节）
├── transcript.txt    合并后的整课转写
├── slides.txt        合并的课件 OCR
├── homework.md       整课作业要求
├── notes.md          整课笔记
└── parts/<时段>/     各小节中间产物（videos/audio/transcript/frames/slides.txt）
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config, runs_root


def safe_name(s: str, fallback: str = "未命名") -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", (s or "").strip())
    s = re.sub(r"\s+", "", s)
    s = s.strip("_.")
    return s[:80] or fallback


@dataclass
class RunDir:
    """小节级目录（保留原始产物，便于重跑与追溯）。"""

    root: Path

    @property
    def videos(self) -> Path:
        return self.root / "videos"

    @property
    def audio(self) -> Path:
        return self.root / "audio"

    @property
    def transcript(self) -> Path:
        return self.root / "transcript"

    @property
    def frames(self) -> Path:
        return self.root / "frames"

    @property
    def meta_path(self) -> Path:
        return self.root / "meta.json"

    @property
    def slides_path(self) -> Path:
        return self.root / "slides.txt"

    @property
    def homework_path(self) -> Path:
        return self.root / "homework.md"

    @property
    def notes_path(self) -> Path:
        return self.root / "notes.md"

    @property
    def full_transcript(self) -> Path:
        return self.transcript / "full.txt"

    def ensure(self) -> "RunDir":
        for d in (self.videos, self.audio, self.transcript, self.frames):
            d.mkdir(parents=True, exist_ok=True)
        return self

    def load_meta(self) -> dict:
        if self.meta_path.is_file():
            return json.loads(self.meta_path.read_text(encoding="utf-8"))
        return {}

    def save_meta(self, meta: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )


@dataclass
class LectureDir:
    """整课目录：对外交付的转写、课件文字、作业要求与笔记都在这一层。"""

    root: Path

    @property
    def parts_dir(self) -> Path:
        return self.root / "parts"

    def part(self, slot: str) -> RunDir:
        return RunDir(self.parts_dir / safe_name(slot, "小节"))

    @property
    def meta_path(self) -> Path:
        return self.root / "lecture.json"

    @property
    def transcript_path(self) -> Path:
        return self.root / "transcript.txt"

    @property
    def slides_path(self) -> Path:
        return self.root / "slides.txt"

    @property
    def homework_path(self) -> Path:
        return self.root / "homework.md"

    @property
    def notes_path(self) -> Path:
        return self.root / "notes.md"

    def ensure(self) -> "LectureDir":
        self.root.mkdir(parents=True, exist_ok=True)
        self.parts_dir.mkdir(parents=True, exist_ok=True)
        return self

    def load_meta(self) -> dict:
        if self.meta_path.is_file():
            return json.loads(self.meta_path.read_text(encoding="utf-8"))
        return {}

    def save_meta(self, meta: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # 兼容 digest.py 里对 RunDir 的用法
    @property
    def name(self) -> str:
        return self.root.name


def run_dir_for(cfg: Config, course: str, date: str, slot: str,
                root: str | None = None, lecture_span: str | None = None) -> RunDir:
    """小节目录：data/<课程>/<日期>/<整课时段>/parts/<小节时段>/

    不传 lecture_span 时退回旧布局 data/<课程>/<日期>/<时段>/（供迁移前兼容）。
    """
    base = runs_root(cfg, root)
    course_dir = base / safe_name(course, "课程") / (date or "未知日期")
    if lecture_span:
        return RunDir(course_dir / safe_name(lecture_span, "整课") / "parts" / safe_name(slot, "小节"))
    return RunDir(course_dir / safe_name(slot, "课时"))


def lecture_dir_for(cfg: Config, course: str, date: str, span: str,
                    root: str | None = None) -> LectureDir:
    base = runs_root(cfg, root)
    return LectureDir(base / safe_name(course, "课程") / (date or "未知日期") / safe_name(span, "整课"))
