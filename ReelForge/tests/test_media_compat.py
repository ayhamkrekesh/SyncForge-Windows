"""Regression tests for unusual video metadata and failed uploads."""
from __future__ import annotations

import json
import sys
from pathlib import Path
import subprocess
import wave

import pytest

import reel_engine
from media_compat import (MediaMetadataError, extract_wav, inspect,
                          parse_duration, read_media_payload, remux_source)
from reel_engine import binary, execute, make_reel, probe, Word


def test_duration_from_missing_video_duration_and_matroska_tags():
    payload = {
        "format": {"duration": "N/A"},
        "streams": [
            {"codec_type": "video", "width": 1080, "height": 1920,
             "duration": "N/A", "tags": {"DURATION": "00:01:09.425"}},
            {"codec_type": "audio", "duration": "N/A"}
        ],
    }
    duration, audio, width, height = read_media_payload(payload)
    assert duration == pytest.approx(69.425)
    assert audio and width == 1080 and height == 1920
    assert parse_duration("N/A") is None
    assert parse_duration("nan") is None
    assert parse_duration("00:00:05.2") == pytest.approx(5.2)


def test_frame_count_fallback_and_poster_not_video():
    payload = {"streams": [
        {"codec_type": "video", "disposition": {"attached_pic": 1},
         "width": 800, "height": 600},
        {"codec_type": "video", "width": 640, "height": 480,
         "nb_frames": "300", "avg_frame_rate": "30000/1001"},
    ]}
    duration, audio, width, height = read_media_payload(payload)
    assert duration == pytest.approx(10.01)
    assert not audio
    assert (width, height) == (640, 480)


def test_ffprobe_extended_retry_and_incomplete_duration(tmp_path):
    source = tmp_path / "movie.mov"
    source.write_bytes(b"x" * 1024)
    valid = {"streams": [{"codec_type": "video", "width": 720,
                          "height": 1280, "duration": "7.5"}]}
    calls = []
    def fake_runner(args):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("bad metadata in stream")
        return json.dumps(valid).encode()
    assert inspect(source, "ffprobe", fake_runner) == (7.5, False, 720, 1280)
    assert len(calls) == 2
    source.write_bytes(b"x")
    with pytest.raises(MediaMetadataError, match="empty or incomplete"):
        inspect(source, "ffprobe", fake_runner)


def generate_tagged_video(path: Path, seconds=3):
    subprocess.run([
        binary("ffmpeg"), "-y", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc2=size=160x90:rate=25:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=500:duration={seconds}",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "mpeg4", "-q:v", "10", "-c:a", "aac",
        "-metadata", "comment=Generated Test — ReelForge metadata",
        "-metadata", "creation_time=2026-11-01T05:04:03Z",
        "-shortest", str(path)
    ], check=True, capture_output=True)
    return path


def test_remux_strips_metadata_without_touching_original_and_clean_wav(tmp_path):
    src = generate_tagged_video(tmp_path / "input with unicode é.mp4")
    original_bytes = src.read_bytes()
    clean = remux_source(src, tmp_path / "repaired.mkv", binary("ffmpeg"), execute)
    info = probe(clean)
    assert 2.7 < info.duration < 3.3
    assert info.has_audio
    pcm = extract_wav(clean, tmp_path / "for_whisper.wav",
                      binary("ffmpeg"), execute)
    with wave.open(str(pcm)) as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
        assert wav.getnframes() > 35000
    assert src.read_bytes() == original_bytes


def test_transcription_receives_normalized_wav_not_original_video(monkeypatch, tmp_path):
    source = generate_tagged_video(tmp_path / "camera_original.mov")
    observed = []
    class FakeSegment:
        words = [type("W", (), {"start": 0.1, "end": 0.35, "word": "Hello"})()]
    class FakeWhisper:
        def __init__(self, *args, **kwargs):
            pass
        def transcribe(self, file, **kwargs):
            assert file.endswith("normalized_speech.wav")
            with wave.open(file) as w:
                assert w.getframerate() == 16000
            observed.append(file)
            return [FakeSegment()], None
    monkeypatch.setattr(reel_engine, "bundled_model", lambda: "test-local-model")
    monkeypatch.setitem(sys.modules, "faster_whisper",
                        type("FakeModule", (), {"WhisperModel": FakeWhisper})())
    words = reel_engine.transcribe(str(source), lambda _: None)
    assert words == [Word(0.1, 0.35, "Hello")]
    assert len(observed) == 1


def test_failed_source_writes_error_report_not_just_metadata_exception(tmp_path):
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"This is not an MP4 video" * 40)
    with pytest.raises(RuntimeError, match="Diagnostic report saved"):
        make_reel(str(bad), str(tmp_path / "outputs"), music="Off")
    runs = list((tmp_path / "outputs").glob("ReelForge_*"))
    assert len(runs) == 1
    diagnostics = runs[0] / "ReelForge_Error_Report.txt"
    assert diagnostics.is_file()
    contents = diagnostics.read_text(encoding="utf-8")
    assert "broken.mp4" in contents
    assert "traceback" in contents.lower()
    assert bad.is_file()


def test_edit_can_recover_source_from_first_metadata_probe_failure(tmp_path, monkeypatch):
    source = generate_tagged_video(tmp_path / "camera.mov")
    original_probe = reel_engine.probe
    first_call = {"count": 0}
    def fail_once(path):
        first_call["count"] += 1
        if first_call["count"] == 1:
            raise MediaMetadataError("Mock source metadata failure")
        return original_probe(path)
    monkeypatch.setattr(reel_engine, "probe", fail_once)
    output = make_reel(str(source), str(tmp_path / "result"),
                       transcript_override=[Word(0.1, 0.7, "Hello"),
                                            Word(0.8, 1.5, "camera"),
                                            Word(1.6, 2.5, "test.")],
                       resolution="720p", music="Off")
    assert Path(output["file"]).is_file()
    assert output["duration"] < 60
    assert output["warnings"]
    assert not (Path(output["file"]).parent / "Recovered_Source.mkv").exists()
