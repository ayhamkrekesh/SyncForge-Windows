"""Qt interface test running without a display."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from reel_studio import ReelWindow, VIDEO_EXT


def test_reelforge_window_and_controls(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    window = ReelWindow()
    assert not window.open_button.isEnabled()
    assert window.go.isEnabled()
    assert window.music.currentText() == "Auto"
    assert window.reframe.currentText() == "Dynamic crop"
    assert window.quality.currentText() == "1080p"
    assert ".mp4" in VIDEO_EXT
    window.music.setCurrentText("Off")
    assert window.music.currentText() == "Off"
    notices = []
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: notices.append(a))
    window.start()
    assert notices
    assert not window.input_path
    window.close()
