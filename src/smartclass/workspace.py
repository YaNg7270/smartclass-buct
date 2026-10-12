"""运行目录布局与元数据。

runs/<课程>/<日期>/<时段>/
  meta.json  videos/  audio/  transcript/  frames/
  slides.txt homework.md notes.md
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
    root: Path

    # --- 子目录 ---
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

    # --- 元数据 ---
    def load_meta(self) -> dict:
        if self.meta_path.is_file():
            return json.loads(self.meta_path.read_text(encoding="utf-8"))
        return {}

    def save_meta(self, meta: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def run_dir_for(cfg: Config, course: str, date: str, slot: str, root: str | None = None) -> RunDir:
    base = runs_root(cfg, root)
    return RunDir(base / safe_name(course, "课程") / (date or "未知日期") / safe_name(slot, "课时"))
