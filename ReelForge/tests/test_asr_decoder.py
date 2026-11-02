"""Regression test: faster-whisper must actually decode audio.

Prior releases passed their model-load smoke test yet failed immediately on
all speech videos due to av.open(metadata_errors=...).
"""
from array import array
import math
from pathlib import Path
import wave

import av
from faster_whisper.audio import decode_audio


def test_faster_whisper_audio_decode_with_bundled_pyav(tmp_path):
    file = tmp_path / "camera_audio.wav"
    with wave.open(str(file), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        samples = array("h", [
            int(7000 * math.sin(2 * math.pi * 440 * n / 16000))
            for n in range(16000)
        ])
        audio.writeframes(samples.tobytes())

    decoded = decode_audio(str(file), sampling_rate=16000)
    assert len(decoded) >= 15800
    assert max(abs(decoded)) > 0.1


def test_pyav_major_version_is_known_compatible():
    # Protects against another pip reinstall silently repackaging incompatible PyAV.
    assert int(av.__version__.split(".")[0]) == 18, (
        f"Check faster-whisper compatibility: unexpected PyAV {av.__version__}"
    )
