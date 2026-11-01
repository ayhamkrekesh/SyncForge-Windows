# SyncForge — Windows Audio / Video Auto-Sync

**A Windows desktop tool that matches separate camera video and external sound-recorder files using their recorded sound.** Produces synchronized playable videos, an Adobe Premiere Pro-importable XML timeline, and a CSV pairing report.

## Easiest installation (no Python, no terminal)

1. Open **[GitHub Actions](https://github.com/ayhamkrekesh/SyncForge-Windows/actions)**.
2. Open the latest **green** run of **Test and Build SyncForge Windows** on the main branch.
3. Scroll to **Artifacts**, click **SyncForge-Windows**, and download the ZIP.
4. Right-click the ZIP in Windows Explorer → **Extract All**.
5. In the extracted folder, double-click **SyncForge.exe**.

**Keep all the extracted files together.** The _internal folder and bin folder (including FFmpeg) must remain beside the EXE. You **do not** need to install Python or FFmpeg separately. Windows may warn about an unsigned application; only proceed if you trust the repository and build.

## How to use

1. Click **Add files** (or **Add folder**) under Camera videos.
2. Add one or more separate recorder audio files, in any order.
3. Choose an output folder if you want to change the default.
4. Click **AUTO-SYNC + EXPORT EVERYTHING**.
5. Open the new timestamped SyncForge output folder. It contains synchronized videos, SyncForge_Premiere_Timeline.xml and SyncForge_Match_Report.csv.
6. In **Adobe Premiere Pro**, choose **File → Import** and select the XML file. The XML references the exported synchronized clips; do not move those clips before importing.

### What automatic pairing does

- Reads the scratch/reference audio track embedded in each camera video.
- Compares the audio against every supplied recorder file and calculates the timing offset.
- Supports multiple video clips recorded during **one long continuous recorder file**.
- Rejects weak or ambiguous matches instead of silently pairing the wrong audio.
- Exports clips with camera video and the externally recorded audio replacing its original scratch sound.

**Important:** Automatic sync requires at least about 4 seconds of distinctive overlapping sound in both the camera video and recorder file. If the camera recorded no audio, waveform-based software cannot discover the offset. Use the optional manual offset field instead (one audio file per video in sorted filename order).

Manual offset convention: **positive = external recorder audio begins later than video**, **negative = external recorder begins earlier**. Example: +2.5 inserts 2.5 seconds of silence before the recorded sound; -2.5 removes 2.5 seconds from its beginning.

### Limitations and quality checks

- Files are analyzed locally on your computer; no footage is uploaded to GitHub.
- XML provides one concatenated editing sequence of the **rendered synced clips**, not a complete reconstruction of your original multi-track camera/recorder project. You can still edit/unlink the output video and audio in Premiere.
- Sync is waveform-based, not timecode-based, and assumes the two devices run at approximately the same clock speed. Long recordings with clock drift may need additional correction.
- Very quiet scratch audio, repetitive music or recordings with no common sound may be rejected.
- Verify XML import and playback on your own Premiere version before relying on an important production.
- Verify lip sync on several points of a long take. Never delete source recordings.

## Building or changing the source

The syncforge.py file contains the desktop GUI; syncforge_core.py contains the matching and export engine. The [GitHub Actions workflow](.github/workflows/windows-exe.yml) installs dependencies, downloads FFmpeg, runs tests with actual generated media, builds a standalone Windows executable using PyInstaller, and attaches it to the workflow run.

For development with Python 3.12 and FFmpeg on PATH:

~~~powershell
python -m pip install -r requirements.txt
python -m pytest -q tests
python syncforge.py
~~~

## Safety

The downloaded executable is unsigned and can trigger Microsoft SmartScreen; avoid downloading look-alike executables from unknown websites. Media files are processed as untrusted inputs by FFmpeg; keep software updated.
