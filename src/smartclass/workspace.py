"""运行目录布局与元数据。

data/<课程>/<日期>/                       ← 整课包（对外交付，扁平）
├── homework_<日期>_<时段>.md
├── notes_<日期>_<时段>.md
├── transcript.txt
├── slides.txt
├── lecture.json
└── parts/<时段>/                          ← 各小节中间产物

同一天同一门课有多节整课时，第一节占用无后缀的 transcript/slides/lecture.json，
后续的自动改用 `_<日期>_<时段>` 后缀，避免互相覆盖。
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
    """小节级目录（保留原始产物，便于重跑与追溯）。

    `uid` 形如 `2026-10-16_0945-1030`；留空表示旧布局（homework.md / notes.md）。
    """

    root: Path
    uid: str = ""

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
        suffix = f"_{self.uid}" if self.uid else ""
        return self.root / f"homework{suffix}.md"

    @property
    def notes_path(self) -> Path:
        suffix = f"_{self.uid}" if self.uid else ""
        return self.root / f"notes{suffix}.md"

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
    """整课目录：对外交付的转写、课件文字、作业要求与笔记都在这一层。

    `uid` 形如 `2026-10-16_0800-1030`；留空表示旧布局（还带 <时段>/ 子目录）。
    """

    root: Path
    uid: str = ""

    def _plain_meta_uid(self) -> str:
        p = self.root / "lecture.json"
        if not p.is_file():
            return ""
        try:
            return str(json.loads(p.read_text(encoding="utf-8")).get("uid") or "")
        except Exception:
            return ""

    def _use_suffixed(self) -> bool:
        """同一天已有另一节整课占用了无后缀的 lecture.json / transcript。"""
        if not self.uid:
            return False
        other = self._plain_meta_uid()
        return bool(other) and other != self.uid

    @property
    def parts_dir(self) -> Path:
        return self.root / "parts"

    def part(self, slot: str) -> RunDir:
        uid = ""
        if self.uid:
            date = self.uid.rsplit("_", 1)[0]
            uid = f"{date}_{slot}"
        return RunDir(self.parts_dir / safe_name(slot, "小节"), uid=uid)

    @property
    def meta_path(self) -> Path:
        if self._use_suffixed():
            return self.root / f"lecture_{self.uid}.json"
        return self.root / "lecture.json"

    @property
    def transcript_path(self) -> Path:
        if self._use_suffixed():
            return self.root / f"transcript_{self.uid}.txt"
        return self.root / "transcript.txt"

    @property
    def slides_path(self) -> Path:
        if self._use_suffixed():
            return self.root / f"slides_{self.uid}.txt"
        return self.root / "slides.txt"

    @property
    def homework_path(self) -> Path:
        suffix = f"_{self.uid}" if self.uid else ""
        return self.root / f"homework{suffix}.md"

    @property
    def notes_path(self) -> Path:
        suffix = f"_{self.uid}" if self.uid else ""
        return self.root / f"notes{suffix}.md"

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
        if self.uid and not meta.get("uid"):
            meta["uid"] = self.uid
        meta.setdefault("layout", "flat")
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
    """整课目录：data/<课程>/<日期>/（扁平），uid = <日期>_<时段>。"""
    base = runs_root(cfg, root)
    uid = f"{date}_{span}" if date and span else ""
    return LectureDir(base / safe_name(course, "课程") / (date or "未知日期"), uid=uid)
