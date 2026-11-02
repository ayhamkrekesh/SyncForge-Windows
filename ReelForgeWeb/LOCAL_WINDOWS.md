# ReelForge AI Studio — free local Windows website

This version hosts the existing browser editor directly on your computer at:

http://127.0.0.1:18765

The server listens only on 127.0.0.1; it is not public. Your video footage, sampled images, transcriptions and exported reels remain on your PC. No Render, Railway, paid OpenAI API key or monthly hosting fee is needed.

## Windows installation (no Python required)

1. Open the latest successful Build ReelForge Local Windows workflow in GitHub Actions and download the ReelForge-Local-Windows artifact.
2. Extract the **entire ZIP** to a new folder. Keep the internal files, bin and models directories.
3. Double-click **Start-ReelForge.cmd**.
4. The launcher checks whether Ollama is installed. If not, it offers to open the official download: https://ollama.com/download/windows. Install Ollama once, then rerun the launcher.
5. The launcher asks permission for a **one-time 3.4 GB download** of the free local vision model gemma3:4b. Future launches reuse it without downloading again.
6. Your browser automatically opens to http://127.0.0.1:18765.

Keep the command window open while editing. Closing it stops the website. Ollama may keep running separately in your Windows tray.

The download bundles FFmpeg, FFprobe, the Python runtime and the offline Whisper base speech model. Your own PC performs video analysis, AI decision-making, transcription, editing, rendering and storage.

## Features

- A local AI model analyzes up to eight real still frames sampled from the source video, alongside a word-timestamped transcript.
- Removes long pauses using speech timestamps, builds a coherent under-one-minute edit and produces burnt-in captions.
- Ask AI via the web chat to change the cut, highlight selection or pacing.
- Use the manual editor for precise source in/out, clip ordering, playback speed, zoom, pan, transitions, captions and background music.
- Export a high-resolution vertical H.264/AAC MP4, under 60 seconds.

## Hardware and accuracy

Gemma 3 4B is a roughly 3.4 GB vision model. We recommend at least 16 GB RAM and a capable GPU. Lower-powered CPU-only machines may work but can take considerably longer to analyze footage. Initial model download requires internet; afterward the AI can run offline.

A local 4B model is not equal to a cloud multimodal editing system. It sees sampled frames, not continuous video, and the edit choices, captions and framing may still need your review. The website preview approximates cut order; final visual transitions and timing are rendered into the exported MP4. No software can guarantee virality.

Video projects are saved in your Windows local application data under ReelForgeAI/projects. Use Delete project on the website to remove stored source and exported footage.

## Troubleshooting

If Ollama isn't installed, install it from its official Windows download and try again. If the model download fails, open Command Prompt and run: ollama pull gemma3:4b. If the browser doesn't open automatically, enter http://127.0.0.1:18765 in your browser.

If you get an error, share the terminal message and the website error. Windows binaries are unsigned and Windows SmartScreen may show a publisher warning.

## Developers

Use Python 3.12 with system FFmpeg, pip install the ReelForgeWeb requirements, install Ollama and pull gemma3:4b, then run local_server.py. No cloud API key is required in local mode. The Render cloud Blueprint remains available separately.
