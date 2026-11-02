"""Noninteractive GUI regression tests; suitable for Windows CI."""
import os
from pathlib import Path

# Must be set before QApplication is created on a CI runner.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from syncforge_studio import MediaPanel, SyncForgeWindow, VIDEO_EXT, AUDIO_EXT


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    yield instance


def test_media_list_sorting_and_duplicate_prevention(app, tmp_path):
    first = tmp_path / "camera10.mp4"
    second = tmp_path / "camera2.mov"
    irrelevant = tmp_path / "notes.txt"
    for p in (first, second, irrelevant):
        p.write_bytes(b"example")
    panel = MediaPanel("video", "Camera", "Files", VIDEO_EXT)
    panel.add_paths([str(first), str(second), str(first), str(irrelevant)])
    assert len(panel.paths) == 2
    assert panel.list.count() == 2
    assert panel.counter.text() == "2 FILES"
    assert panel.paths[0].endswith("camera2.mov")
    assert panel.paths[1].endswith("camera10.mp4")
    panel.list.item(0).setSelected(True)
    panel.remove_selected()
    assert len(panel.paths) == 1
    panel.clear()
    assert not panel.paths
    assert panel.counter.text() == "0 FILES"
    panel.close()


def test_audio_extensions_and_missing_files(app, tmp_path):
    audio = tmp_path / "recorder.wav"
    video_only = tmp_path / "clip.avi"
    audio.write_bytes(b"abc")
    video_only.write_bytes(b"xyz")
    panel = MediaPanel("audio", "External", "Files", AUDIO_EXT)
    panel.add_paths([str(audio), str(video_only), str(tmp_path / "missing.wav")])
    assert panel.paths == [str(audio.resolve())]
    assert panel.counter.text() == "1 FILE"
    panel.close()


def test_window_controls_and_advanced_animation(app, monkeypatch, tmp_path):
    window = SyncForgeWindow()
    assert window.video_panel.kind == "video"
    assert window.audio_panel.kind == "audio"
    assert window.start_button.isEnabled()
    assert not window.open_button.isEnabled()
    assert window.container.currentText() == "mp4"
    window.container.setCurrentText("mov")
    assert window.container.currentText() == "mov"
    window._set_progress(58, animate=False)
    assert window.progress.value() == 58
    window.advanced_button.setChecked(True)
    assert window.advanced_button.isChecked()
    window.manual.setText("-2.5")
    assert window.manual.text() == "-2.5"
    notices = []
    monkeypatch.setattr(window, "_notify", lambda title, detail: notices.append((title, detail)))
    window.start_job()
    assert len(notices) == 1
    assert "Add at least one" in notices[0][1]
    window.close()
