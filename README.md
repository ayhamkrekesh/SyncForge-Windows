# SyncForge Studio v3

A polished Windows desktop app for automatically synchronizing separately recorded video and audio — **without installing Python or using a terminal**.

The **sync engine is unchanged from SyncForge v2**. Version 3 replaces its interface with a responsive dark-themed studio dashboard powered by Qt: animated buttons and progress, teal-to-blue gradients, drag-and-drop file panels, and optional advanced settings.

## Download and run on Windows

1. Open the latest successful **[GitHub Actions build](https://github.com/ayhamkrekesh/SyncForge-Windows/actions/workflows/windows-exe.yml)** on the **main** branch.
2. Under **Artifacts**, download **SyncForge-Studio-v3-Windows**.
3. Right-click the downloaded ZIP and choose **Extract All**.
4. Open the extracted folder and double-click **SyncForge.exe**.

Keep the extracted folder intact, including its _internal and bin directories. FFmpeg and FFprobe are bundled, so you need **no separate installs**. The EXE is not code-signed, so Windows may show a publisher warning.

## One-click workflow

1. Add camera videos under **01 Camera footage** (via button, folder, or drag and drop).
2. Add recorder audio under **02 External recordings** (also in any order).
3. Optionally choose a destination folder and MP4/MOV format.
4. Click **AUTO-SYNC & EXPORT**.
5. Click **Open results folder** after processing.

Each job gets its own timestamped results directory, containing:
- Validated synchronized video exports with camera footage + external sound
- **SyncForge_Premiere_Timeline.xml** (File → Import in Adobe Premiere Pro)
- **SyncForge_Match_Report.csv**, showing successful and uncertain matches

Automatic detection analyzes sound shared by the camera's scratch track and the separate recorder track. A single continuous recorder file can match multiple camera clips. To avoid confusing clips, uncertain matches are reported instead of being exported.

### Advanced manual offset

Expand **Advanced options** to manually supply an offset. Blank means automatic sync. A **positive** value delays the recorder audio relative to video; a **negative** value trims audio from the beginning. Manual mode uses the same number of videos and audio files, paired by natural filename order.

### Important limitations

- Camera videos must have usable reference/scratch audio to auto-sync. Without overlapping audio, a timecode system or a manual offset is required.
- Like v2, the engine assumes approximately matching device clock speeds; if the devices drift over a long take, check sync at both the beginning and end.
- Premiere XML includes a **single consecutive sequence of the rendered synchronized clips** rather than a fully separate, editable original-camera-and-recorded-audio multitrack reconstruction.
- Premiere XML support is validated structurally in automated tests; you should still check import with your installed Premiere version before critical work.
- Media is processed **locally** on your machine, not uploaded to GitHub. Keep original camera and sound files until you verify the results.

## For developers

Source files:
- syncforge_studio.py — animated, modern desktop UI (PySide6 Qt Widgets)
- syncforge_core.py — waveform alignment and FFmpeg/Premiere exports (same backend as v2)
- syncforge.py — original v2 Tkinter UI retained for reference
- tests/test_studio_ui.py — headless UI regression tests
- tests/test_syncforge.py — synthetic-media sync and export regression tests

The [Windows GitHub Actions workflow](.github/workflows/windows-exe.yml) installs Python packages, obtains FFmpeg, runs unit + integration + GUI tests, builds a PyInstaller Windows app, tests its bundled binaries and UI, then publishes the downloadable ZIP artifact.

Development (Python 3.12 + FFmpeg on PATH):

~~~powershell
python -m pip install -r requirements.txt
python -m pytest -q tests
python syncforge_studio.py
~~~

Qt for Python / PySide6-Essentials is distributed under LGPLv3 or commercial licensing. If distributing modified packages to other people, comply with the applicable PySide6 and FFmpeg licenses. The exported application is unsigned.
