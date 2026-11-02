"""ReelForge — local, automatic 9:16 short-form video editor.

No cloud processing; dependencies: FFmpeg/FFprobe, NumPy, faster-whisper for
spoken-word transcription. Selects intelligible speech spans when present,
otherwise makes a motion-scored montage. Any model is loaded only when editing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from typing import Callable
import traceback

from media_compat import inspect, extract_wav, remux_source

import numpy as np

MAX_DURATION = 58.0
TARGET_SECONDS = 53.0
SPEED = 1.06
FPS = 30


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Cut:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class Media:
    path: str
    duration: float
    has_audio: bool
    width: int
    height: int


def binary(name: str) -> str:
    folders: list[Path] = []
    if getattr(sys, "frozen", False):
        exedir = Path(sys.executable).resolve().parent
        folders += [exedir / "bin", Path(getattr(sys, "_MEIPASS", exedir)) / "bin"]
    source = Path(__file__).resolve().parent
    folders += [source / "bin", source.parent / "bin"]
    filename = name + ".exe" if os.name == "nt" else name
    for folder in folders:
        executable = folder / filename
        if executable.is_file():
            return str(executable)
    which = shutil.which(name) or shutil.which(name + ".exe")
    if which:
        return which
    raise RuntimeError(f"{name} missing. Extract the entire ReelForge Windows ZIP before starting.")


def bundled_model() -> str:
    folders = []
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve().parent
        folders += [exe / "models" / "whisper-base",
                    Path(getattr(sys, "_MEIPASS", exe)) / "models" / "whisper-base"]
    folders.append(Path(__file__).resolve().parent / "models" / "whisper-base")
    for path in folders:
        if (path / "model.bin").is_file():
            return str(path)
    raise RuntimeError(
        "Offline speech model not found. Download and extract the full ReelForge ZIP "
        "including the models folder. Developers can run scripts/download_model.py.")


def execute(args: list[str], *, cwd=None) -> bytes:
    result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, cwd=cwd, check=False)
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace")[-1700:]
        raise RuntimeError(f"Media tool failed: {detail}")
    return result.stdout


def probe(path: str | Path) -> Media:
    """Read video properties with tolerance for unusual embedded metadata."""
    seconds, has_audio, width, height = inspect(path, binary("ffprobe"), execute)
    return Media(str(Path(path).resolve()), seconds, has_audio, width, height)


def transcribe(path: str, log: Callable[[str], None]) -> list[Word]:
    """Extract clean PCM so Whisper never directly parses camera metadata."""
    from faster_whisper import WhisperModel
    with tempfile.TemporaryDirectory(prefix="reelforge_speech_") as tmp:
        wav = Path(tmp) / "normalized_speech.wav"
        log("Decoding audio separately from video metadata...")
        extract_wav(path, wav, binary("ffmpeg"), execute)
        log("Loading local multilingual speech recognizer...")
        model = WhisperModel(bundled_model(), device="cpu", compute_type="int8",
                             cpu_threads=min(8, os.cpu_count() or 4))
        segments, _ = model.transcribe(
            str(wav), beam_size=3, word_timestamps=True, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 480},
            condition_on_previous_text=False)
        words = []
        for seg in segments:
            if seg.words:
                for word in seg.words:
                    token = word.word.strip()
                    if word.start is not None and word.end is not None and token:
                        words.append(Word(float(word.start), float(word.end), token))
            else:
                sentence = seg.text.strip()
                if sentence:
                    words.append(Word(float(seg.start), float(seg.end), sentence))
        log(f"Recognized {len(words)} speech words or phrases.")
        return words


def _speech_blocks(words: list[Word], duration: float) -> list[Cut]:
    """Build candidate thought units at breathing/punctuation boundaries."""
    valid = [w for w in sorted(words, key=lambda x: x.start)
             if 0 <= w.start < duration and w.end > w.start and
             w.end - w.start < 15]
    if not valid:
        return []
    groups: list[list[Word]] = []
    current: list[Word] = []
    for word in valid:
        prior = current[-1] if current else None
        pause = word.start - prior.end if prior else 0
        length = word.end - current[0].start if current else 0
        if current and (pause > 0.55 or length > 7.0 or
                        (prior.text.endswith((".", "?", "!")) and length >= 2.0)):
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)
    return [Cut(max(0.0, group[0].start - 0.09),
                min(duration, group[-1].end + 0.15))
            for group in groups if group[-1].end - group[0].start >= 0.9]


def _block_score(cut: Cut, words: list[Word]) -> float:
    tokens = [w.text.lower().strip() for w in words
              if w.start < cut.end and w.end > cut.start]
    sentence = " ".join(tokens)
    hooks = ("why", "how", "secret", "never", "mistake", "best", "worst",
             "imagine", "actually", "here's", "because", "wait", "watch",
             "remember", "what if", "the truth", "but", "however")
    quality = sum(1.0 for term in hooks if term in sentence)
    punctuation = 0.35 if any(t.endswith(("?", "!")) for t in tokens) else 0.0
    density = min(1.5, len(tokens) / max(1.0, cut.duration) / 2.6)
    return (density + min(1.4, 0.28 * quality) + punctuation) * min(1.0, cut.duration / 2.0)


def select_speech_cuts(words: list[Word], duration: float, *, max_duration=MAX_DURATION) -> list[Cut]:
    """Choose a dense coherent section and remove its dead space.

    The selected 2.5-minute source window prevents taking unrelated statements
    from every part of a long interview and assembling them as one assertion.
    """
    candidates = _speech_blocks(words, duration)
    if not candidates:
        return []
    budget = min(TARGET_SECONDS, max_duration * SPEED - 0.8)
    if sum(c.duration for c in candidates) <= budget:
        return candidates
    anchor = candidates[0].start
    best_score = -1.0
    for start in (c.start for c in candidates):
        stop = start + 145.0
        window = [c for c in candidates if c.start >= start and c.end <= stop]
        ranked = sorted(window, key=lambda c: _block_score(c, words) / max(0.1, c.duration),
                        reverse=True)
        used = 0.0
        score = 0.0
        for cut in ranked:
            if used + cut.duration <= budget + 1e-6:
                used += cut.duration
                score += _block_score(cut, words)
        if score > best_score:
            best_score, anchor = score, start
    window = [c for c in candidates if c.start >= anchor and c.end <= anchor + 145.0]
    ranked = sorted(window,
                    key=lambda c: _block_score(c, words) / max(0.1, c.duration), reverse=True)
    chosen = []
    total = 0.0
    for cut in ranked:
        if total + cut.duration > budget:
            continue
        chosen.append(cut)
        total += cut.duration
    if not chosen:
        chosen = [Cut(candidates[0].start, min(candidates[0].end, candidates[0].start + budget))]
    return sorted(chosen, key=lambda c: c.start)


def _motion_scores(path: str, max_analysis: float) -> list[tuple[float, float]]:
    """Downsample video to low-res grayscale, score change per 3-second bin."""
    # First 15 minutes are scanned for very long source videos to limit runtime.
    cmd = [binary("ffmpeg"), "-nostdin", "-hide_banner", "-v", "error",
           "-i", path, "-t", f"{max_analysis:.2f}", "-an",
           "-vf", "fps=1,scale=40:72,format=gray",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    length = 40 * 72
    previous = None
    bins = {}
    try:
        assert proc.stdout is not None
        frame_index = 0
        while True:
            raw = proc.stdout.read(length)
            if len(raw) < length:
                break
            current = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
            change = float(np.mean(np.abs(current - previous))) if previous is not None else 1.0
            bin_index = frame_index // 3
            bins.setdefault(bin_index, []).append(change)
            previous = current
            frame_index += 1
        assert proc.stderr is not None
        error = proc.stderr.read().decode("utf-8", "replace")
        if proc.wait():
            raise RuntimeError(f"Unable to inspect motion: {error[-500:]}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    return [(index * 3.0, float(np.mean(scores)))
            for index, scores in bins.items()]


def select_montage_cuts(path: str, duration: float, *, max_duration=MAX_DURATION) -> list[Cut]:
    if duration <= min(max_duration, TARGET_SECONDS):
        # Even short clips get a few fast-paced zoom edits.
        if duration <= 5:
            return [Cut(0.0, duration)]
        size = 3.8
        return [Cut(i * size, min(duration, (i + 1) * size))
                for i in range(math.ceil(duration / size))
                if i * size < duration]
    scores = _motion_scores(path, min(900.0, duration))
    if not scores:
        return [Cut(0.0, min(duration, 50))]
    # Pick ~14 active 3-second passages, preserving their original time order.
    max_clips = min(16, int(TARGET_SECONDS // 3.2))
    ranked = sorted(scores, key=lambda x: x[1], reverse=True)
    selected: list[float] = []
    for start, score in ranked:
        if len(selected) >= max_clips:
            break
        if start + 3.0 > duration:
            continue
        if all(abs(start - existing) >= 3.0 for existing in selected):
            selected.append(start)
    selected.sort()
    return [Cut(t, min(duration, t + 3.0)) for t in selected]


def enforce_limit(cuts: list[Cut], max_duration=MAX_DURATION,
                  speed=SPEED) -> list[Cut]:
    """Truncate only at the final clip boundary, keeping final media < 60s."""
    safe_budget = min(58.0, max_duration - 0.5) * speed
    selected: list[Cut] = []
    remaining = safe_budget
    for cut in sorted(cuts, key=lambda c: c.start):
        if remaining < 0.45:
            break
        length = min(cut.duration, remaining)
        if length >= 0.45:
            selected.append(Cut(cut.start, cut.start + length))
            remaining -= length
    return selected


def reel_word_timing(words: list[Word], cuts: list[Cut], speed=SPEED) -> list[Word]:
    mapped = []
    elapsed = 0.0
    for cut in cuts:
        for word in words:
            if word.start >= cut.start - 0.035 and word.end <= cut.end + 0.055:
                start = elapsed + max(0.0, word.start - cut.start) / speed
                end = elapsed + min(cut.duration, word.end - cut.start) / speed
                if end > start:
                    mapped.append(Word(round(start, 3), round(end, 3), word.text))
        elapsed += cut.duration / speed
    return mapped


def ass_timestamp(t: float) -> str:
    h = int(max(0, t)) // 3600
    m = int(max(0, t)) % 3600 // 60
    s = int(max(0, t)) % 60
    cs = int((max(0, t) % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def captions_ass(words: list[Word], destination: Path, *, width=1080, height=1920) -> bool:
    if not words:
        return False
    font_size = int(75 * width / 1080)
    outline = max(2, int(5 * width / 1080))
    margin = int(345 * height / 1920)
    header = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\n"
              "WrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
              "[V4+ Styles]\n"
              "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
              "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
              "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
              "Alignment, MarginL, MarginR, MarginV, Encoding\n"
              f"Style: Reel,Arial,{font_size},&H00FFFFFF,&H0012DDEA,"
              f"&H00151A2B,&H85000000,-1,0,0,0,100,100,0,0,1,"
              f"{outline},2,2,55,55,{margin},1\n\n"
              "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
              "MarginV, Effect, Text\n")
    events = []
    ordered = sorted(words, key=lambda w: w.start)
    chunks: list[list[Word]] = []
    group: list[Word] = []
    for w in ordered:
        if group and (len(group) >= 4 or w.start - group[-1].end > 0.35 or
                      len(" ".join(x.text for x in group)) > 26):
            chunks.append(group)
            group = []
        group.append(w)
    if group:
        chunks.append(group)
    for group in chunks:
        for index, active in enumerate(group):
            next_start = group[index + 1].start if index + 1 < len(group) else group[-1].end + 0.12
            start = max(0.0, active.start)
            end = max(start + 0.07, next_start)
            fragments = []
            for i, word in enumerate(group):
                # ASS uses BGR hex. Yellow active word, others white.
                color = "&H0033E9FF&" if i == index else "&H00FFFFFF&"
                fragments.append(r"{\c" + color + "}" + ass_escape(word.text).upper())
            events.append("Dialogue: 0," + ass_timestamp(start) + "," +
                          ass_timestamp(end) + ",Reel,,0,0,0,," + " ".join(fragments))
    destination.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return True


def generate_original_beat(destination: Path, seconds: float, bpm=110) -> Path:
    """Procedurally synthesize a non-copyrighted, self-contained instrumental bed."""
    sample_rate = 32000
    length = int(math.ceil(seconds + 1) * sample_rate)
    audio = np.zeros(length, dtype=np.float32)
    beat = 60.0 / bpm
    rng = np.random.default_rng(2027)
    # Minimal original percussive electronic beat: kick, clap, hat, warm bass.
    for step in range(int((seconds + 1) / (beat / 2)) + 1):
        start = int(step * beat / 2 * sample_rate)
        if start >= length:
            break
        if step % 2 == 0:
            n = min(int(0.22 * sample_rate), length - start)
            t = np.arange(n, dtype=np.float32) / sample_rate
            phase = 2 * np.pi * (60 * t + 45 * (1 - np.exp(-20 * t)) / 20)
            audio[start:start + n] += 0.40 * np.sin(phase) * np.exp(-22 * t)
        if step % 4 == 2:
            n = min(int(0.11 * sample_rate), length - start)
            t = np.arange(n, dtype=np.float32) / sample_rate
            noise = rng.standard_normal(n).astype(np.float32)
            audio[start:start + n] += 0.11 * noise * np.exp(-35 * t)
        n = min(int(0.055 * sample_rate), length - start)
        t = np.arange(n, dtype=np.float32) / sample_rate
        noise = rng.standard_normal(n).astype(np.float32)
        audio[start:start + n] += 0.042 * noise * np.exp(-78 * t)
    notes = [110.00, 130.81, 146.83, 130.81, 98.00, 123.47, 146.83, 123.47]
    for idx in range(int((seconds + 1) / beat) + 1):
        start = int(idx * beat * sample_rate)
        if start >= length:
            break
        n = min(int(beat * 0.85 * sample_rate), length - start)
        t = np.arange(n, dtype=np.float32) / sample_rate
        freq = notes[idx % len(notes)]
        envelope = np.minimum(1.0, t * 70) * np.exp(-3.5 * t)
        audio[start:start + n] += 0.06 * np.sin(2 * np.pi * freq * t) * envelope
    audio = np.tanh(audio * 1.5) * 0.65
    fade_len = min(int(0.4 * sample_rate), len(audio))
    audio[:fade_len] *= np.linspace(0, 1, fade_len)
    audio[-fade_len:] *= np.linspace(1, 0, fade_len)
    with wave.open(str(destination), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
    return destination


def _visual_filters(index: int, source: str, cut: Cut, *, width: int, height: int,
                    crop: str, speed: float) -> list[str]:
    interval = f"trim=start={cut.start:.3f}:end={cut.end:.3f},setpts=(PTS-STARTPTS)/{speed:.5f},fps={FPS}"
    base = f"[0:v]{interval}"
    if crop == "Backdrop":
        return [
            base + f",split=2[bg{index}][fg{index}]",
            f"[bg{index}]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=25:2[back{index}]",
            f"[fg{index}]scale={width}:{height}:force_original_aspect_ratio=decrease[front{index}]",
            f"[back{index}][front{index}]overlay=(W-w)/2:(H-h)/2,"
            f"format=yuv420p[v{index}]",
        ]
    # Gentle animated push-in and a changing base zoom on each cut.
    z_start = 1.0 if index % 3 == 0 else (1.03 if index % 3 == 1 else 1.055)
    return [
        base + f",scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"zoompan=z='min({z_start}+on*0.00025,1.095)':"
        "x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
        f"d=1:s={width}x{height}:fps={FPS},format=yuv420p[v{index}]"
    ]


def build_filter(cuts: list[Cut], *, has_audio: bool, music: bool, captions: bool,
                 crop: str, width: int, height: int, speed=SPEED,
                 source_audio_index: int = 0, music_index: int = 1) -> str:
    if not cuts:
        raise ValueError("No usable video clips were selected.")
    filters = []
    for idx, cut in enumerate(cuts):
        filters.extend(_visual_filters(idx, "", cut, width=width, height=height,
                                       crop=crop, speed=speed))
        audio_source = f"[{source_audio_index}:a:0]"
        filters.append(
            f"{audio_source}atrim=start={cut.start:.3f}:end={cut.end:.3f},"
            f"asetpts=PTS-STARTPTS,atempo={speed:.5f},aresample=48000[a{idx}]")
    labels = "".join(f"[v{i}][a{i}]" for i in range(len(cuts)))
    filters.append(f"{labels}concat=n={len(cuts)}:v=1:a=1[vc][ac]")
    if captions:
        filters.append("[vc]ass=captions.ass,format=yuv420p[outv]")
    else:
        filters.append("[vc]format=yuv420p[outv]")
    if music:
        duration = sum(c.duration for c in cuts) / speed
        voice_volume = 0.70 if not has_audio else 1.0
        music_volume = 0.33 if not has_audio else 0.095
        filters.append(f"[ac]volume={voice_volume}[voice]")
        filters.append(f"[{music_index}:a:0]atrim=duration={duration:.3f},"
                       f"asetpts=PTS-STARTPTS,volume={music_volume:.3f}[music]")
        filters.append("[voice][music]amix=inputs=2:duration=first:dropout_transition=0[outa]")
    else:
        filters.append("[ac]anull[outa]")
    return ";\n".join(filters)


def render(source: Media, cuts: list[Cut], words: list[Word],
           destination: Path, temp: Path, *, crop="Dynamic crop", music="Auto",
           resolution="1080p", log: Callable[[str], None] | None = None,
           progress: Callable[[float], None] | None = None) -> dict:
    log = log or (lambda _: None)
    progress = progress or (lambda _: None)
    if not cuts:
        raise ValueError("No clips could be selected. Check the input media.")
    width, height = (1080, 1920) if resolution == "1080p" else (720, 1280)
    length = sum(c.duration for c in cuts) / SPEED
    if length >= 59.5:
        raise ValueError("Internal duration limit exceeded.")
    local_words = reel_word_timing(words, cuts)
    use_music = music == "On" or (music == "Auto" and len(local_words) < 8)
    has_captions = captions_ass(local_words, temp / "captions.ass",
                                width=width, height=height)
    audio_source = 0 if source.has_audio else 1
    music_index = 1 if source.has_audio else 2
    inputs = ["-i", source.path]
    if not source.has_audio:
        inputs += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    if use_music:
        generated = generate_original_beat(temp / "original_music.wav", length)
        inputs += ["-i", str(generated)]
    graph = build_filter(cuts, has_audio=source.has_audio, music=use_music,
                         captions=has_captions, crop=crop, width=width,
                         height=height, source_audio_index=audio_source,
                         music_index=music_index)
    (temp / "filters.txt").write_text(graph, encoding="utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    cmd = [binary("ffmpeg"), "-y", "-nostdin", "-hide_banner",
           "-loglevel", "error", "-progress", "pipe:1",
           "-fflags", "+genpts", *inputs, "-filter_complex", graph,
           "-map", "[outv]", "-map", "[outa]",
           "-map_metadata", "-1", "-map_chapters", "-1",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
           "-r", str(FPS), "-movflags", "+faststart", "-t", f"{min(59.0,length):.3f}",
           str(destination)]
    log(f"Editing {len(cuts)} segments at {width}x{height}; final length {length:.1f}s")
    log("Captions: " + ("animated word highlights" if has_captions else "none (no detected speech)"))
    log("Music: " + ("original generated instrumental bed" if use_music else "off"))
    # Stderr is redirected to a file, avoiding a blocked pipe when rendering.
    error_path = temp / "ffmpeg_errors.txt"
    with error_path.open("wb") as err:
        process = subprocess.Popen(cmd, cwd=str(temp), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=err)
        try:
            assert process.stdout is not None
            for raw in process.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("out_time_ms="):
                    try:
                        elapsed = int(line.split("=", 1)[1]) / 1_000_000
                        progress(min(0.99, elapsed / max(1.0, length)))
                    except ValueError:
                        pass
            result = process.wait()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    if result != 0:
        destination.unlink(missing_ok=True)
        raise RuntimeError("Video render failed:\n" +
                           error_path.read_text(encoding="utf-8", errors="replace")[-2200:])
    output_info = probe(destination)
    if output_info.duration >= 60.0 or output_info.duration < 0.3:
        destination.unlink(missing_ok=True)
        raise RuntimeError("Export duration verification failed.")
    if not output_info.has_audio:
        destination.unlink(missing_ok=True)
        raise RuntimeError("Exported video is missing its audio stream.")
    progress(1.0)
    return {
        "file": str(destination), "duration": output_info.duration,
        "size": [output_info.width, output_info.height],
        "captions": bool(has_captions), "original_music": bool(use_music),
        "cuts": [asdict(c) for c in cuts],
    }


def make_reel(path: str, output_folder: str, *, crop="Dynamic crop",
              music="Auto", resolution="1080p",
              log: Callable[[str], None] | None = None,
              progress: Callable[[float], None] | None = None,
              transcript_override: list[Word] | None = None) -> dict:
    log = log or (lambda _: None)
    progress = progress or (lambda _: None)
    source = probe(path)
    if source.duration < 0.6:
        raise ValueError("The input video is too short to edit.")
    folder = Path(output_folder).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    stem = "ReelForge_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    target_folder = folder / stem
    n = 2
    while target_folder.exists():
        target_folder = folder / f"{stem}_{n}"
        n += 1
    target_folder.mkdir()
    log(f"Source video: {Path(source.path).name} ({source.duration:.1f}s)")
    progress(0.03)
    try:
        words = (list(transcript_override) if transcript_override is not None else
                 (transcribe(source.path, log) if source.has_audio else []))
        progress(0.18)
        speech = select_speech_cuts(words, source.duration) if words else []
        if speech:
            log(f"Choosing spoken highlights from {len(speech)} cuts.")
            cuts = enforce_limit(speech)
            selection = "speech highlights"
        else:
            log("No usable spoken sections — creating a motion-led montage.")
            cuts = enforce_limit(select_montage_cuts(source.path, source.duration))
            selection = "motion montage"
        if not cuts:
            cuts = [Cut(0.0, min(10.0, source.duration))]
        progress(0.28)
        destination = target_folder / "ReelForge_Final_Reel.mp4"
        with tempfile.TemporaryDirectory(prefix="reelforge_") as tmp:
            meta = render(source, cuts, words, destination, Path(tmp), crop=crop,
                          music=music, resolution=resolution, log=log,
                          progress=lambda f: progress(0.28 + f * 0.70))
        meta.update({"selection": selection, "source": source.path,
                     "original_duration": source.duration, "crop": crop,
                     "music_setting": music, "resolution": resolution,
                     "transcription": [asdict(w) for w in reel_word_timing(words, cuts)]})
        (target_folder / "Edit_Decisions.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        progress(1.0)
        log(f"DONE — {meta['duration']:.1f}s reel, stored in {target_folder}")
        return meta
    except Exception:
        # Keep the log directory for troubleshooting; delete partial video.
        (target_folder / "ReelForge_Final_Reel.mp4").unlink(missing_ok=True)
        raise
