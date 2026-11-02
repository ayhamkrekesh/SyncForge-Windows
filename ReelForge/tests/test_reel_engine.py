"""Offline synthetic-video regression tests; no external footage or cloud API."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import wave

import pytest

from reel_engine import (Cut, Word, ass_timestamp, binary, captions_ass,
                         enforce_limit, generate_original_beat, make_reel,
                         probe, reel_word_timing, render, select_montage_cuts,
                         select_speech_cuts)


def source_video(path: Path, *, seconds=4.0, audio=True):
    cmd = [binary("ffmpeg"), "-y", "-v", "error",
           "-f", "lavfi", "-i", f"testsrc2=size=240x136:rate=30:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}"]
    cmd += ["-c:v", "mpeg4", "-q:v", "8"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "96k"]
    cmd += ["-shortest", str(path)]
    subprocess.run(cmd, capture_output=True, check=True)
    return path


def test_speech_selection_and_caption_alignment(tmp_path):
    words = []
    for idx in range(80):
        start = idx * 0.45
        words.append(Word(start, start + 0.31, ["How", "do", "you", "know?"][idx % 4]))
    selected = select_speech_cuts(words, 60)
    selected = enforce_limit(selected)
    assert selected
    assert sum(c.duration for c in selected)/1.06 < 60.0
    mapped = reel_word_timing(words, selected)
    assert len(mapped) > 0
    assert all(mapped[i].start <= mapped[i+1].start for i in range(len(mapped)-1))
    assert captions_ass(mapped, tmp_path / "captions.ass", width=720, height=1280)
    content = (tmp_path / "captions.ass").read_text(encoding="utf-8")
    assert "[Events]" in content
    assert "Dialogue:" in content
    assert "HOW" in content
    assert ass_timestamp(1.23) == "0:00:01.23"


def test_duration_guard():
    selected = enforce_limit([Cut(0, 45), Cut(46, 120), Cut(130, 170)])
    assert sum(c.duration for c in selected) / 1.06 <= 58.1
    assert selected[-1].end <= 170


def test_original_music_is_self_generated(tmp_path):
    file = generate_original_beat(tmp_path / "beat.wav", 4)
    assert file.exists()
    with wave.open(str(file)) as wav:
        assert wav.getnchannels() == 1
        assert wav.getframerate() == 32000
        assert wav.getnframes() > 4 * 32000


def test_ffmpeg_caption_zoom_and_export(tmp_path):
    video = source_video(tmp_path / "test.mp4", seconds=4, audio=True)
    info = probe(video)
    assert info.has_audio
    cuts = [Cut(0.1, 1.5), Cut(1.9, 3.6)]
    words = [Word(0.2, 0.8, "Amazing"), Word(0.85, 1.25, "moment!"),
             Word(2.1, 2.7, "Watch"), Word(2.8, 3.2, "this")]
    workspace = tmp_path / "temporary"
    workspace.mkdir()
    out = tmp_path / "edited.mp4"
    detail = render(info, cuts, words, out, workspace, music="Off", resolution="720p")
    assert detail["captions"]
    assert detail["duration"] < 5
    assert detail["size"] == [720, 1280]
    assert out.is_file()
    assert probe(out).has_audio


def test_silent_video_montage_with_music_and_no_spoken_captions(tmp_path):
    video = source_video(tmp_path / "silent.mp4", seconds=3, audio=False)
    info = probe(video)
    assert not info.has_audio
    cuts = select_montage_cuts(str(video), info.duration)
    assert cuts
    workspace = tmp_path / "working"
    workspace.mkdir()
    out = tmp_path / "montage.mp4"
    detail = render(info, cuts, [], out, workspace, crop="Backdrop",
                    music="Auto", resolution="720p")
    assert not detail["captions"]
    assert detail["original_music"]
    assert detail["duration"] < 60
    assert detail["size"] == [720, 1280]
    assert probe(out).has_audio


def test_one_click_job_writes_edit_report(tmp_path):
    video = source_video(tmp_path / "source.mp4", seconds=3.6)
    words = [Word(0.1, 0.5, "Hello"), Word(0.5, 0.95, "everyone"),
             Word(1.1, 1.7, "today"), Word(2, 2.7, "wow!")]
    result = make_reel(str(video), str(tmp_path / "outputs"),
                       transcript_override=words,
                       resolution="720p", music="Off")
    assert result["duration"] < 60
    assert result["selection"] == "speech highlights"
    assert Path(result["file"]).is_file()
    metadata = json.loads((Path(result["file"]).parent / "Edit_Decisions.json").read_text())
    assert metadata["transcription"]
    assert metadata["duration"] < 60
