"""Media analysis and exports for SyncForge. No UI dependencies.

Offsets are measured from the video start: +2 means recorder audio starts
2 seconds AFTER the camera; -2 means recorder audio starts 2 seconds BEFORE.
"""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import numpy as np

SAMPLE_RATE = 8000
HOP = 160  # 50 feature frames per second
FEATURE_RATE = SAMPLE_RATE / HOP
MIN_CONFIDENCE = 0.56
MIN_MARGIN = 0.055


@dataclass(frozen=True)
class MediaInfo:
    path: str
    duration: float
    video: bool
    audio: bool
    fps: float = 30.0
    width: int = 1920
    height: int = 1080


@dataclass(frozen=True)
class Match:
    video: str
    audio: str
    offset: float
    confidence: float
    alternative: float = 0.0


def find_binary(name: str) -> str:
    """Find FFmpeg beside the packaged EXE, or on PATH for development."""
    folders = []
    if getattr(sys, "frozen", False):
        exe_folder = Path(sys.executable).resolve().parent
        folders += [exe_folder, exe_folder / "bin", Path(getattr(sys, "_MEIPASS", exe_folder)) / "bin"]
    folders += [Path(__file__).resolve().parent / "bin"]
    for folder in folders:
        candidate = folder / (name + ".exe" if os.name == "nt" else name)
        if candidate.is_file():
            return str(candidate)
    result = shutil.which(name) or shutil.which(name + ".exe")
    if result:
        return result
    raise RuntimeError(f"{name} not found. Download the complete SyncForge-Windows ZIP and extract it before opening the EXE.")


def ff(cmd: list[str]) -> bytes:
    result = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise RuntimeError(f"FFmpeg error: {result.stderr.decode('utf-8', 'replace')[-1400:]}")
    return result.stdout


def probe(path: str | Path) -> MediaInfo:
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Missing media file: {path}")
    payload = json.loads(ff([find_binary("ffprobe"), "-v", "error",
                             "-show_entries", "format=duration:stream=codec_type,width,height,avg_frame_rate,duration",
                             "-of", "json", str(path)]))
    streams = payload.get("streams", [])
    vs = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = any(s.get("codec_type") == "audio" for s in streams)
    d = payload.get("format", {}).get("duration") or next(
        (s.get("duration") for s in streams if s.get("duration")), 0)
    duration = float(d or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"Cannot determine duration: {path.name}")
    fps = 30.0
    if vs:
        try:
            fps = float(Fraction(vs.get("avg_frame_rate", "30/1")))
            if fps <= 0 or not math.isfinite(fps):
                fps = 30.0
        except (ValueError, ZeroDivisionError):
            pass
    return MediaInfo(str(path), duration, bool(vs), a, fps,
                     int(vs.get("width") or 1920) if vs else 1920,
                     int(vs.get("height") or 1080) if vs else 1080)


def envelope(path: str | Path, start: float = 0.0, seconds: float | None = None) -> np.ndarray:
    """Stream downsampled audio into RAM-bounded 20ms logarithmic RMS features."""
    cmd = [find_binary("ffmpeg"), "-nostdin", "-hide_banner", "-v", "error",
           "-ss", str(max(0.0, start)), "-i", str(path)]
    if seconds is not None:
        cmd += ["-t", str(seconds)]
    cmd += ["-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-f", "f32le", "-acodec", "pcm_f32le", "-"]
    process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    features = []
    leftover = b""
    try:
        assert process.stdout is not None
        while True:
            piece = process.stdout.read(HOP * 4 * 4096)
            if not piece:
                break
            buf = leftover + piece
            size = (len(buf) // (HOP * 4)) * (HOP * 4)
            if size:
                arr = np.frombuffer(buf[:size], dtype="<f4").reshape(-1, HOP)
                rms = np.sqrt(np.mean(arr.astype(np.float64) ** 2, axis=1))
                features.append(np.log1p(rms * 1000.0))
            leftover = buf[size:]
        assert process.stderr is not None
        error = process.stderr.read().decode("utf-8", "replace")
        status = process.wait()
        if status:
            raise RuntimeError(f"Cannot decode audio from {Path(path).name}: {error[-900:]}")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    if not features:
        raise ValueError(f"No readable audio from {Path(path).name}")
    return np.concatenate(features)


def correlation_scores(rec: np.ndarray, cam: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Overlap-aware Pearson correlation over every physically possible lag.

    Lag > 0 means the camera segment occurs later IN the recorder file.
    Lag < 0 means the camera segment begins before the recorder file begins.
    """
    rec = np.asarray(rec, dtype=np.float64)
    cam = np.asarray(cam, dtype=np.float64)
    if len(rec) < 200 or len(cam) < 200:
        raise ValueError("At least four seconds of usable camera and recorder audio are required.")
    n, m = len(rec), len(cam)
    size = n + m - 1
    fft_size = 1 << (size - 1).bit_length()
    dots = np.fft.irfft(np.fft.rfft(rec, fft_size) *
                        np.fft.rfft(cam[::-1], fft_size), fft_size)[:size]
    lags = np.arange(-m + 1, n, dtype=np.int64)
    rs = np.maximum(lags, 0)
    cs = np.maximum(-lags, 0)
    counts = np.minimum(n - rs, m - cs)
    min_overlap = min(n, m, max(200, min(1200, int(0.60 * m))))
    valid = counts >= min_overlap

    rsum = np.r_[0.0, np.cumsum(rec)]
    csum = np.r_[0.0, np.cumsum(cam)]
    rsq = np.r_[0.0, np.cumsum(rec * rec)]
    csq = np.r_[0.0, np.cumsum(cam * cam)]
    sx = rsum[rs + counts] - rsum[rs]
    sy = csum[cs + counts] - csum[cs]
    vx = (rsq[rs + counts] - rsq[rs]) - sx * sx / counts
    vy = (csq[cs + counts] - csq[cs]) - sy * sy / counts
    numer = dots - sx * sy / counts
    scores = numer / np.sqrt(np.maximum(vx * vy, 1e-20))
    scores[~valid | (vx < counts * 0.002) | (vy < counts * 0.002)] = -np.inf
    return lags, scores


def offset_search(camera_features: np.ndarray, recorder_features: np.ndarray,
                  camera_excerpt_start: float = 0) -> tuple[float, float, float]:
    lags, scores = correlation_scores(recorder_features, camera_features)
    best = int(np.argmax(scores))
    confidence = float(scores[best])
    if not math.isfinite(confidence):
        raise ValueError("No distinctive overlapping audio; check that camera audio was recorded.")
    # Second unrelated peak helps identify repeated songs and other ambiguous audio.
    unrelated = np.abs(lags - lags[best]) > int(2 * FEATURE_RATE)
    second = float(np.max(scores[unrelated])) if np.any(unrelated) else -1.0
    offset = camera_excerpt_start - float(lags[best]) / FEATURE_RATE
    return round(offset, 3), confidence, second


def excerpt_starts(duration: float) -> list[float]:
    """Try the opening, middle and late portions of long camera recordings."""
    if duration <= 100:
        return [0.0]
    span = min(90.0, duration)
    starts = [0.0, (duration - span) / 2, duration - span]
    return sorted(set(round(s, 3) for s in starts))


def analyze(videos: list[str], audios: list[str], log=None) -> tuple[list[Match], list[tuple[str, str]]]:
    """Auto-pair every camera clip to its best recorder audio.

    One long recorder file can serve multiple camera clips. Ambiguous or weak
    results are skipped, never silently guessed.
    """
    log = log or (lambda _: None)
    if not videos or not audios:
        raise ValueError("Add at least one video and one external audio file.")
    recorder: dict[str, np.ndarray] = {}
    for audio in audios:
        ai = probe(audio)
        if not ai.audio:
            raise ValueError(f"Not an audio recording: {Path(audio).name}")
        log(f"Reading recorder: {Path(audio).name}")
        recorder[audio] = envelope(audio)
    matches: list[Match] = []
    problems: list[tuple[str, str]] = []
    for video in videos:
        try:
            vi = probe(video)
            if not vi.video or not vi.audio:
                raise ValueError("Camera video must contain a reference/scratch audio track.")
            candidates: list[tuple[float, float, float, str]] = []
            for start in excerpt_starts(vi.duration):
                snippet = envelope(video, start, min(90.0, vi.duration - start))
                if len(snippet) < 200:
                    continue
                for audio, full in recorder.items():
                    try:
                        offset, score, second = offset_search(snippet, full, start)
                    except ValueError:
                        continue
                    if math.isfinite(score):
                        candidates.append((score, offset, second, audio))
            if not candidates:
                raise ValueError("No usable audio overlap. Check the camera scratch track.")
            candidates.sort(key=lambda x: x[0], reverse=True)
            confidence, offset, second, audio = candidates[0]
            # Different excerpts of the SAME camera file can confirm a match.
            competitors = [x for x in candidates[1:]
                           if x[3] != audio or abs(x[1] - offset) > 0.5]
            alternative = max([second] + [x[0] for x in competitors], default=-1.0)
            if confidence < MIN_CONFIDENCE:
                raise ValueError(f"Low-confidence waveform match ({confidence:.2f}); no export made.")
            if alternative > confidence - MIN_MARGIN:
                raise ValueError(f"Ambiguous waveform match ({confidence:.2f} vs {alternative:.2f}); no export made.")
            ai = probe(audio)
            overlap = min(vi.duration, offset + ai.duration) - max(0.0, offset)
            if overlap < 4:
                raise ValueError("The matched audio barely overlaps the video.")
            matches.append(Match(video, audio, offset, round(confidence, 4),
                                 round(alternative, 4)))
            log(f"MATCH  {Path(video).name}  ⇔  {Path(audio).name}   "
                f"offset {offset:+.3f}s  confidence {confidence:.2f}")
        except Exception as exc:
            problems.append((video, str(exc)))
            log(f"SKIPPED {Path(video).name}: {exc}")
    return matches, problems


def safe_name(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text).strip(" .")[:100] or "clip"


def export_video(match: Match, destination: str | Path, log=None) -> Path:
    """Produce synchronized playable media, preserving the camera's video track."""
    log = log or (lambda _: None)
    vi = probe(match.video)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.resolve() == Path(match.video).resolve():
        raise ValueError("Output must not overwrite its input.")
    audio_filter = (f"[1:a:0]adelay={round(match.offset * 1000)}:all=1"
                    if match.offset >= 0 else
                    f"[1:a:0]atrim=start={-match.offset:.3f},asetpts=PTS-STARTPTS")
    audio_filter += (f",apad,atrim=duration={vi.duration:.6f},"
                     "asetpts=N/SR/TB[aout]")
    args = [find_binary("ffmpeg"), "-y", "-nostdin", "-hide_banner", "-v", "error",
            "-i", str(match.video), "-i", str(match.audio),
            "-filter_complex", audio_filter, "-map", "0:v:0", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000", "-t", f"{vi.duration:.6f}",
            "-movflags", "+faststart", str(target)]
    try:
        try:
            ff(args)
        except RuntimeError:
            target.unlink(missing_ok=True)
            log(f"Video stream copy unsupported for {Path(match.video).name}; re-encoding H.264.")
            at = args.index("-c:v")
            args[at + 1:at + 2] = ["libx264", "-preset", "medium", "-crf", "18",
                                  "-pix_fmt", "yuv420p"]
            ff(args)
        result = probe(target)
        if not result.video or not result.audio or abs(result.duration - vi.duration) > 1.0:
            raise RuntimeError("Export validation failed: duration or streams are incorrect.")
        return target
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _add(parent, tag, value=None, **attrs):
    child = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if value is not None:
        child.text = str(value)
    return child


def premiere_xml(paths: list[str | Path], destination: str | Path) -> Path:
    """Write FCP7 interchange XML importable by Premiere Pro.

    Outputs are placed consecutively on a single video and audio track. Audio
    is synchronized into the exported videos, so the timeline needs no offsets.
    """
    if not paths:
        raise ValueError("No synchronized clips to include in timeline.")
    infos = [probe(p) for p in paths]
    first = infos[0]
    timebase = max(1, round(first.fps))
    ntsc = abs(first.fps - timebase * 1000 / 1001) < 0.05
    root = ET.Element("xmeml", version="5")
    seq = _add(root, "sequence", id="sequence-1")
    _add(seq, "name", "SyncForge - Synchronized Clips")
    rate = _add(seq, "rate")
    _add(rate, "timebase", timebase)
    _add(rate, "ntsc", str(ntsc).upper())
    total_frames = sum(max(1, round(info.duration * first.fps)) for info in infos)
    _add(seq, "duration", total_frames)
    media = _add(seq, "media")
    video = _add(media, "video")
    fmt = _add(video, "format")
    spec = _add(fmt, "samplecharacteristics")
    rate2 = _add(spec, "rate")
    _add(rate2, "timebase", timebase)
    _add(rate2, "ntsc", str(ntsc).upper())
    _add(spec, "width", first.width)
    _add(spec, "height", first.height)
    _add(spec, "anamorphic", "FALSE")
    _add(spec, "pixelaspectratio", "square")
    vt = _add(video, "track")
    audio = _add(media, "audio")
    _add(audio, "numOutputChannels", 2)
    at = _add(audio, "track")
    cursor = 0
    for idx, info in enumerate(infos, 1):
        path = Path(info.path).resolve()
        frames = max(1, round(info.duration * first.fps))
        for kind, track in (("video", vt), ("audio", at)):
            item = _add(track, "clipitem", id=f"clip-{kind}-{idx}")
            _add(item, "name", path.name)
            _add(item, "duration", frames)
            _add(item, "start", cursor)
            _add(item, "end", cursor + frames)
            _add(item, "in", 0)
            _add(item, "out", frames)
            f = _add(item, "file", id=f"file-{idx}")
            if kind == "video":
                _add(f, "name", path.name)
                _add(f, "pathurl", path.as_uri())
                _add(f, "duration", frames)
                fr = _add(f, "rate")
                _add(fr, "timebase", timebase)
                _add(fr, "ntsc", str(ntsc).upper())
                fm = _add(f, "media")
                fv = _add(fm, "video")
                characteristics = _add(fv, "samplecharacteristics")
                _add(characteristics, "width", info.width)
                _add(characteristics, "height", info.height)
                fa = _add(fm, "audio")
                _add(fa, "channelcount", 2)
            source = _add(item, "sourcetrack")
            _add(source, "mediatype", kind)
            _add(source, "trackindex", 1)
        cursor += frames
    _add(vt, "enabled", "TRUE")
    _add(at, "enabled", "TRUE")
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, "  ")
    tree.write(output, encoding="utf-8", xml_declaration=True)
    return output


def write_report(matches: list[Match], problems: list[tuple[str, str]],
                 output: str | Path) -> Path:
    report = Path(output)
    with report.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["video", "recorder_audio", "offset_seconds",
                         "confidence", "status", "notes"])
        for match in matches:
            writer.writerow([Path(match.video).name, Path(match.audio).name,
                             match.offset, match.confidence, "matched", ""])
        for video, why in problems:
            writer.writerow([Path(video).name, "", "", "", "needs review", why])
    return report
