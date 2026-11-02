"""Regression tests for local AI editing; never contact the Ollama server in CI."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import local_ai
from engine import ai_plan, json_schema, validate_timeline


def prepared_project(folder: Path):
    photos = folder / "frames"
    photos.mkdir()
    (photos / "frame0.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (photos / "frame1.jpg").write_bytes(b"\xff\xd8\xc0\xff\xd9")
    return {
        "media": {"duration": 37.0, "width": 1280, "height": 720,
                  "has_audio": True},
        "words": [
            {"start": 0.2, "end": 0.4, "text": "Hello"},
            {"start": 0.5, "end": 0.8, "text": "world"},
        ],
        "frames": [
            {"time": 3.2, "file": "frame0.jpg"},
            {"time": 15.6, "file": "frame1.jpg"},
        ],
        "timeline": None,
    }


def sample_plan():
    return {
        "title": "Short edit", "hook": "Hello", "notes": "Keep it punchy",
        "fit": "crop", "music": "none", "caption_style": "bold",
        "segments": [
            {"start": 0.2, "end": 3.4, "zoom": 1.03, "speed": 1.07,
             "focus_x": .5, "transition": "cut", "reason": "Good opening"},
            {"start": 5.1, "end": 8.0, "zoom": 1.06, "speed": 1.05,
             "focus_x": .52, "transition": "cut", "reason": "Good ending"}
        ],
    }


def test_vision_model_receives_true_sampled_frames_and_words(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "gemma3:4b")
    original = prepared_project(tmp_path)
    captures = []

    def fake_ollama(endpoint, payload=None, timeout=20):
        assert endpoint == "/api/chat"
        captures.append(payload)
        return {"message": {"role": "assistant", "content": json.dumps(sample_plan())}}

    monkeypatch.setattr(local_ai, "ollama_request", fake_ollama)
    proposal = ai_plan(original, tmp_path)
    assert len(captures) == 1
    sent = captures[0]
    assert sent["model"] == "gemma3:4b"
    assert sent["stream"] is False
    assert sent["format"]["type"] == "object"
    assert len(sent["messages"][1]["images"]) == 2
    assert "3.2" in sent["messages"][0]["content"]
    assert "[0.2] Hello" in sent["messages"][1]["content"]
    assert base64.b64decode(sent["messages"][1]["images"][0]) == b"\xff\xd8\xff\xd9"
    edited = validate_timeline(proposal, original["media"]["duration"])
    assert edited["duration"] < 58


def test_local_ai_chat_revises_existing_edit(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    source = prepared_project(tmp_path)
    source["timeline"] = validate_timeline(sample_plan(), 37)
    captured = []
    def fake_ollama(endpoint, payload=None, timeout=20):
        captured.append(payload)
        response = sample_plan()
        response["segments"][0]["zoom"] = 1.08
        return {"message": {"content": json.dumps(response)}}
    monkeypatch.setattr(local_ai, "ollama_request", fake_ollama)
    edited = ai_plan(source, tmp_path, "Zoom in more on the opening")
    assert edited["segments"][0]["zoom"] == 1.08
    assert "Zoom in more" in captured[0]["messages"][1]["content"]
    assert "CURRENT TIMELINE" in captured[0]["messages"][1]["content"]


def test_local_model_readiness_uses_real_ollama_tags(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "gemma3:4b")
    monkeypatch.setattr(local_ai, "ollama_request", lambda endpoint, **kw:
                        {"models": [{"name": "gemma3:4b"}]})
    assert local_ai.status()["available"] is True
    monkeypatch.setattr(local_ai, "ollama_request", lambda endpoint, **kw:
                        {"models": []})
    assert local_ai.status()["available"] is False


def test_server_stores_user_media_locally_and_only_binds_loopback(monkeypatch, tmp_path):
    import local_server
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    local_server.setup_local_paths()
    assert local_server.os.environ["AI_PROVIDER"] == "ollama"
    assert str(tmp_path) in local_server.os.environ["REEL_DATA_DIR"]
    assert local_server.os.environ["REQUIRE_AUTH"] == "0"
    assert "127.0.0.1" in local_ai.BASE_URL
