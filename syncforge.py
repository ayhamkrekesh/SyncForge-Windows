"""SyncForge: one-click Windows audio/video sync and Premiere Pro XML export."""
from __future__ import annotations

from datetime import datetime
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from syncforge_core import (Match, analyze, export_video, find_binary,
                            premiere_xml, probe, safe_name, write_report)


VIDEO_EXT = {".mp4", ".mov", ".mxf", ".mkv", ".avi", ".mts", ".m2ts", ".webm"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".aif", ".aiff",
             ".ogg", ".wma", ".mp4", ".mov", ".mxf"}


def natural_key(filename):
    import re
    return [int(p) if p.isdigit() else p.lower()
            for p in re.split(r"(\d+)", str(filename))]


class SyncForgeApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("SyncForge | Automatic Audio & Video Synchronization")
        self.root.geometry("1050x740")
        self.root.minsize(800, 620)
        self.videos: list[str] = []
        self.audios: list[str] = []
        self.queue = queue.Queue()
        self.output = tk.StringVar(value=str(Path.home() / "Videos" / "SyncForge"))
        self.container = tk.StringVar(value="mp4")
        self.manual = tk.StringVar(value="")
        self.status = tk.StringVar(value="Ready. Choose your camera videos and separate recorder audio.")
        self.progress = tk.DoubleVar(value=0)
        self.last_output = None
        self._build()
        root.after(100, self._poll)

    def _build(self):
        parent = ttk.Frame(self.root, padding=18)
        parent.pack(fill="both", expand=True)

        ttk.Label(parent, text="SyncForge", font=("Segoe UI", 25, "bold")).pack(anchor="w")
        ttk.Label(parent,
                  text="Auto-match real audio waveforms  •  Export synced video  •  Import a timeline into Premiere Pro",
                  font=("Segoe UI", 10)).pack(anchor="w", pady=(1, 14))

        panels = ttk.Frame(parent)
        panels.pack(fill="both", expand=True)
        panels.columnconfigure(0, weight=1)
        panels.columnconfigure(1, weight=1)
        panels.rowconfigure(0, weight=1)

        for col, kind, title in [(0, "video", "1   Camera videos"),
                                 (1, "audio", "2   Separate recorder audio")]:
            panel = ttk.LabelFrame(panels, text=title, padding=10)
            panel.grid(row=0, column=col, sticky="nsew",
                       padx=(0, 8) if col == 0 else (8, 0))
            listbox = tk.Listbox(panel, selectmode="extended", height=10,
                                 font=("Segoe UI", 10), activestyle="dotbox")
            listbox.pack(fill="both", expand=True)
            setattr(self, kind + "_list", listbox)
            buttons = ttk.Frame(panel)
            buttons.pack(fill="x", pady=(10, 0))
            ttk.Button(buttons, text="Add files", command=lambda k=kind: self.add_files(k)).pack(side="left")
            ttk.Button(buttons, text="Remove", command=lambda k=kind: self.remove_files(k)).pack(side="left", padx=7)
            ttk.Button(buttons, text="Clear", command=lambda k=kind: self.clear_files(k)).pack(side="left")
            ttk.Button(buttons, text="Add folder", command=lambda k=kind: self.add_folder(k)).pack(side="right")

        opts = ttk.LabelFrame(parent, text="3   Output", padding=10)
        opts.pack(fill="x", pady=(12, 7))
        ttk.Label(opts, text="Save results in").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(opts, textvariable=self.output).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(opts, text="Browse", command=self.browse).grid(row=0, column=2)
        ttk.Label(opts, text="Video format").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Combobox(opts, textvariable=self.container, values=("mp4", "mov"),
                     state="readonly", width=9).grid(row=1, column=1, sticky="w", padx=8)
        opts.columnconfigure(1, weight=1)

        advanced = ttk.LabelFrame(parent, text="Advanced (optional)", padding=10)
        advanced.pack(fill="x", pady=(0, 8))
        ttk.Label(advanced, text="Manual offset (seconds)").pack(side="left")
        ttk.Entry(advanced, textvariable=self.manual, width=12).pack(side="left", padx=(12, 15))
        ttk.Label(advanced, text="Blank = automatic pairing. Manual = pair by file order.",
                  foreground="#555").pack(side="left")
        ttk.Label(parent, text="Your camera videos must have their own scratch audio to auto-sync. "
                  "One long recorder file can match several videos.",
                  foreground="#555").pack(anchor="w", pady=(2, 9))
        self.start_button = ttk.Button(
            parent, text="4   AUTO-SYNC + EXPORT EVERYTHING", command=self.start)
        self.start_button.pack(fill="x", ipady=8)
        ttk.Progressbar(parent, variable=self.progress, maximum=100).pack(fill="x", pady=(8, 3))
        ttk.Label(parent, textvariable=self.status).pack(anchor="w", pady=(0, 6))
        logframe = ttk.LabelFrame(parent, text="Progress and pairing decisions", padding=7)
        logframe.pack(fill="both", expand=True)
        self.logbox = tk.Text(logframe, height=7, state="disabled", wrap="word",
                              font=("Consolas", 9))
        scrollbar = ttk.Scrollbar(logframe, orient="vertical", command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=scrollbar.set)
        self.logbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        bottom = ttk.Frame(parent)
        bottom.pack(fill="x", pady=(8, 0))
        self.open_button = ttk.Button(bottom, text="Open results folder",
                                      command=self.open_results, state="disabled")
        self.open_button.pack(side="right")

    def _refresh(self, kind: str):
        target = self.videos if kind == "video" else self.audios
        box = getattr(self, kind + "_list")
        box.delete(0, "end")
        for p in target:
            box.insert("end", Path(p).name)

    def _add_paths(self, kind: str, paths):
        target = self.videos if kind == "video" else self.audios
        allowed = VIDEO_EXT if kind == "video" else AUDIO_EXT
        for p in paths:
            p = str(Path(p).resolve())
            if Path(p).suffix.lower() in allowed and p not in target:
                target.append(p)
        target.sort(key=lambda p: natural_key(Path(p).name))
        self._refresh(kind)

    def add_files(self, kind: str):
        filters = ([("Video files", "*.mp4 *.mov *.mxf *.mkv *.avi *.mts *.m2ts *.webm")]
                   if kind == "video" else
                   [("Audio files", "*.wav *.mp3 *.m4a *.aac *.flac *.aif *.aiff *.ogg *.wma *.mp4 *.mov *.mxf")])
        files = filedialog.askopenfilenames(title="Choose " + kind + " recordings",
                                            filetypes=filters + [("All files", "*.*")])
        self._add_paths(kind, files)

    def add_folder(self, kind: str):
        selected = filedialog.askdirectory(title="Select folder containing " + kind + " files")
        if selected:
            folder = Path(selected)
            self._add_paths(kind, (p for p in folder.iterdir() if p.is_file()))

    def remove_files(self, kind: str):
        target = self.videos if kind == "video" else self.audios
        box = getattr(self, kind + "_list")
        for index in reversed(box.curselection()):
            target.pop(index)
        self._refresh(kind)

    def clear_files(self, kind: str):
        (self.videos if kind == "video" else self.audios).clear()
        self._refresh(kind)

    def browse(self):
        path = filedialog.askdirectory(title="Where should SyncForge save its results?")
        if path:
            self.output.set(path)

    def start(self):
        if not self.videos or not self.audios:
            messagebox.showerror("Missing files", "Please add at least one video and audio recording.")
            return
        try:
            find_binary("ffmpeg")
            find_binary("ffprobe")
            manual = float(self.manual.get().strip()) if self.manual.get().strip() else None
            if manual is not None and not math.isfinite(manual):
                raise ValueError("Manual offset must be a finite number.")
            if manual is not None and len(self.videos) != len(self.audios):
                raise ValueError("Manual mode needs the same number of videos and audio files.")
            if not str(self.output.get()).strip():
                raise ValueError("Choose an output folder.")
        except (RuntimeError, ValueError) as exc:
            messagebox.showerror("SyncForge setup", str(exc))
            return
        self.start_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self.progress.set(0)
        self._log("----- New synchronization job -----")
        args = (list(self.videos), list(self.audios),
                str(self.output.get()), self.container.get(), manual)
        threading.Thread(target=self.worker, args=args, daemon=True).start()

    def _emit(self, kind: str, value):
        self.queue.put((kind, value))

    def worker(self, videos, audios, output, container, manual):
        try:
            base = Path(output).expanduser()
            base.mkdir(parents=True, exist_ok=True)
            stem = "SyncForge_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            folder = base / stem
            i = 2
            while folder.exists():
                folder = base / f"{stem}_{i}"
                i += 1
            folder.mkdir()
            self._emit("log", f"Job folder: {folder}")
            self._emit("status", "Analyzing waveforms and identifying recorder matches...")
            if manual is None:
                matches, problems = analyze(videos, audios,
                                            log=lambda msg: self._emit("log", msg))
            else:
                matches, problems = [], []
                for v, a in zip(videos, audios):
                    vi, ai = probe(v), probe(a)
                    if not vi.video or not ai.audio:
                        problems.append((v, "Invalid video/audio pair."))
                    else:
                        matches.append(Match(v, a, manual, 1.0))
                        self._emit("log", f"MANUAL  {Path(v).name} ↔ {Path(a).name}: {manual:+.3f}s")
            self._emit("progress", 15)
            exports = []
            successes = []
            for number, match in enumerate(matches, 1):
                self._emit("status", f"Exporting {number} of {len(matches)}: {Path(match.video).name}")
                file_name = f"{number:03d}_{safe_name(Path(match.video).stem)}_synced.{container}"
                try:
                    out = export_video(match, folder / file_name,
                                       log=lambda msg: self._emit("log", msg))
                    exports.append(out)
                    successes.append(match)
                    self._emit("log", f"EXPORTED  {out.name}")
                except Exception as exc:
                    problems.append((match.video, f"Export failed: {exc}"))
                    self._emit("log", f"FAILED  {Path(match.video).name}: {exc}")
                self._emit("progress", 15 + 75 * number / max(1, len(matches)))
            if exports:
                xml = premiere_xml(exports, folder / "SyncForge_Premiere_Timeline.xml")
                self._emit("log", f"PREMIERE XML  {xml.name}")
            report = write_report(successes, problems, folder / "SyncForge_Match_Report.csv")
            self._emit("log", f"PAIRING REPORT  {report.name}")
            self._emit("progress", 100)
            self._emit("folder", str(folder))
            summary = (f"Finished: {len(exports)} synchronized video(s), "
                       f"{len(problems)} needing review.\n\nSaved in:\n{folder}")
            self._emit("done", summary)
        except Exception as exc:
            self._emit("error", str(exc))

    def _log(self, message):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", message + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                kind, value = self.queue.get_nowait()
                if kind == "log":
                    self._log(value)
                elif kind == "status":
                    self.status.set(value)
                elif kind == "progress":
                    self.progress.set(value)
                elif kind == "folder":
                    self.last_output = value
                    self.open_button.configure(state="normal")
                elif kind in ("done", "error"):
                    self.start_button.configure(state="normal")
                    self.status.set(value.split("\n")[0])
                    if kind == "done":
                        messagebox.showinfo("SyncForge finished", value)
                    else:
                        self._log("ERROR: " + value)
                        messagebox.showerror("SyncForge error", value)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def open_results(self):
        if not self.last_output:
            return
        if sys.platform == "win32":
            os.startfile(self.last_output)
        else:
            subprocess.Popen(["xdg-open", self.last_output])


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        find_binary("ffmpeg")
        find_binary("ffprobe")
        sys.exit(0)
    root = tk.Tk()
    ttk.Style().theme_use("clam")
    SyncForgeApp(root)
    root.mainloop()
