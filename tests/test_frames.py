"""抽帧去重：相同画面只留一张，但原始序号必须保留（时间戳靠它）。"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartclass import audio as audio_mod      # noqa: E402


def _frame(path: Path, extra: bool = False) -> Path:
    im = Image.new("RGB", (320, 180), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([20, 20, 200, 70], fill="black")
    d.text((24, 90), "chapter 1", fill="black")
    if extra:
        d.rectangle([20, 120, 300, 170], fill="black")
    im.save(path, quality=95)
    return path


def test_identical_frames_collapse(tmp_path):
    frames = [_frame(tmp_path / f"f_{i:03d}.jpg") for i in range(4)]
    kept = audio_mod.dedupe_frames(frames, threshold=3)
    assert [i for i, _ in kept] == [0]
    assert kept[0][1] == frames[0]


def test_changed_frame_is_kept_with_original_index(tmp_path):
    a = _frame(tmp_path / "f_001.jpg")
    b = _frame(tmp_path / "f_002.jpg")
    c = _frame(tmp_path / "f_003.jpg", extra=True)
    d = _frame(tmp_path / "f_004.jpg", extra=True)
    kept = audio_mod.dedupe_frames([a, b, c, d], threshold=3)
    # a/b 相同只留 a；c/d 相同只留 c —— 序号必须是 0 和 2
    assert [i for i, _ in kept] == [0, 2]
    assert kept[1][1] == c


def test_empty_input(tmp_path):
    assert audio_mod.dedupe_frames([], threshold=3) == []


def test_threshold_zero_is_most_conservative(tmp_path):
    frames = [_frame(tmp_path / f"f_{i:03d}.jpg") for i in range(3)]
    assert len(audio_mod.dedupe_frames(frames, threshold=0)) == 1
