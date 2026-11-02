# ReelForge — automatic vertical reel creator

ReelForge is a standalone Windows app that converts a source video into a short-form vertical MP4 reel **under 60 seconds**, using locally processed footage.

It is a separate executable and workflow from SyncForge, housed in the ReelForge subdirectory of the same GitHub repository.

## Installation with no coding

1. Open the [ReelForge Windows build page](https://github.com/ayhamkrekesh/SyncForge-Windows/actions/workflows/reelforge-windows.yml).
2. Open the latest **green** main-branch run.
3. Under **Artifacts**, download **ReelForge-Windows**.
4. Extract the ZIP fully, retaining all internal folders.
5. Double-click **ReelForge.exe**.

The package includes Python libraries, FFmpeg, FFprobe and an offline multilingual speech model. No Python installation, extra model download, account, cloud service or API key is required.

## How to create a reel

1. Drag your video into the upload area, or choose a file.
2. Select framing: **Dynamic crop** for gently zooming 9:16 portrait fill, or **Backdrop** for keeping the full shot on a blurred vertical background.
3. Select music: **Auto** adds procedurally generated original music for montage-style footage, **On** always adds it, **Off** leaves it out.
4. Select **1080p** or **720p**, then click **CREATE MY REEL**.
5. Open the finished folder: the result is **ReelForge_Final_Reel.mp4**, plus **Edit_Decisions.json**.

For videos with speech, the app transcribes words locally using a bundled Whisper base model. It heuristically selects interesting spoken passages within a coherent portion of the input, removes long pauses, adds cuts and subtle zooms, and overlays highlighted-word captions.

For silent footage or footage without usable speech, it samples visual motion and creates a faster montage with original instrumental backing music. Output is a standard vertical H.264/AAC video.

## Quality notes and limitations

- **Under 60 seconds**: final exports are checked with FFprobe; target length is approximately 45–55 seconds where source allows.
- **No guaranteed virality**: the automatic editor is a heuristic. Great hooks, timing and storytelling may still require your review.
- **Automatic captions may be inaccurate**, particularly for noisy, accented, multilingual or overlapping speech.
- **Dynamic crop is centered**: it does not track faces or moving subjects. Use Backdrop when the subject is off center.
- For long source files, visual motion scanning is limited to 15 minutes and spoken highlights are selected from a nearby part of the recording.
- Content stays on the Windows PC; the app does not send footage to an external server.
- For professional publication, always check subtitle wording, crop, narrative context and whether music is suitable.

## Development

Source consists of reel_engine.py (selection, transcription, captions and editing) and reel_studio.py (Windows Qt frontend). Tests in tests/ create their own short synthetic videos with FFmpeg. The GitHub workflow builds a standalone EXE and tests both it and the GUI.

Commands for developers with Python 3.12 and FFmpeg available locally:

~~~powershell
python -m pip install -r ReelForge/requirements.txt
python ReelForge/scripts/download_model.py
python -m pytest -q ReelForge/tests
python ReelForge/reel_studio.py
~~~

Licenses: The dependencies (FFmpeg, Qt/PySide6, Whisper-derived model, and related libraries) have their own licenses; redistribution requires compliance. The background instrumental is generated from the app's own synthesizer.
