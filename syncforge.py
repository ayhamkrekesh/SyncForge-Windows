"""SyncForge — automatic dual-system sound synchronization for Windows."""
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import xml.etree.ElementTree as ET

import numpy as np

APP_NAME = 'SyncForge'
SAMPLE_RATE = 8000
HOP = 160  # 50 envelope samples per second


def binary(name):
    base = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    local = base / 'bin' / (name + '.exe')
    if local.is_file():
        return str(local)
    return shutil.which(name) or shutil.which(name + '.exe')


def run(command, **kwargs):
    return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, **kwargs)


def probe(path):
    data = json.loads(run([binary('ffprobe'), '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)]).stdout)
    video = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
    audio = next((s for s in data['streams'] if s['codec_type'] == 'audio'), None)
    duration = float(data.get('format', {}).get('duration') or (video or audio or {}).get('duration') or 0)
    return video, audio, duration


def envelope(path, seconds, start=0):
    cmd = [binary('ffmpeg'), '-nostdin', '-v', 'error', '-ss', str(start), '-i', str(path), '-t', str(seconds),
           '-vn', '-ac', '1', '-ar', str(SAMPLE_RATE), '-f', 'f32le', '-']
    raw = run(cmd).stdout
    samples = np.frombuffer(raw, dtype='<f4')
    samples = samples[:len(samples) // HOP * HOP]
    if len(samples) < SAMPLE_RATE * 5:
        raise ValueError('At least five seconds of usable reference audio are required.')
    chunks = samples.reshape(-1, HOP)
    rms = np.sqrt(np.mean(chunks * chunks, axis=1) + 1e-12)
    # Compress dynamic range to make recordings with different microphones comparable.
    return np.log1p(rms * 1000).astype(np.float64)


def fft_correlate(a, b):
    """Full cross-correlation of a with b: lag = index - (len(b)-1)."""
    size = len(a) + len(b) - 1
    n = 1 << (size - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(a, n) * np.fft.rfft(b[::-1], n), n)[:size]


def find_offset(video_path, audio_path, video_duration, audio_duration):
    """Return offset seconds: positive means external audio begins after video."""
    if video_duration < 5 or audio_duration < 5:
        raise ValueError('Both recordings must be at least five seconds long.')
    # Search up to three minutes from the beginning of the external recording.
    cam = envelope(video_path, min(75, video_duration))
    ext = envelope(audio_path, min(240, audio_duration))
    if np.std(cam) < 0.035 or np.std(ext) < 0.035:
        raise ValueError('Audio is too quiet or uniform for reliable waveform matching.')
    cam -= cam.mean()
    ext -= ext.mean()
    corr = fft_correlate(ext, cam)
    lags = np.arange(-len(cam) + 1, len(ext))
    # Correlation score is normalized by the energy in each overlapping region.
    ext_sq = np.r_[0., np.cumsum(ext ** 2)]
    cam_sq = np.r_[0., np.cumsum(cam ** 2)]
    ext_start = np.maximum(0, lags)
    cam_start = np.maximum(0, -lags)
    overlap = np.minimum(len(ext) - ext_start, len(cam) - cam_start)
    valid = overlap >= min(500, int(min(len(cam), len(ext)) * .65))
    ext_energy = ext_sq[ext_start + overlap] - ext_sq[ext_start]
    cam_energy = cam_sq[cam_start + overlap] - cam_sq[cam_start]
    score = corr / np.sqrt(np.maximum(ext_energy * cam_energy, 1e-15))
    score[~valid] = -np.inf
    best = int(np.argmax(score))
    confidence = float(score[best])
    if confidence < .30:
        raise ValueError(f'No reliable waveform match (similarity {confidence:.2f}). Try a manual offset.')
    # ext time = camera time + lag; thus ext begins at camera time = -lag.
    offset = -float(lags[best]) * HOP / SAMPLE_RATE
    return offset, confidence


def safe_stem(name):
    return re.sub(r'[^\w\-. ]+', '_', name).strip(' .') or 'clip'


def export_synced(video, audio, dest, offset, container, on_log):
    """Create synced media; positive offset delays external audio."""
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    vinfo, _, duration = probe(video)
    if not vinfo:
        raise ValueError('Selected video file has no video stream.')
    # adelay keeps output video beginning at 0; atrim removes external preroll.
    if offset >= 0:
        filter_str = f'[1:a:0]adelay={round(offset * 1000)}:all=1,apad,atrim=duration={duration:.6f},asetpts=N/SR/TB[aout]'
    else:
        filter_str = f'[1:a:0]atrim=start={-offset:.6f},asetpts=PTS-STARTPTS,apad,atrim=duration={duration:.6f}[aout]'
    cmd = [binary('ffmpeg'), '-y', '-nostdin', '-hide_banner', '-loglevel', 'error', '-i', str(video), '-i', str(audio),
           '-filter_complex', filter_str, '-map', '0:v:0', '-map', '[aout]', '-c:v', 'copy',
           '-c:a', 'aac', '-b:a', '320k', '-ar', '48000', '-movflags', '+faststart', '-t', f'{duration:.6f}', str(target)]
    try:
        run(cmd)
    except subprocess.CalledProcessError as exc:
        # Some input codecs cannot be copied into the selected container.
        on_log('Stream copy unavailable; retrying with H.264 video encoding.')
        cmd[cmd.index('copy')] = 'libx264'
        cmd[cmd.index('-c:v') + 2:cmd.index('-c:a')] = ['-preset', 'medium', '-crf', '18']
        try:
            run(cmd)
        except subprocess.CalledProcessError as exc2:
            target.unlink(missing_ok=True)
            raise RuntimeError(exc2.stderr.decode(errors='replace')[-1500:]) from exc2
    return target


def add(parent, tag, text=None, **attrs):
    el = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        el.text = str(text)
    return el


def make_premiere_xml(clips, destination):
    """FCP7 XML (xmeml), importable into Adobe Premiere Pro.

    Uses the already-synchronized exported files so timing and audio are identical
    in the rendered media and the Premiere sequence.
    """
    root = ET.Element('xmeml', version='4')
    sequence = add(root, 'sequence', id='sequence-1')
    add(sequence, 'name', 'SyncForge - Synced Timeline')
    rate = add(sequence, 'rate')
    add(rate, 'timebase', 30)
    add(rate, 'ntsc', 'FALSE')
    media = add(sequence, 'media')
    vtrack = add(add(media, 'video'), 'track')
    atrack = add(add(media, 'audio'), 'track')
    cursor = 0
    for index, item in enumerate(clips, 1):
        path = Path(item['path']).resolve()
        vi, _, duration = probe(path)
        fps_text = vi.get('avg_frame_rate') or vi.get('r_frame_rate') or '30/1'
        num, den = (fps_text.split('/') + ['1'])[:2]
        fps = float(num) / max(1, float(den))
        frames = max(1, round(duration * 30))
        for track, kind in [(vtrack, 'video'), (atrack, 'audio')]:
            ci = add(track, 'clipitem', id=f'{kind}-clip-{index}')
            add(ci, 'name', path.name)
            add(ci, 'start', cursor)
            add(ci, 'end', cursor + frames)
            add(ci, 'in', 0)
            add(ci, 'out', frames)
            file_el = add(ci, 'file', id=f'file-{index}')
            add(file_el, 'name', path.name)
            add(file_el, 'pathurl', path.as_uri())
            f_rate = add(file_el, 'rate')
            add(f_rate, 'timebase', round(fps))
            add(f_rate, 'ntsc', 'TRUE' if abs(fps - 29.97) < .05 or abs(fps - 23.976) < .05 else 'FALSE')
            add(file_el, 'duration', max(1, round(duration * fps)))
            file_media = add(file_el, 'media')
            add(add(file_media, 'video'), 'samplecharacteristics')
            add(file_media, 'audio')
            source = add(ci, 'sourcetrack')
            add(source, 'mediatype', kind)
            add(source, 'trackindex', 1)
        cursor += frames
    add(sequence, 'duration', cursor)
    tree = ET.ElementTree(root)
    ET.indent(tree, space='  ')
    tree.write(destination, encoding='utf-8', xml_declaration=True)


class SyncForgeApp:
    def __init__(self, root):
        self.root = root
        root.title('SyncForge — Audio / Video Synchronizer')
        root.geometry('990x720')
        root.minsize(780, 560)
        self.video_paths = []
        self.audio_paths = []
        self.events = queue.Queue()
        self.output = tk.StringVar(value=str(Path.home() / 'Videos' / 'SyncForge'))
        self.container = tk.StringVar(value='mp4')
        self.manual = tk.StringVar(value='')
        self.progress = tk.DoubleVar(value=0)
        self.status = tk.StringVar(value='Ready. Add videos and separately recorded audio.')
        self.build()
        root.after(100, self.poll)

    def build(self):
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='SyncForge', font=('Segoe UI', 23, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='Waveform sync  •  Batch exports  •  Premiere Pro XML', font=('Segoe UI', 10)).pack(anchor='w', pady=(0, 12))
        panes = ttk.Frame(outer)
        panes.pack(fill='both', expand=True)
        for col, title, key in [(0, 'Video files (camera)', 'video'), (1, 'Audio files (recorder)', 'audio')]:
            f = ttk.LabelFrame(panes, text=title, padding=8)
            f.grid(row=0, column=col, sticky='nsew', padx=(0, 8) if col == 0 else (8, 0))
            lb = tk.Listbox(f, selectmode='extended', height=10, font=('Segoe UI', 10))
            lb.pack(fill='both', expand=True)
            setattr(self, key + '_list', lb)
            btns = ttk.Frame(f)
            btns.pack(fill='x', pady=(8, 0))
            ttk.Button(btns, text='Add files', command=lambda k=key: self.add_files(k)).pack(side='left')
            ttk.Button(btns, text='Remove', command=lambda k=key: self.remove(k)).pack(side='left', padx=6)
            ttk.Button(btns, text='Clear', command=lambda k=key: self.clear(k)).pack(side='left')
        panes.columnconfigure(0, weight=1)
        panes.columnconfigure(1, weight=1)
        panes.rowconfigure(0, weight=1)
        options = ttk.LabelFrame(outer, text='Export settings', padding=10)
        options.pack(fill='x', pady=12)
        ttk.Label(options, text='Output folder').grid(row=0, column=0, sticky='w')
        ttk.Entry(options, textvariable=self.output).grid(row=0, column=1, sticky='ew', padx=8)
        ttk.Button(options, text='Browse', command=self.browse).grid(row=0, column=2)
        ttk.Label(options, text='Container').grid(row=1, column=0, sticky='w', pady=8)
        ttk.Combobox(options, textvariable=self.container, values=['mp4', 'mov'], state='readonly', width=9).grid(row=1, column=1, sticky='w', padx=8)
        ttk.Label(options, text='Manual offset (seconds; blank = auto)').grid(row=2, column=0, sticky='w')
        ttk.Entry(options, textvariable=self.manual, width=14).grid(row=2, column=1, sticky='w', padx=8)
        ttk.Label(options, text='Positive = delay recorder audio; negative = trim its beginning.', foreground='#555').grid(row=3, column=0, columnspan=3, sticky='w', pady=(6, 0))
        options.columnconfigure(1, weight=1)
        ttk.Label(outer, text='Pairing: video 1 ↔ audio 1, video 2 ↔ audio 2, etc. Add files in matching order.').pack(anchor='w')
        self.go = ttk.Button(outer, text='Synchronize + Export Video & Premiere XML', command=self.start)
        self.go.pack(fill='x', pady=(10, 6), ipady=5)
        ttk.Progressbar(outer, variable=self.progress, maximum=100).pack(fill='x')
        ttk.Label(outer, textvariable=self.status).pack(anchor='w', pady=5)
        logframe = ttk.LabelFrame(outer, text='Activity', padding=6)
        logframe.pack(fill='both', expand=True)
        self.logbox = tk.Text(logframe, height=6, wrap='word', state='disabled', font=('Consolas', 9))
        self.logbox.pack(fill='both', expand=True)

    def add_files(self, kind):
        extensions = [('Media files', '*.mp4 *.mov *.mxf *.mkv *.avi *.wav *.mp3 *.m4a *.aac *.flac'), ('All files', '*.*')]
        paths = filedialog.askopenfilenames(title='Select ' + kind + ' files', filetypes=extensions)
        target = self.video_paths if kind == 'video' else self.audio_paths
        lb = getattr(self, kind + '_list')
        for path in paths:
            if path not in target:
                target.append(path)
                lb.insert('end', Path(path).name)

    def remove(self, kind):
        target = self.video_paths if kind == 'video' else self.audio_paths
        lb = getattr(self, kind + '_list')
        for i in reversed(lb.curselection()):
            target.pop(i)
            lb.delete(i)

    def clear(self, kind):
        getattr(self, kind + '_list').delete(0, 'end')
        getattr(self, kind + '_paths').clear()

    def browse(self):
        path = filedialog.askdirectory()
        if path:
            self.output.set(path)

    def start(self):
        if not binary('ffmpeg') or not binary('ffprobe'):
            messagebox.showerror('Missing FFmpeg', 'Install FFmpeg and FFprobe and add them to PATH, or put ffmpeg.exe and ffprobe.exe inside a bin folder beside this script.')
            return
        if not self.video_paths or len(self.video_paths) != len(self.audio_paths):
            messagebox.showerror('File pairing', 'Add an equal, nonzero number of video and audio files in matching order.')
            return
        try:
            manual = None if not self.manual.get().strip() else float(self.manual.get())
            if manual is not None and not math.isfinite(manual):
                raise ValueError('Offset must be finite')
        except ValueError:
            messagebox.showerror('Invalid offset', 'Enter a valid number of seconds, or leave the offset blank.')
            return
        self.go.configure(state='disabled')
        self.progress.set(0)
        args = (list(self.video_paths), list(self.audio_paths), Path(self.output.get()), self.container.get(), manual)
        threading.Thread(target=self.worker, args=args, daemon=True).start()

    def worker(self, videos, audios, output, container, manual):
        try:
            output.mkdir(parents=True, exist_ok=True)
            clips = []
            for i, (v, a) in enumerate(zip(videos, audios), 1):
                self.events.put(('status', f'Processing {i}/{len(videos)}: {Path(v).name}'))
                vi, va, vd = probe(v)
                _, aa, ad = probe(a)
                if not vi or not aa:
                    raise ValueError(f'Invalid pair: {Path(v).name} / {Path(a).name}')
                if manual is None:
                    if not va:
                        raise ValueError(f'{Path(v).name} has no camera reference audio. Use a manual offset.')
                    offset, confidence = find_offset(v, a, vd, ad)
                    self.events.put(('log', f'[{i}] Waveform match {confidence:.2f}; offset {offset:+.3f} s'))
                else:
                    offset = manual
                    self.events.put(('log', f'[{i}] Manual offset {offset:+.3f} s'))
                name = f'{i:03d}_{safe_stem(Path(v).stem)}_synced.{container}'
                dest = output / name
                export_synced(v, a, dest, offset, container, lambda msg: self.events.put(('log', msg)))
                clips.append({'path': str(dest)})
                self.events.put(('log', f'[{i}] Exported {name}'))
                self.events.put(('progress', 90 * i / len(videos)))
            xml = output / 'SyncForge_Premiere_Timeline.xml'
            make_premiere_xml(clips, xml)
            self.events.put(('progress', 100))
            self.events.put(('log', f'Premiere XML: {xml}'))
            self.events.put(('done', f'Finished! {len(clips)} synchronized video(s) + Premiere XML in {output}'))
        except Exception as exc:
            self.events.put(('error', str(exc)))

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'progress':
                    self.progress.set(value)
                elif kind == 'status':
                    self.status.set(value)
                elif kind == 'log':
                    self.logbox.configure(state='normal')
                    self.logbox.insert('end', value + '\n')
                    self.logbox.see('end')
                    self.logbox.configure(state='disabled')
                elif kind in ('done', 'error'):
                    self.go.configure(state='normal')
                    self.status.set(value if kind == 'done' else 'Error: ' + value)
                    (messagebox.showinfo if kind == 'done' else messagebox.showerror)('SyncForge', value)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


if __name__ == '__main__':
    root = tk.Tk()
    ttk.Style().theme_use('clam')
    SyncForgeApp(root)
    root.mainloop()
