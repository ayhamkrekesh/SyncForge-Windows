"""ReelForge desktop — one-button local vertical Reel creation.

PySide6 frontend, analysis and rendering delegated to reel_engine.py in a
QThread. No cloud upload, login, command line, or editing knowledge required.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

from PySide6.QtCore import Qt, QObject, QThread, Signal, Slot, QTimer, Property, QEasingCurve, QPropertyAnimation
from PySide6.QtGui import QColor, QPainter, QLinearGradient, QPixmap, QPen, QFont, QDragEnterEvent, QDropEvent, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget, QPlainTextEdit,
)
from PySide6.QtCore import QUrl

from reel_engine import binary, bundled_model, make_reel, probe

APP_NAME = "ReelForge"
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".webm", ".mts", ".m2ts"}

STYLE = """
QWidget { background: transparent; color: #e7efff; font-family: "Segoe UI"; }
QFrame#panel {
    background: #111c30; border-radius: 21px; border: 1px solid #293b57;
}
QFrame#drop { background: #0b192d; border-radius: 16px;
              border: 2px dashed #405677; }
QFrame#drop[active="true"] { background: #112d3b; border: 2px solid #69e9cb; }
QLabel#logo { font-weight: 900; font-size: 20px; letter-spacing: 1px; color: #ebf8ff; }
QLabel#version { color: #68e5ca; font-size: 10px; font-weight: 800; letter-spacing: 2px; }
QLabel#hero { font-weight: 800; font-size: 32px; color: #ffffff; }
QLabel#description { color: #a0b3d0; font-size: 12px; }
QLabel#title { font-weight: 750; font-size: 15px; color: #edf5ff; }
QLabel#eyebrow { font-size: 10px; color: #7e9dbb; font-weight: 700; letter-spacing: 1px; }
QLabel#hint { font-size: 11px; color: #a2b5d2; }
QLabel#metric { font-size: 11px; color: #72e4d4; font-weight: 700; }
QLineEdit, QComboBox {
    background: #0a172a; border-radius: 10px; border: 1px solid #374d6b;
    padding: 10px 12px; color: white; min-height: 19px;
}
QLineEdit:focus, QComboBox:focus { border-color: #59dcc7; }
QComboBox::drop-down { border: none; width: 25px; }
QComboBox QAbstractItemView { background: #182944; selection-background-color: #2e647c; }
QPushButton#secondary {
    background: #263954; border: 1px solid #385676; border-radius: 10px;
    padding: 11px 14px; color: #d6eaff; font-size: 11px; font-weight: 700;
}
QPushButton#secondary:hover { background: #385473; }
QPushButton#secondary:disabled { background: #1b2738; color: #67788b; }
QPushButton#ghost { color: #91a5c4; padding: 9px; background: transparent; border: none; }
QPushButton#ghost:hover { color: #f6ffff; }
QProgressBar { background: #0a182a; border: none; border-radius: 6px; height: 12px; }
QProgressBar::chunk { border-radius: 6px; background:
    qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #6678ff,stop:1 #5ef0d4); }
QPlainTextEdit { background: #0b192b; border: 1px solid #2d4360; border-radius: 10px;
                padding: 9px; color: #a8c5e3; font-family: Consolas; font-size: 10px; }
QScrollBar:vertical { background: transparent; width: 9px; }
QScrollBar::handle:vertical { background: #425671; border-radius: 4px; }
"""


def glow(widget: QWidget, blur=38):
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setColor(QColor(0, 0, 0, 74))
    shadow.setOffset(0, 12)
    widget.setGraphicsEffect(shadow)


class Backdrop(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor("#091126"))
        gradient.setColorAt(0.55, QColor("#101a32"))
        gradient.setColorAt(1, QColor("#071728"))
        painter.fillRect(self.rect(), gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        for x, y, color, r in [
            (self.width() * 0.85, 100, QColor(92, 62, 223, 32), 260),
            (self.width() * 0.09, 450, QColor(40, 208, 194, 23), 350),
        ]:
            painter.setBrush(color)
            painter.drawEllipse(int(x-r), int(y-r), 2*r, 2*r)
        painter.end()


class AnimatedButton(QPushButton):
    def __init__(self, title):
        super().__init__(title)
        self._hover = 0.0
        self._anim = QPropertyAnimation(self, b"hoverAmount")
        self._anim.setDuration(190)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setMinimumHeight(59)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))

    def get_hover(self):
        return self._hover

    def set_hover(self, value):
        self._hover = float(value)
        self.update()

    hoverAmount = Property(float, get_hover, set_hover)

    def enterEvent(self, event):
        if self.isEnabled():
            self.animate(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.animate(0.0)
        super().leaveEvent(event)

    def animate(self, value):
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(value)
        self._anim.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        enabled = self.isEnabled()
        gradient = QLinearGradient(0, 0, rect.width(), 0)
        if enabled:
            h = self._hover
            gradient.setColorAt(0, QColor.fromRgb(87, min(255, int(224+18*h)), 199))
            gradient.setColorAt(1, QColor.fromRgb(101, min(255, int(145+26*h)), 255))
        else:
            gradient.setColorAt(0, QColor("#37536a"))
            gradient.setColorAt(1, QColor("#344568"))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor("#79dcd9" if enabled else "#435570"), 1))
        painter.drawRoundedRect(rect, 15, 15)
        painter.setPen(QColor("#0a182c") if enabled else QColor("#aab9cc"))
        painter.setFont(self.font())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())
        if self.hasFocus():
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(4, 4, -4, -4), 12, 12)
        painter.end()


class VideoDrop(QFrame):
    selected = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("drop")
        self.setProperty("active", False)
        self.setAcceptDrops(True)
        self.setMinimumHeight(225)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(6)
        self.preview = QLabel("▣")
        self.preview.setFont(QFont("Segoe UI", 38, QFont.Weight.Bold))
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet("color: #59e5d2;")
        self.preview.setMinimumHeight(75)
        layout.addWidget(self.preview, 1)
        self.label = QLabel("DRAG YOUR VIDEO HERE")
        self.label.setObjectName("title")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label)
        self.caption = QLabel("or choose a video file from your computer")
        self.caption.setObjectName("hint")
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.caption)
        browse = QPushButton("Choose video")
        browse.setObjectName("secondary")
        browse.clicked.connect(self.browse)
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(browse, alignment=Qt.AlignmentFlag.AlignCenter)
        self.pick_button = browse

    def _change_active(self, active):
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def _url(self, event):
        for uri in event.mimeData().urls():
            if uri.isLocalFile() and Path(uri.toLocalFile()).suffix.lower() in VIDEO_EXT:
                return uri.toLocalFile()
        return None

    def dragEnterEvent(self, event):
        if self._url(event):
            self._change_active(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._change_active(False)
        event.accept()

    def dropEvent(self, event):
        self._change_active(False)
        candidate = self._url(event)
        if candidate:
            self.selected.emit(candidate)
            event.acceptProposedAction()
        else:
            event.ignore()

    def browse(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Select the video to transform", "",
            "Video files (*.mp4 *.mov *.mkv *.avi *.mxf *.webm *.mts *.m2ts);;All files (*)")
        if filename:
            self.selected.emit(filename)

    def show_video(self, path: str, thumbnail: QPixmap | None = None):
        self.label.setText(Path(path).name)
        self.caption.setText("Video loaded — ready to transform")
        if thumbnail and not thumbnail.isNull():
            self.preview.setPixmap(thumbnail.scaled(
                310, 150, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        else:
            self.preview.setText("▶")


class Job(QObject):
    log = Signal(str)
    progress = Signal(float)
    done = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, input_file: str, output: str, crop: str, music: str, resolution: str):
        super().__init__()
        self.input_file = input_file
        self.output = output
        self.crop = crop
        self.music = music
        self.resolution = resolution

    @Slot()
    def run(self):
        try:
            result = make_reel(
                self.input_file, self.output, crop=self.crop, music=self.music,
                resolution=self.resolution, log=self.log.emit, progress=self.progress.emit)
            self.done.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class ReelWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ReelForge | Your Video → Your Reel")
        self.setMinimumSize(765, 700)
        self.resize(920, 815)
        self.input_path = None
        self.last_output = None
        self.last_error = ""
        self.thread = None
        self.worker = None
        self._thumb_directory = tempfile.TemporaryDirectory(prefix="reelforge_thumb_")

        bg = Backdrop()
        self.setCentralWidget(bg)
        outer = QVBoxLayout(bg)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        main = QVBoxLayout(body)
        main.setContentsMargins(32, 22, 32, 28)
        main.setSpacing(16)

        brand = QHBoxLayout()
        mark = QLabel("◉")
        mark.setFixedSize(44, 44)
        mark.setFont(QFont("Segoe UI", 29, QFont.Weight.Bold))
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setStyleSheet("color: #58ecd0; background: #233754; border-radius: 12px;")
        brand.addWidget(mark)
        label_box = QVBoxLayout()
        label_box.setSpacing(0)
        name = QLabel("REELFORGE")
        name.setObjectName("logo")
        label_box.addWidget(name)
        edition = QLabel("AUTOMATIC REEL STUDIO  /  V1.0")
        edition.setObjectName("version")
        label_box.addWidget(edition)
        brand.addLayout(label_box)
        brand.addStretch()
        private_label = QLabel("●  LOCAL PROCESSING")
        private_label.setObjectName("version")
        brand.addWidget(private_label)
        main.addLayout(brand)

        headline = QLabel("One video. One standout reel.")
        headline.setObjectName("hero")
        headline.setWordWrap(True)
        main.addWidget(headline)
        subhead = QLabel(
            "Automatic highlights, smart jump cuts, dynamic zooms, word-by-word "
            "captions and optional original background music. Every reel is under 60 seconds.")
        subhead.setObjectName("description")
        subhead.setWordWrap(True)
        main.addWidget(subhead)

        upper = QFrame()
        upper.setObjectName("panel")
        panel = QVBoxLayout(upper)
        panel.setContentsMargins(20, 16, 20, 17)
        panel.setSpacing(11)
        title = QLabel("01   YOUR SOURCE VIDEO")
        title.setObjectName("eyebrow")
        panel.addWidget(title)
        self.drop = VideoDrop()
        self.drop.selected.connect(self.load_video)
        panel.addWidget(self.drop)
        self.source_info = QLabel("Formats: MP4, MOV, MKV, AVI, MXF, WEBM, MTS")
        self.source_info.setObjectName("metric")
        panel.addWidget(self.source_info)
        main.addWidget(upper)
        glow(upper)

        settings = QFrame()
        settings.setObjectName("panel")
        settings_layout = QVBoxLayout(settings)
        settings_layout.setContentsMargins(20, 14, 20, 18)
        settings_layout.setSpacing(12)
        s_title = QLabel("02   STYLE YOUR REEL")
        s_title.setObjectName("eyebrow")
        settings_layout.addWidget(s_title)
        row = QHBoxLayout()
        row.setSpacing(12)

        def control(title_text: str, choices: list[str], tooltip: str) -> QComboBox:
            column = QVBoxLayout()
            label = QLabel(title_text)
            label.setObjectName("hint")
            column.addWidget(label)
            item = QComboBox()
            item.addItems(choices)
            item.setToolTip(tooltip)
            item.setAccessibleName(title_text)
            column.addWidget(item)
            row.addLayout(column, 1)
            return item

        self.reframe = control("Video framing", ["Dynamic crop", "Backdrop"],
                               "Dynamic crop fills 9:16; Backdrop keeps the whole frame on a blurred background.")
        self.music = control("Original background music", ["Auto", "On", "Off"],
                             "Auto enables synthesized music for non-talking montages; On forces it.")
        self.quality = control("Export resolution", ["1080p", "720p"],
                               "1080p renders at 1080 × 1920 vertical pixels.")
        settings_layout.addLayout(row)
        output_row = QHBoxLayout()
        self.output = QLineEdit(str(Path.home() / "Videos" / "ReelForge"))
        self.output.setAccessibleName("Output folder")
        choose = QPushButton("Browse folder")
        choose.setObjectName("secondary")
        choose.clicked.connect(self.select_folder)
        choose.setCursor(Qt.CursorShape.PointingHandCursor)
        output_row.addWidget(self.output, 1)
        output_row.addWidget(choose)
        settings_layout.addLayout(output_row)
        main.addWidget(settings)
        glow(settings)

        self.go = AnimatedButton("CREATE MY REEL   →")
        self.go.clicked.connect(self.start)
        main.addWidget(self.go)

        activity = QFrame()
        activity.setObjectName("panel")
        activity_layout = QVBoxLayout(activity)
        activity_layout.setContentsMargins(20, 15, 20, 17)
        activity_layout.setSpacing(9)
        progress_header = QHBoxLayout()
        self.status = QLabel("Ready to create.")
        self.status.setObjectName("title")
        progress_header.addWidget(self.status)
        progress_header.addStretch()
        self.percent = QLabel("0%")
        self.percent.setObjectName("metric")
        progress_header.addWidget(self.percent)
        activity_layout.addLayout(progress_header)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        activity_layout.addWidget(self.progress)
        self.progress_anim = QPropertyAnimation(self.progress, b"value", self)
        self.progress_anim.setDuration(300)
        self.progress_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setPlaceholderText("Your automatic editing decisions will appear here.")
        self.logs.setFixedHeight(85)
        activity_layout.addWidget(self.logs)
        self.open_button = QPushButton("OPEN FINISHED REEL")
        self.open_button.setObjectName("secondary")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_result)
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        activity_layout.addWidget(self.open_button)
        self.copy_error_button = QPushButton("COPY ERROR DETAILS")
        self.copy_error_button.setObjectName("secondary")
        self.copy_error_button.setEnabled(False)
        self.copy_error_button.clicked.connect(self.copy_error)
        activity_layout.addWidget(self.copy_error_button)
        main.addWidget(activity)
        disclaimer = QLabel(
            "No uploads. No subscriptions. Automatically edited footage still deserves a quick review "
            "before you publish. Virality cannot be guaranteed.")
        disclaimer.setObjectName("eyebrow")
        disclaimer.setWordWrap(True)
        disclaimer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main.addWidget(disclaimer)

    def load_video(self, path: str):
        # A video with broken duration metadata may still be playable.
        # Always allow it into the editing worker so remux recovery can run.
        item = Path(path)
        if not item.is_file():
            QMessageBox.warning(self, "Video missing", "The chosen file cannot be found.")
            return
        if item.suffix.lower() not in VIDEO_EXT:
            QMessageBox.warning(self, "Unsupported file", "Choose a supported video file.")
            return
        self.input_path = str(item.resolve())
        try:
            info = probe(path)
            label = f"{info.duration:.1f}s  •  {info.width}×{info.height}"
            label += "  •  Source audio detected" if info.has_audio else "  •  Silent footage"
            self.source_info.setText(label)
            preview = None
            try:
                shot = Path(self._thumb_directory.name) / "preview.png"
                subprocess.run([
                    binary("ffmpeg"), "-y", "-v", "error",
                    "-ss", f"{min(info.duration/3, 2.0):.2f}", "-i", info.path,
                    "-frames:v", "1", "-vf", "scale=460:-2", str(shot)],
                    check=True, capture_output=True, timeout=18)
                preview = QPixmap(str(shot))
            except (OSError, RuntimeError, subprocess.CalledProcessError,
                    subprocess.TimeoutExpired):
                pass
            self.drop.show_video(path, preview)
            self.status.setText("Video loaded. Ready to edit.")
        except Exception as exc:
            self.drop.show_video(path)
            self.source_info.setText("Unusual metadata detected — automatic repair available.")
            self.status.setText("Video selected. ReelForge will attempt metadata repair.")
            self.logs.appendPlainText(f"Source inspection warning: {exc}")

    def select_folder(self):
        result = QFileDialog.getExistingDirectory(self, "Choose output folder", self.output.text())
        if result:
            self.output.setText(result)

    def start(self):
        if self.thread is not None and self.thread.isRunning():
            return
        if not self.input_path:
            QMessageBox.warning(self, "Add a video", "Choose or drop in a video first.")
            return
        if not self.output.text().strip():
            QMessageBox.warning(self, "Choose an output folder", "Select where to save the edited reel.")
            return
        try:
            binary("ffmpeg")
            binary("ffprobe")
            bundled_model()
        except Exception as exc:
            QMessageBox.warning(self, "Missing application component", str(exc))
            return
        self.go.setEnabled(False)
        self.drop.pick_button.setEnabled(False)
        self.last_output = None
        self.last_error = ""
        self.copy_error_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.logs.clear()
        self.status.setText("Choosing the best moments...")
        self.set_progress(0)
        self.worker = Job(self.input_path, self.output.text().strip(),
                          self.reframe.currentText(), self.music.currentText(),
                          self.quality.currentText())
        self.thread = QThread(self)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.logs.appendPlainText)
        self.worker.progress.connect(self.set_progress)
        self.worker.done.connect(self.completed)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.cleanup_thread)
        self.thread.start()

    def set_progress(self, fraction: float):
        progress = max(0, min(100, round(fraction * 100)))
        self.percent.setText(f"{progress}%")
        self.progress_anim.stop()
        self.progress_anim.setStartValue(self.progress.value())
        self.progress_anim.setEndValue(progress)
        self.progress_anim.start()

    @Slot(object)
    def completed(self, report):
        self.last_output = str(Path(report["file"]).parent)
        self.status.setText(
            f"Reel ready! {report['duration']:.1f}s  •  {report['size'][0]} × {report['size'][1]}")
        self.go.setEnabled(True)
        self.drop.pick_button.setEnabled(True)
        self.open_button.setEnabled(True)
        QMessageBox.information(self, "ReelForge finished",
            "Your reel has been exported successfully.\n\n"
            f"Length: {report['duration']:.1f} seconds\n"
            f"Captions: {'Yes' if report['captions'] else 'No speech detected'}\n"
            f"Original beat: {'Yes' if report['original_music'] else 'No'}\n\n"
            "Open the finished reel folder to review the video.")

    @Slot(str)
    def failed(self, error: str):
        self.last_error = error
        self.copy_error_button.setEnabled(True)
        if "Diagnostic report saved:\n" in error:
            diagnostic_path = error.rsplit("Diagnostic report saved:\n", 1)[-1].strip()
            if Path(diagnostic_path).is_file():
                self.last_output = str(Path(diagnostic_path).parent)
                self.open_button.setEnabled(True)
        self.status.setText("Editing could not finish — copy error details for support.")
        self.logs.appendPlainText("ERROR: " + error)
        self.go.setEnabled(True)
        self.drop.pick_button.setEnabled(True)
        QMessageBox.warning(self, "ReelForge error", error)

    @Slot()
    def cleanup_thread(self):
        old = self.thread
        self.thread = None
        self.worker = None
        if old is not None:
            old.deleteLater()

    def copy_error(self):
        if self.last_error:
            QApplication.clipboard().setText(self.last_error)
            self.logs.appendPlainText("Error details copied to clipboard.")

    def open_result(self):
        if self.last_output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_output))

    def closeEvent(self, event):
        if self.thread is not None and self.thread.isRunning():
            QMessageBox.information(
                self, "ReelForge is editing",
                "Finish the current export before closing the program.")
            event.ignore()
            return
        self._thumb_directory.cleanup()
        event.accept()


def main():
    if "--self-test" in sys.argv:
        binary("ffmpeg")
        binary("ffprobe")
        bundled_model()
        return 0
    if "--verify-caption-model" in sys.argv:
        # Verify that bundled model and native CTranslate2 runtime actually load.
        # A full inference test runs separately during development, not at GUI startup.
        from faster_whisper import WhisperModel
        WhisperModel(bundled_model(), device="cpu", compute_type="int8", cpu_threads=2)
        return 0
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLE)
    app.setApplicationName(APP_NAME)
    window = ReelWindow()
    window.show()
    if "--smoke-ui" in sys.argv:
        QTimer.singleShot(400, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
