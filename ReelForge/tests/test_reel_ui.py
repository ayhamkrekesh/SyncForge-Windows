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



def test_metadata_warning_allows_video_and_error_report_is_copyable(monkeypatch, tmp_path):
    import reel_studio
    from PySide6.QtWidgets import QMessageBox
    app = QApplication.instance() or QApplication([])
    window = ReelWindow()
    input_file = tmp_path / "difficult video.mp4"
    input_file.write_bytes(b"sample" * 200)
    def fail_metadata(_path):
        raise ValueError("metadata duration not readable")
    monkeypatch.setattr(reel_studio, "probe", fail_metadata)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    window.load_video(str(input_file))
    assert window.input_path == str(input_file.resolve())
    assert "repair" in window.source_info.text().lower()
    report = tmp_path / "ReelForge_Error_Report.txt"
    report.write_text("dummy diagnostic")
    error = f"Failed to read metadata\n\nDiagnostic report saved:\n{report}"
    window.failed(error)
    assert window.open_button.isEnabled()
    assert window.copy_error_button.isEnabled()
    window.copy_error()
    assert QApplication.clipboard().text() == error
    window.close()
