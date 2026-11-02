"""Local Ollama vision editor: keeps transcripts and source frames on the PC."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_MODEL = "gemma3:4b"
BASE_URL = "http://127.0.0.1:11434"


def ollama_request(endpoint: str, payload=None, timeout: int = 18) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(BASE_URL + endpoint, data=body,
                      headers={"Content-Type": "application/json"},
                      method="POST" if body is not None else "GET")
    try:
        with urlopen(request, timeout=timeout) as result:
            return json.load(result)
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8", "replace")).get("error", "")
        except Exception:
            detail = str(exc)
        raise RuntimeError(
            "Local AI rejected the request: " + detail + ". Try running: ollama pull "
            + os.getenv("OLLAMA_MODEL", DEFAULT_MODEL)
        ) from exc
    except (URLError, TimeoutError, ConnectionError) as exc:
        raise RuntimeError(
            "Ollama is not running on your computer. Start Ollama and try again."
        ) from exc


def status() -> dict:
    name = os.getenv("OLLAMA_MODEL", DEFAULT_MODEL)
    try:
        tags = ollama_request("/api/tags", timeout=3)
        names = [item.get("name", "") for item in tags.get("models", [])]
        present = name in names
        return {
            "provider": "ollama", "model": name, "available": present,
            "message": ("Local AI ready (" + name + ").") if present else
                       ("Model missing. Run: ollama pull " + name)
        }
    except Exception:
        return {"provider": "ollama", "model": name, "available": False,
                "message": "Ollama isn't running. Start Ollama on this computer."}


def _parse_json(text: str) -> dict:
    content = text.strip()
    if content.startswith("~~~"):
        content = content.replace("~~~json", "", 1).replace("~~~", "")
    try:
        data = json.loads(content.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("Local AI returned invalid JSON. Retry with a shorter video.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise RuntimeError("Local AI returned no usable edit timeline.")
    return data


def local_edit_plan(instructions: str, message: str, folder: Path,
                    frames: list[dict], schema: dict) -> dict:
    images, times = [], []
    for frame in frames[:8]:
        path = folder / "frames" / frame["file"]
        if path.is_file():
            images.append(base64.b64encode(path.read_bytes()).decode("ascii"))
            times.append(str(frame.get("time", "?")))
    if not images:
        raise RuntimeError("No frames could be sampled from this video.")
    model = os.getenv("OLLAMA_MODEL", DEFAULT_MODEL)
    guidance = (
        "You are a professional reel editor working LOCALLY. You see sampled frames "
        "from the ORIGINAL video, with their source timestamps in seconds: "
        + ", ".join(times) + ". Images are supplied in that order. "
        "Cross-reference these with timestamped speech. Return only a JSON object "
        "with all required fields. Don't invent events or source timestamps."
    )
    payload = {
        "model": model, "stream": False, "format": schema,
        "messages": [
            {"role": "system", "content": instructions + " " + guidance},
            {"role": "user", "content": message, "images": images},
        ],
        "options": {"temperature": 0.15, "num_ctx": 12288, "num_predict": 3200},
        "keep_alive": "5m",
    }
    response = ollama_request(
        "/api/chat", payload,
        timeout=max(60, int(os.getenv("LOCAL_AI_TIMEOUT_SECONDS", "900"))))
    return _parse_json((response.get("message") or {}).get("content") or "")
