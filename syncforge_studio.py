"""SyncForge Studio v3 — animated PySide6 UI for the existing sync engine.

All waveform detection, FFmpeg exports, CSV and Premiere XML creation are
handled by the unchanged syncforge_core module.
"""
from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path
import re
import sys

from PySide6.QtCore import (
    Property, QEasingCurve, QObject, QPointF, QPropertyAnimation, QRectF, Qt,
    QThread, QTimer, QUrl, Signal, Slot,
)
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QIcon, QLinearGradient, QPainter,
    QPen, QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget, QFileDialog,
)

from syncforge_core import (
    Match, analyze, export_video, find_binary, premiere_xml, probe, safe_name,
    write_report,
)

APP_VERSION = "3.0"
VIDEO_EXT = {".mp4", ".mov", ".mxf", ".mkv", ".avi", ".mts", ".m2ts", ".webm"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".aif", ".aiff",
             ".ogg", ".wma", ".mp4", ".mov", ".mxf"}

STYLESHEET = """
QWidget { color: #e9efff; background: transparent; font-family: "Segoe UI"; }
QFrame#fileCard, QFrame#settingsCard, QFrame#activityCard {
    background: #141e36; border: 1px solid #2a3755; border-radius: 19px;
}
QFrame#fileCard[dropActive="true"] { border: 2px solid #61ecd4; }
QLabel#eyebrow { color: #86a5c8; font-size: 10px; font-weight: 700; letter-spacing: 2px; }
QLabel#hero { color: #ffffff; font-size: 33px; font-weight: 800; }
QLabel#subtitle { color: #9caecc; font-size: 12px; }
QLabel#cardTitle { color: #edf4ff; font-size: 15px; font-weight: 700; }
QLabel#muted { color: #94a5c4; font-size: 11px; }
QLabel#count { background: #223653; color: #69e6d9;
               padding: 5px 10px; border-radius: 10px; font-weight: 700; }
QLabel#pill { background: #1e2945; color: #b4c6e2; border-radius: 10px;
              padding: 6px 11px; font-size: 11px; font-weight: 600; }
QLabel#statusTitle { color: #ecf6ff; font-size: 12px; font-weight: 700; }
QLabel#statusHint { color: #8ea1bc; font-size: 11px; }
QListWidget#files {
    border: 1px dashed #394c70; border-radius: 12px;
    background: #0e172b; padding: 8px; outline: none; color: #dbe7fb;
    font-size: 12px; alternate-background-color: #172441;
}
QListWidget#files::item { padding: 9px 8px; border-radius: 7px; }
QListWidget#files::item:selected { background: #21476a; color: #f5fffe; }
QListWidget#files::item:hover { background: #1c3250; }
QPushButton#secondary, QToolButton#advancedButton {
    background: #253452; color: #d3e5fd; border: 1px solid #3b5277;
    padding: 10px 13px; border-radius: 10px; font-size: 11px; font-weight: 700;
}
QPushButton#secondary:hover, QToolButton#advancedButton:hover {
    background: #34476a; border-color: #6082ad;
}
QPushButton#ghost { color: #98b1d1; background: transparent;
                     border: none; padding: 10px; font-size: 11px; }
QPushButton#ghost:hover { color: white; background: #243450; border-radius: 8px; }
QPushButton:disabled { color: #637492; }
QLineEdit, QComboBox {
    background: #0d182c; border: 1px solid #344968; border-radius: 10px;
    color: #f1f6ff; padding: 10px 12px; min-height: 20px;
    selection-background-color: #267b8e;
}
QLineEdit:focus, QComboBox:focus { border: 1px solid #55d6c6; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView {
    background: #172741; color: #f1f6ff; border: 1px solid #415778;
    selection-background-color: #2b6071; padding: 6px;
}
QPlainTextEdit#log {
    background: #0d172a; color: #b9d2ed; border: 1px solid #293c58;
    border-radius: 12px; padding: 12px; font-family: Consolas; font-size: 11px;
    selection-background-color: #2f6175;
}
QProgressBar {
    background: #0c192b; border: none; border-radius: 6px;
    height: 12px; text-align: center; color: transparent;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                                stop:0 #6175fa, stop:0.5 #40c5dc, stop:1 #63efd1);
    border-radius: 6px;
}
QScrollBar:vertical { background: transparent; width: 9px; margin: 0; }
QScrollBar::handle:vertical { background: #3a4c67; border-radius: 4px; min-height: 26px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


def natural_sort(path: str):
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", Path(path).name)]


def shadow(widget: QWidget, blur=30, y=9, alpha=56):
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)


class AmbientBackground(QWidget):
    """Subtle radial color blooms behind an otherwise plain native Qt interface."""
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QLinearGradient(0, 0, 0, self.height())
        bg.setColorAt(0, QColor("#091225"))
        bg.setColorAt(1, QColor("#0b1020"))
        p.fillRect(self.rect(), bg)
        p.setPen(Qt.PenStyle.NoPen)
        for cx, cy, radius, color in (
            (self.width() * 0.78, 130, 390, (67, 65, 170)),
            (self.width() * 0.20, 350, 340, (17, 121, 132)),
        ):
            radial = QRadialGradient(QPointF(cx, cy), radius)
            radial.setColorAt(0, QColor(*color, 54))
            radial.setColorAt(1, QColor(*color, 0))
            p.setBrush(radial)
            p.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))
        p.end()


class AnimatedAccentButton(QPushButton):
    """Smooth paint-driven hover transitions without native button flicker."""
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self._hover = 0.0
        self._animation = QPropertyAnimation(self, b"hoverAmount", self)
        self._animation.setDuration(210)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.setAccessibleName(text)

    def _get_hover(self):
        return self._hover

    def _set_hover(self, value):
        self._hover = float(value)
        self.update()

    hoverAmount = Property(float, _get_hover, _set_hover)

    def _animate(self, target):
        self._animation.stop()
        self._animation.setStartValue(self._hover)
        self._animation.setEndValue(target)
        self._animation.start()

    def enterEvent(self, event):
        if self.isEnabled():
            self._animate(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate(0.0)
        super().leaveEvent(event)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.2, 1.2, -1.2, -1.2)
        enabled = self.isEnabled()
        mix = self._hover if enabled else 0.0
        left = QColor("#57e4cd" if enabled else "#344c62")
        right = QColor("#5b92ff" if enabled else "#2f425d")
        if enabled:
            left = QColor.fromRgb(
                min(255, int(left.red() + 14 * mix)),
                min(255, int(left.green() + 12 * mix)),
                min(255, int(left.blue() + 12 * mix)))
            right = QColor.fromRgb(
                min(255, int(right.red() + 19 * mix)),
                min(255, int(right.green() + 20 * mix)),
                min(255, int(right.blue() + 9 * mix)))
        grad = QLinearGradient(rect.topLeft(), rect.topRight())
        grad.setColorAt(0, left)
        grad.setColorAt(1, right)
        p.setPen(QPen(QColor(146, 245, 232, int(80 + 100 * mix)) if enabled
                      else QColor("#435572"), 1))
        p.setBrush(grad)
        p.drawRoundedRect(rect, 15, 15)
        p.setFont(self.font())
        p.setPen(QColor("#06182b") if enabled else QColor("#8799ad"))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())
        if self.hasFocus():
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 12, 12)
        p.end()


class StatusDot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self._pulse = 0.0
        self._busy = False
        self._animation = QPropertyAnimation(self, b"pulse", self)
        self._animation.setDuration(1250)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._animation.setLoopCount(-1)

    def _get_pulse(self):
        return self._pulse

    def _set_pulse(self, value):
        self._pulse = float(value)
        self.update()

    pulse = Property(float, _get_pulse, _set_pulse)

    def set_busy(self, busy: bool):
        self._busy = busy
        if busy:
            self._animation.start()
        else:
            self._animation.stop()
            self._pulse = 0.0
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._busy:
            radius = 6 + self._pulse * 3
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(92, 239, 208, int(85 * (1 - self._pulse))))
            p.drawEllipse(QPointF(9, 9), radius, radius)
        p.setBrush(QColor("#65eacb" if self._busy else "#88a6c1"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(9, 9), 4, 4)
        p.end()


class DropList(QListWidget):
    incoming = Signal(list)

    def __init__(self, allowed: set[str], parent=None):
        super().__init__(parent)
        self.allowed = allowed
        self.setObjectName("files")
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setToolTip("Drop files here, or click Add files / Add folder")

    def _valid_urls(self, event):
        return [url.toLocalFile() for url in event.mimeData().urls()
                if url.isLocalFile() and
                Path(url.toLocalFile()).suffix.lower() in self.allowed]

    def dragEnterEvent(self, event):
        if self._valid_urls(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._valid_urls(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = self._valid_urls(event)
        if paths:
            self.incoming.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class MediaPanel(QFrame):
    def __init__(self, kind: str, title: str, description: str, allowed: set[str], parent=None):
        super().__init__(parent)
        self.setObjectName("fileCard")
        self.setProperty("dropActive", False)
        self.setAcceptDrops(True)
        self.kind = kind
        self.allowed = allowed
        self.paths: list[str] = []
        self.setMinimumHeight(280)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(19, 17, 19, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        left.addWidget(title_label)
        subtitle_label = QLabel(description)
        subtitle_label.setObjectName("muted")
        subtitle_label.setWordWrap(True)
        left.addWidget(subtitle_label)
        header.addLayout(left)
        header.addStretch()
        self.counter = QLabel("0 FILES")
        self.counter.setObjectName("count")
        header.addWidget(self.counter, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self.list = DropList(allowed)
        self.list.incoming.connect(self.add_paths)
        self.list.setMinimumHeight(125)
        self.list.setMaximumHeight(185)
        layout.addWidget(self.list, stretch=1)

        self.empty_hint = QLabel("DROP FILES ABOVE  ·  OR BROWSE BELOW")
        self.empty_hint.setObjectName("eyebrow")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_hint)

        actions = QHBoxLayout()
        actions.setSpacing(7)
        self.add_button = QPushButton("+ Add files")
        self.add_button.setObjectName("secondary")
        self.add_button.clicked.connect(self.browse_files)
        self.folder_button = QPushButton("Folder")
        self.folder_button.setObjectName("secondary")
        self.folder_button.clicked.connect(self.browse_folder)
        self.remove_button = QPushButton("Remove")
        self.remove_button.setObjectName("ghost")
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setObjectName("ghost")
        self.clear_button.clicked.connect(self.clear)
        for btn in (self.add_button, self.folder_button, self.remove_button, self.clear_button):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            actions.addWidget(btn)
        layout.addLayout(actions)
        self.list.itemSelectionChanged.connect(self._refresh_buttons)
        self._refresh_buttons()
        shadow(self, alpha=36)

    def _refresh_buttons(self):
        self.remove_button.setEnabled(bool(self.list.selectedItems()))
        self.clear_button.setEnabled(bool(self.paths))

    def add_paths(self, paths):
        new = 0
        for item in paths:
            path = Path(item)
            if not path.is_file() or path.suffix.lower() not in self.allowed:
                continue
            absolute = str(path.resolve())
            if absolute not in self.paths:
                self.paths.append(absolute)
                new += 1
        if new:
            self.paths.sort(key=natural_sort)
            self.refresh()

    def refresh(self):
        self.list.clear()
        self.list.addItems([Path(path).name for path in self.paths])
        self.counter.setText(f"{len(self.paths)} FILE{'S' if len(self.paths) != 1 else ''}")
        self.empty_hint.setVisible(not bool(self.paths))
        self._refresh_buttons()

    def browse_files(self):
        file_filter = (
            "Video files (*.mp4 *.mov *.mxf *.mkv *.avi *.mts *.m2ts *.webm)"
            if self.kind == "video"
            else "Audio files (*.wav *.mp3 *.m4a *.aac *.flac *.aif *.aiff *.ogg *.wma *.mp4 *.mov *.mxf)")
        paths, _ = QFileDialog.getOpenFileNames(
            self, f"Add {self.kind} files", "", file_filter + ";;All files (*)")
        self.add_paths(paths)

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, f"Select {self.kind} folder")
        if folder:
            self.add_paths([str(p) for p in Path(folder).iterdir() if p.is_file()])

    def remove_selected(self):
        indices = {self.list.row(item) for item in self.list.selectedItems()}
        self.paths = [p for index, p in enumerate(self.paths) if index not in indices]
        self.refresh()

    def clear(self):
        self.paths.clear()
        self.refresh()

    def dragEnterEvent(self, event):
        valid = any(url.isLocalFile() and
                    Path(url.toLocalFile()).suffix.lower() in self.allowed
                    for url in event.mimeData().urls())
        if valid:
            self.setProperty("dropActive", True)
            self.style().unpolish(self)
            self.style().polish(self)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setProperty("dropActive", False)
        self.style().unpolish(self)
        self.style().polish(self)
        event.accept()

    def dropEvent(self, event):
        self.add_paths([url.toLocalFile() for url in event.mimeData().urls()
                        if url.isLocalFile()])
        self.setProperty("dropActive", False)
        self.style().unpolish(self)
        self.style().polish(self)
        event.acceptProposedAction()


class SyncJob(QObject):
    """FFmpeg work runs in a dedicated Qt thread, never on the UI event loop."""
    log = Signal(str)
    status = Signal(str)
    progress = Signal(float)
    result_folder = Signal(str)
    done = Signal(str)
    error = Signal(str)
    finished = Signal()

    def __init__(self, videos, audios, output, container, manual):
        super().__init__()
        self.videos = list(videos)
        self.audios = list(audios)
        self.output = output
        self.container = container
        self.manual = manual

    @Slot()
    def run(self):
        try:
            base = Path(self.output).expanduser()
            base.mkdir(parents=True, exist_ok=True)
            prefix = "SyncForge_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            folder = base / prefix
            counter = 2
            while folder.exists():
                folder = base / f"{prefix}_{counter}"
                counter += 1
            folder.mkdir()
            self.log.emit(f"Project folder: {folder}")
            self.status.emit("Analyzing waveform patterns across all media ...")
            if self.manual is None:
                matches, problems = analyze(
                    self.videos, self.audios, log=self.log.emit)
            else:
                matches, problems = [], []
                for video, audio in zip(self.videos, self.audios):
                    vi, ai = probe(video), probe(audio)
                    if vi.video and ai.audio:
                        matches.append(Match(video, audio, self.manual, 1.0))
                        self.log.emit(
                            f"MANUAL {Path(video).name} + {Path(audio).name} "
                            f"at {self.manual:+.3f}s")
                    else:
                        problems.append((video, "Invalid video/audio pair."))
            self.progress.emit(15.0)
            exported = []
            accepted = []
            for index, match in enumerate(matches, 1):
                self.status.emit(
                    f"Exporting {index}/{len(matches)} · {Path(match.video).name}")
                filename = (
                    f"{index:03d}_{safe_name(Path(match.video).stem)}"
                    f"_synced.{self.container}")
                try:
                    path = export_video(match, folder / filename, log=self.log.emit)
                    exported.append(path)
                    accepted.append(match)
                    self.log.emit(f"EXPORTED {path.name}")
                except Exception as exc:
                    problems.append((match.video, f"Export failed: {exc}"))
                    self.log.emit(f"NEEDS REVIEW {Path(match.video).name}: {exc}")
                self.progress.emit(15 + 75 * index / max(1, len(matches)))
            if exported:
                premiere_xml(exported, folder / "SyncForge_Premiere_Timeline.xml")
                self.log.emit("Premiere Pro XML created.")
            write_report(accepted, problems, folder / "SyncForge_Match_Report.csv")
            self.log.emit("Pairing report saved.")
            self.progress.emit(100.0)
            self.result_folder.emit(str(folder))
            self.done.emit(f"{len(exported)} videos exported · {len(problems)} need review")
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished.emit()


class SyncForgeWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SyncForge Studio · Automatic Audio Sync")
        self.resize(1160, 815)
        self.setMinimumSize(780, 690)
        self.setWindowIcon(make_icon())
        self._job_thread = None
        self._job_worker = None
        self._result_folder = None
        self._animations = []

        background = AmbientBackground(self)
        self.setCentralWidget(background)
        outer = QVBoxLayout(background)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        body = QWidget()
        body.setObjectName("body")
        scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(33, 25, 33, 25)
        root.setSpacing(18)

        top = QHBoxLayout()
        top.setSpacing(12)
        logo = QLabel("S")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(42, 42)
        logo.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            "stop:0 #49efd0,stop:1 #6989fc); color: #091427;"
            "border-radius: 12px; font-size: 24px; font-weight: 900;")
        top.addWidget(logo)
        brand = QVBoxLayout()
        brand.setSpacing(0)
        name = QLabel("SYNCFORGE")
        name.setStyleSheet("font-size: 16px; font-weight: 800; letter-spacing: 1px;")
        brand.addWidget(name)
        version = QLabel("STUDIO EDITION  /  V3.0")
        version.setObjectName("eyebrow")
        brand.addWidget(version)
        top.addLayout(brand)
        top.addStretch()
        self.top_dot = StatusDot()
        top.addWidget(self.top_dot)
        self.top_state = QLabel("READY TO SYNC")
        self.top_state.setObjectName("eyebrow")
        top.addWidget(self.top_state)
        root.addLayout(top)

        hero = QVBoxLayout()
        hero.setSpacing(8)
        eyebrow = QLabel("A BETTER WORKFLOW STARTS HERE")
        eyebrow.setObjectName("eyebrow")
        hero.addWidget(eyebrow)
        headline = QLabel("Perfect sync. Zero busywork.")
        headline.setObjectName("hero")
        headline.setWordWrap(True)
        hero.addWidget(headline)
        description = QLabel(
            "Drop your camera footage and external audio. We find the match, "
            "align the sound, and prepare your Premiere Pro timeline.")
        description.setObjectName("subtitle")
        description.setWordWrap(True)
        hero.addWidget(description)
        root.addLayout(hero)

        pills = QHBoxLayout()
        pills.setSpacing(8)
        for text in ("SMART AUDIO MATCHING", "PREMIERE PRO XML", "BATCH EXPORT"):
            pill = QLabel(text)
            pill.setObjectName("pill")
            pill.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            pills.addWidget(pill)
        pills.addStretch()
        root.addLayout(pills)

        media = QHBoxLayout()
        media.setSpacing(15)
        self.video_panel = MediaPanel(
            "video", "01  Camera footage", "Video clips with reference / scratch audio", VIDEO_EXT)
        self.audio_panel = MediaPanel(
            "audio", "02  External recordings", "Audio files from your field recorder or microphone", AUDIO_EXT)
        media.addWidget(self.video_panel, 1)
        media.addWidget(self.audio_panel, 1)
        root.addLayout(media)

        settings = QFrame()
        settings.setObjectName("settingsCard")
        config = QVBoxLayout(settings)
        config.setContentsMargins(19, 16, 19, 16)
        config.setSpacing(10)
        config_header = QHBoxLayout()
        config_title = QLabel("03  Export preferences")
        config_title.setObjectName("cardTitle")
        config_header.addWidget(config_title)
        config_header.addStretch()
        self.advanced_button = QToolButton()
        self.advanced_button.setObjectName("advancedButton")
        self.advanced_button.setText("Advanced options  +")
        self.advanced_button.setCheckable(True)
        self.advanced_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.advanced_button.toggled.connect(self.toggle_advanced)
        config_header.addWidget(self.advanced_button)
        config.addLayout(config_header)

        destination = QHBoxLayout()
        destination.setSpacing(10)
        self.output = QLineEdit(str(Path.home() / "Videos" / "SyncForge"))
        self.output.setPlaceholderText("Choose export folder")
        self.output.setAccessibleName("Output directory")
        browse = QPushButton("Browse")
        browse.setObjectName("secondary")
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        browse.clicked.connect(self.choose_output)
        self.container = QComboBox()
        self.container.addItems(["mp4", "mov"])
        self.container.setAccessibleName("Export video format")
        self.container.setFixedWidth(112)
        destination.addWidget(self.output, 1)
        destination.addWidget(browse)
        destination.addWidget(self.container)
        config.addLayout(destination)

        self.advanced = QFrame()
        adv_row = QHBoxLayout(self.advanced)
        adv_row.setContentsMargins(0, 3, 0, 3)
        adv_row.setSpacing(12)
        adv_label = QLabel("Manual offset (seconds)")
        adv_label.setObjectName("muted")
        self.manual = QLineEdit()
        self.manual.setPlaceholderText("Auto")
        self.manual.setFixedWidth(120)
        self.manual.setAccessibleName("Manual synchronization offset")
        adv_help = QLabel("Blank = automatic. Manual pairs clips by filename order.")
        adv_help.setObjectName("muted")
        adv_help.setWordWrap(True)
        adv_row.addWidget(adv_label)
        adv_row.addWidget(self.manual)
        adv_row.addWidget(adv_help, 1)
        config.addWidget(self.advanced)
        self.advanced.setMaximumHeight(0)
        self.advanced_anim = QPropertyAnimation(self.advanced, b"maximumHeight", self)
        self.advanced_anim.setDuration(240)
        self.advanced_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        root.addWidget(settings)
        shadow(settings, alpha=33)

        self.start_button = AnimatedAccentButton("AUTO-SYNC & EXPORT   →")
        self.start_button.clicked.connect(self.start_job)
        root.addWidget(self.start_button)

        activity = QFrame()
        activity.setObjectName("activityCard")
        content = QVBoxLayout(activity)
        content.setContentsMargins(19, 14, 19, 17)
        content.setSpacing(10)
        status_row = QHBoxLayout()
        title = QLabel("Processing activity")
        title.setObjectName("cardTitle")
        status_row.addWidget(title)
        status_row.addStretch()
        self.open_button = QPushButton("Open results folder")
        self.open_button.setObjectName("secondary")
        self.open_button.setEnabled(False)
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_button.clicked.connect(self.open_results)
        status_row.addWidget(self.open_button)
        content.addLayout(status_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        content.addWidget(self.progress)
        self.progress_animation = QPropertyAnimation(self.progress, b"value", self)
        self.progress_animation.setDuration(400)
        self.progress_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.status_title = QLabel("Everything is ready.")
        self.status_title.setObjectName("statusTitle")
        content.addWidget(self.status_title)
        self.log_box = QPlainTextEdit()
        self.log_box.setObjectName("log")
        self.log_box.setReadOnly(True)
        self.log_box.setFixedHeight(101)
        self.log_box.setPlaceholderText("Sync results and details will appear here.")
        content.addWidget(self.log_box)
        root.addWidget(activity)
        shadow(activity, alpha=32)

        footer = QLabel(
            "LOCAL & PRIVATE  ·  AUDIO AND VIDEO STAY ON YOUR COMPUTER  ·  "
            "KEEP ORIGINAL FOOTAGE UNTIL SYNC IS VERIFIED")
        footer.setObjectName("eyebrow")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setWordWrap(True)
        root.addWidget(footer)
        self._scroll = scroll

    def toggle_advanced(self, checked: bool):
        self.advanced_button.setText(
            "Advanced options  −" if checked else "Advanced options  +")
        self.advanced_anim.stop()
        self.advanced_anim.setStartValue(self.advanced.maximumHeight())
        self.advanced_anim.setEndValue(58 if checked else 0)
        self.advanced_anim.start()

    def choose_output(self):
        selected = QFileDialog.getExistingDirectory(self, "Choose export folder", self.output.text())
        if selected:
            self.output.setText(selected)

    def _notify(self, title, detail):
        QMessageBox.warning(self, title, detail)

    def start_job(self):
        videos, audios = self.video_panel.paths, self.audio_panel.paths
        if not videos or not audios:
            self._notify("Missing media", "Add at least one camera video and one external audio recording.")
            return
        try:
            find_binary("ffmpeg")
            find_binary("ffprobe")
            if not self.output.text().strip():
                raise ValueError("Choose an output folder.")
            manual = (float(self.manual.text().strip())
                      if self.manual.text().strip() else None)
            if manual is not None and not math.isfinite(manual):
                raise ValueError("Manual offset must be a finite number.")
            if manual is not None and len(videos) != len(audios):
                raise ValueError(
                    "Manual mode needs the same number of videos and audio files.")
        except (ValueError, RuntimeError) as exc:
            self._notify("Check settings", str(exc))
            return

        self._result_folder = None
        self.open_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.start_button.setText("WORKING ON YOUR SYNC  ···")
        self.top_state.setText("PROCESSING")
        self.top_dot.set_busy(True)
        self._set_progress(0, animate=False)
        self.log_box.clear()
        self._append_log("SyncForge Studio v3 · Starting synchronization")

        self._job_thread = QThread(self)
        self._job_worker = SyncJob(
            list(videos), list(audios), self.output.text().strip(),
            self.container.currentText(), manual)
        self._job_worker.moveToThread(self._job_thread)
        self._job_thread.started.connect(self._job_worker.run)
        self._job_worker.log.connect(self._append_log)
        self._job_worker.status.connect(self.status_title.setText)
        self._job_worker.progress.connect(self._set_progress)
        self._job_worker.result_folder.connect(self._set_result_folder)
        self._job_worker.done.connect(self._on_done)
        self._job_worker.error.connect(self._on_error)
        self._job_worker.finished.connect(self._job_thread.quit)
        self._job_worker.finished.connect(self._job_worker.deleteLater)
        self._job_thread.finished.connect(self._thread_finished)
        self._job_thread.start()

    def _append_log(self, line: str):
        self.log_box.appendPlainText(line)

    def _set_progress(self, progress: float, animate=True):
        target = max(0, min(100, round(progress)))
        self.progress_animation.stop()
        if not animate:
            self.progress.setValue(target)
            return
        self.progress_animation.setStartValue(self.progress.value())
        self.progress_animation.setEndValue(target)
        self.progress_animation.start()

    def _set_result_folder(self, folder: str):
        self._result_folder = folder
        self.open_button.setEnabled(True)

    def _finish_visuals(self):
        self.start_button.setEnabled(True)
        self.start_button.setText("AUTO-SYNC & EXPORT   →")
        self.top_dot.set_busy(False)

    def _on_done(self, summary: str):
        self._finish_visuals()
        self.top_state.setText("COMPLETED")
        self.status_title.setText(summary)
        self._append_log("FINISHED · " + summary)
        QMessageBox.information(
            self, "SyncForge completed",
            summary + "\n\nOpen the results folder to review synced files, "
            "the Premiere XML and the pairing report.")

    def _on_error(self, detail: str):
        self._finish_visuals()
        self.top_state.setText("NEEDS ATTENTION")
        self.status_title.setText("Could not complete the sync job.")
        self._append_log("ERROR · " + detail)
        self._notify("SyncForge error", detail)

    @Slot()
    def _thread_finished(self):
        thread = self._job_thread
        self._job_thread = None
        self._job_worker = None
        if thread is not None:
            thread.deleteLater()

    def open_results(self):
        if self._result_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._result_folder))

    def closeEvent(self, event):
        if self._job_thread is not None and self._job_thread.isRunning():
            self._notify("Sync in progress", "Finish the current sync job before closing SyncForge.")
            event.ignore()
        else:
            event.accept()


def make_icon() -> QIcon:
    img = QPixmap(96, 96)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, 96, 96)
    gradient.setColorAt(0, QColor("#50efd1"))
    gradient.setColorAt(1, QColor("#628bfa"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(gradient)
    p.drawRoundedRect(QRectF(5, 5, 86, 86), 24, 24)
    p.setPen(QColor("#0d1d31"))
    p.setFont(QFont("Segoe UI", 50, QFont.Weight.Black))
    p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, "S")
    p.end()
    return QIcon(img)


def main():
    if "--self-test" in sys.argv and "--self-test-ui" not in sys.argv:
        find_binary("ffmpeg")
        find_binary("ffprobe")
        return 0
    application = QApplication(sys.argv)
    application.setApplicationName("SyncForge Studio")
    application.setApplicationVersion(APP_VERSION)
    application.setStyle("Fusion")
    application.setFont(QFont("Segoe UI", 10))
    application.setStyleSheet(STYLESHEET)
    window = SyncForgeWindow()
    window.show()
    if "--self-test-ui" in sys.argv:
        if window.video_panel.kind != "video" or window.audio_panel.kind != "audio":
            return 1
        QTimer.singleShot(350, application.quit)
    return application.exec()


if __name__ == "__main__":
    sys.exit(main())
