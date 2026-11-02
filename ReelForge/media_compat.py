"""Tolerant container inspection and local media recovery for ReelForge.

Never modifies the original video. Explicitly strips problematic nonessential
metadata from a temporary copy when normal playback/metadata loading fails.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
from typing import Callable


class MediaMetadataError(ValueError):
    """Video container could not be inspected safely."""


def parse_duration(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        value = value.strip()
        if not value or value.lower() in ("n/a", "nan", "none", "inf"):
            return None
        # Matroska and some editor exports put time in stream tags.
        if ":" in value:
            match = re.fullmatch(r"(\d+):(\d{1,2}):(\d{1,2})(?:\.(\d+))?", value)
            if not match:
                return None
            hours, mins, secs, frac = match.groups()
            seconds = int(hours) * 3600 + int(mins) * 60 + int(secs)
            if frac:
                seconds += float("0." + frac)
        else:
            try:
                seconds = float(value)
            except ValueError:
                return None
    else:
        return None
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def _rate(value) -> float | None:
    try:
        if isinstance(value, str) and "/" in value:
            numer, den = value.split("/", 1)
            result = float(numer) / float(den)
        else:
            result = float(value)
        return result if math.isfinite(result) and result > 0 else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def read_media_payload(payload: dict) -> tuple[float, bool, int, int]:
    """Return seconds, has-audio, width, height from imperfect ffprobe metadata."""
    if not isinstance(payload, dict):
        raise MediaMetadataError("Invalid media metadata response.")
    streams = payload.get("streams") or []
    videos = [s for s in streams if s.get("codec_type") == "video"
              and not int(s.get("disposition", {}).get("attached_pic", 0) or 0)]
    if not videos:
        raise MediaMetadataError("No decodable video stream was identified.")
    video = videos[0]
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    durations = [parse_duration((payload.get("format") or {}).get("duration"))]
    for stream in [video] + streams:
        durations += [
            parse_duration(stream.get("duration")),
            parse_duration((stream.get("tags") or {}).get("DURATION")),
            parse_duration((stream.get("tags") or {}).get("duration")),
        ]
        timebase = _rate(stream.get("time_base"))
        ticks = parse_duration(stream.get("duration_ts"))
        if timebase and ticks:
            durations.append(ticks * timebase)
        fps = _rate(stream.get("avg_frame_rate")) or _rate(stream.get("r_frame_rate"))
        frames = parse_duration(stream.get("nb_frames"))
        if fps and frames:
            durations.append(frames / fps)
    seconds = next((d for d in durations if d is not None), None)
    if seconds is None:
        raise MediaMetadataError(
            "This video's container does not provide a readable duration. "
            "ReelForge can attempt a temporary remux. "
            "If that fails, re-export the video as MP4 (H.264 + AAC).")
    try:
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
    except (ValueError, TypeError):
        width = height = 0
    return seconds, has_audio, width, height


def inspect(path: str | Path, ffprobe: str, execute: Callable) -> tuple[float, bool, int, int]:
    """Try standard probing, then larger probe buffers for unusual phone/editor files."""
    file = Path(path)
    if not file.is_file():
        raise MediaMetadataError(f"Video file not found: {file}")
    if file.stat().st_size < 128:
        raise MediaMetadataError(
            "This file is empty or incomplete. Copy/download the original video again.")
    variants = [
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(file)],
        [ffprobe, "-v", "error", "-analyzeduration", "100000000",
         "-probesize", "100000000", "-fflags", "+genpts",
         "-show_streams", "-show_format", "-of", "json", str(file)],
    ]
    errors = []
    for args in variants:
        try:
            raw = execute(args)
            return read_media_payload(json.loads(raw))
        except (ValueError, RuntimeError, json.JSONDecodeError, OSError) as exc:
            errors.append(str(exc)[-450:])
    raise MediaMetadataError(
        "Unable to read video metadata after retrying with extended inspection. "
        "The file might be an incomplete download, a damaged MP4 header, "
        "or an unusual phone/editor export. " + " | ".join(errors[-2:]))


def extract_wav(video: str | Path, destination: str | Path, ffmpeg: str,
                execute: Callable) -> Path:
    """Decode audio with bundled FFmpeg so Whisper never parses video container tags."""
    source = Path(video)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-y", "-nostdin", "-hide_banner", "-v", "error",
        "-fflags", "+genpts", "-err_detect", "ignore_err",
        "-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn",
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        "-f", "wav", str(target),
    ]
    try:
        execute(command)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if not target.is_file() or target.stat().st_size < 32044:
        target.unlink(missing_ok=True)
        raise ValueError(
            "The audio track could not be decoded into usable speech audio.")
    return target


def remux_source(source: str | Path, destination: str | Path,
                 ffmpeg: str, execute: Callable) -> Path:
    """Repackage streams with regenerated timestamps and no third-party metadata."""
    original = Path(source)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-y", "-nostdin", "-hide_banner", "-v", "error",
        "-fflags", "+genpts", "-err_detect", "ignore_err",
        "-analyzeduration", "100000000", "-probesize", "100000000",
        "-i", str(original),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1",
        "-c", "copy", "-avoid_negative_ts", "make_zero",
        "-f", "matroska", str(target),
    ]
    try:
        execute(command)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if not target.is_file() or target.stat().st_size < 128:
        target.unlink(missing_ok=True)
        raise RuntimeError("Temporary metadata repair did not produce a playable file.")
    return target
