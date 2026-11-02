"""A real audio-decoding and speech-inference smoke test.

The previous packaging check only loaded the model, so it missed a PyAV
19 / faster-whisper 1.2.1 incompatibility which crashes on audio decoding.
Run this check in CI and from the packaged Windows EXE.
"""
from __future__ import annotations

from array import array
import math
from pathlib import Path
import tempfile
import wave


def check_asr_runtime(model_folder: str) -> str:
    import av
    from faster_whisper.audio import decode_audio
    from faster_whisper import WhisperModel

    if not Path(model_folder, "model.bin").is_file():
        raise RuntimeError("The offline transcription model is missing.")

    with tempfile.TemporaryDirectory(prefix="reelforge_asr_test_") as directory:
        sample = Path(directory) / "decode_test.wav"
        sample_rate = 16000
        frames = array("h", (
            int(6000 * math.sin(2 * math.pi * 260 * i / sample_rate))
            for i in range(int(1.6 * sample_rate))
        ))
        with wave.open(str(sample), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(frames.tobytes())

        # This exact call raises TypeError when PyAV 19 is paired with
        # faster-whisper 1.2.1. Merely importing/loading a WhisperModel does not.
        decoded = decode_audio(str(sample), sampling_rate=sample_rate)
        if len(decoded) < sample_rate:
            raise RuntimeError("Whisper audio decoder returned insufficient audio.")

        model = WhisperModel(model_folder, device="cpu", compute_type="int8",
                             cpu_threads=2)
        segments, info = model.transcribe(
            str(sample), language="en", beam_size=1, vad_filter=False,
            word_timestamps=True, condition_on_previous_text=False)
        list(segments)  # The model inference is lazy; this forces execution.

    return f"Transcription path passed; PyAV={av.__version__}"
