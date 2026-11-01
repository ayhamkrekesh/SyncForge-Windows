"""End-to-end tests use synthetic recordings, never copyrighted footage."""
from pathlib import Path
import subprocess
import wave
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from syncforge_core import (
    Match, analyze, correlation_scores, envelope, export_video,
    find_binary, offset_search, premiere_xml, probe, safe_name, write_report,
)


def write_wav(path: Path, samples, sr=8000):
    pcm = np.clip(samples * 32767, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(pcm.tobytes())


def make_sound(seconds=36, sr=8000):
    """Speech-like uneven loudness + distinctive crackles."""
    rng = np.random.default_rng(12345)
    n = int(seconds * sr)
    amplitude_steps = rng.uniform(0.08, 0.8, int(np.ceil(seconds * 9)) + 1)
    amplitude = np.repeat(amplitude_steps, max(1, sr // 9))[:n]
    audio = rng.standard_normal(n) * amplitude * 0.15
    return audio.astype(np.float64)


@pytest.fixture
def media(tmp_path):
    sr = 8000
    master = make_sound()
    vid_audio = tmp_path / "scratch.wav"
    matching_audio = tmp_path / "recorder.wav"
    distractor = tmp_path / "unrelated.wav"
    # Video covers master 2-20; external recorder covers master 5-26.
    # Therefore recorder audio begins 3 seconds into the camera video.
    write_wav(vid_audio, master[2*sr:20*sr])
    write_wav(matching_audio, master[5*sr:26*sr] * 0.8)
    write_wav(distractor, np.random.default_rng(5).standard_normal(21*sr) * 0.1)
    video = tmp_path / "camera.mp4"
    subprocess.run([find_binary("ffmpeg"), "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=25",
                    "-i", str(vid_audio), "-shortest",
                    "-c:v", "mpeg4", "-q:v", "8",
                    "-c:a", "aac", "-b:a", "64k", str(video)],
                   check=True, capture_output=True)
    return video, matching_audio, distractor


def test_fft_offset_direction_and_peak():
    rng = np.random.default_rng(2)
    ref = rng.random(1000)
    long = np.r_[np.zeros(185), ref, np.zeros(250)]
    offset, confidence, _ = offset_search(ref, long)
    assert abs(offset + 185/50) < 0.03
    assert confidence > 0.99
    # A camera segment begins before a recorder which captures its later part.
    offset2, confidence2, _ = offset_search(ref, ref[200:])
    assert abs(offset2 - 4) < 0.03
    assert confidence2 > 0.99


def test_auto_pairing_and_ambiguous_fail_safe(media):
    video, audio, decoy = media
    result, problems = analyze([str(video)], [str(decoy), str(audio)])
    assert problems == []
    assert len(result) == 1
    assert result[0].audio == str(audio)
    assert abs(result[0].offset - 3) < 0.16
    assert result[0].confidence > 0.65
    # Duplicate recordings are ambiguous. Refuse to guess which to pick.
    clone = audio.with_name("recorder2.wav")
    clone.write_bytes(audio.read_bytes())
    result2, problems2 = analyze([str(video)], [str(audio), str(clone)])
    assert result2 == []
    assert len(problems2) == 1
    assert "Ambiguous" in problems2[0][1]


def test_export_video_and_premiere_xml(media, tmp_path):
    video, audio, _ = media
    matches, problems = analyze([str(video)], [str(audio)])
    assert not problems
    output = export_video(matches[0], tmp_path / "finished.mp4")
    info = probe(output)
    assert info.audio and info.video
    assert abs(info.duration - 18) < 0.3
    samples = tmp_path / "output.raw"
    with samples.open("wb") as handle:
        subprocess.run([find_binary("ffmpeg"), "-v", "error", "-i",
                        str(output), "-map", "0:a:0", "-ac", "1",
                        "-ar", "8000", "-f", "f32le", "-"],
                       check=True, stdout=handle, stderr=subprocess.PIPE)
    audio_out = np.fromfile(samples, dtype="<f4")
    assert np.sqrt(np.mean(audio_out[:2*8000]**2)) < 0.003
    assert np.sqrt(np.mean(audio_out[5*8000:7*8000]**2)) > 0.01

    xml = premiere_xml([output, output], tmp_path / "timeline.xml")
    root = ET.parse(xml).getroot()
    assert root.tag == "xmeml"
    clips_v = root.findall("./sequence/media/video/track/clipitem")
    clips_a = root.findall("./sequence/media/audio/track/clipitem")
    assert len(clips_v) == len(clips_a) == 2
    assert clips_v[0].findtext("end") == clips_v[1].findtext("start")
    assert clips_a[0].findtext("end") == clips_a[1].findtext("start")
    assert output.resolve().as_uri() == clips_v[0].findtext("file/pathurl")
    report = write_report(matches, [], tmp_path / "pairing.csv")
    assert "recorder.wav" in report.read_text(encoding="utf-8-sig")


def test_bad_input_handling(tmp_path):
    video = tmp_path / "missing.mp4"
    with pytest.raises(ValueError, match="Missing media"):
        probe(video)
    with pytest.raises(ValueError, match="No synchronized clips"):
        premiere_xml([], tmp_path / "empty.xml")
    assert safe_name('illegal<>:"*/\\|?name') == "illegal________name"
