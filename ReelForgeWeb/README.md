# ReelForge AI Studio — Browser video editor

**Want to run the website privately on your Windows PC without hosting or API charges?** Use the one-click [Local Windows edition](LOCAL_WINDOWS.md), with a free Ollama vision model and fully local video transcription/rendering.

A real web application for professionally styled vertical short-form reels. A vision-capable AI model examines sampled **real video frames** and a **timestamped source transcript**, proposes a coherent edit under one minute, and then FFmpeg renders that edit from your actual footage. You can change the timeline manually or give the AI revised instructions in normal language.

ReelForgeWeb is **separate** from the previous Windows ReelForge and SyncForge applications.

## Implemented features

- Upload MP4, MOV, MKV, WebM, AVI, MXF or MTS videos up to 450 MB and up to 15 minutes.
- Offline Whisper transcription on the hosting server with word timestamps and grouped subtitle cues.
- Analysis of up to 12 actual still frames distributed across the source, supplied to the AI with the time-stamped transcript.
- AI-selected coherent highlights, an opening hook, pause reduction, deliberate hard cuts or gentle dissolves, subtle zooms, speed changes and framing.
- Vertical 1080 x 1920 H.264/AAC output, validated under 60 seconds.
- Editable clip timing, reorderable scene tiles, per-clip speed, zoom, pan and transition, crop-versus-blur fit, caption style and optional subtle original music.
- Editable subtitle lines, followed by server-side burned-in captions.
- Follow-up AI chat commands that regenerate the editable timeline rather than only emitting an uneditable finished video.

The browser player provides an approximate edit preview based on the source video. The exported video renders the real effects, captions and audio mixing; always review the exported MP4.

**No viral guarantee.** The AI sees sampled frames, not all frames of the video. Its editorial choices can miss context, and Whisper can make transcription mistakes.

## Deploy on Render (not Railway)

A complete Render Blueprint is included at the root of the repo: [render.yaml](../render.yaml). It sets up one Docker web service plus a persistent disk mounted at /data to preserve uploads between deployments.

**Hosting is paid.** The selected 1c-2g Render plan offers 1 CPU and 2 GB RAM (approximately USD 25/month at the time of writing), and the 10 GB disk adds approximately USD 2.50/month. Extra bandwidth/storage and OpenAI API requests are billed separately. Review the actual costs before authorizing resources; the assistant will not provision a paid service without permission.

1. In Render select **New -> Blueprint**, connect the GitHub repository **ayhamkrekesh/SyncForge-Windows** on its **main** branch and use the root **render.yaml**.
2. Set two required secrets in the Render creation screen: **OPENAI_API_KEY** (your own billed OpenAI API key) and **APP_PASSWORD** (a strong private password). Never commit these values to GitHub or post them publicly.
3. Confirm the 2 GB web service and 10 GB disk. Deploy only after reviewing the paid services.
4. When the health check passes, open the Render URL and log in using any username and your APP_PASSWORD. The site uses HTTPS and basic password authentication.

The Blueprint requires both secrets and will refuse to start if either is absent, instead of exposing private footage.

### Local development

Install Python 3.12 and system FFmpeg. From ReelForgeWeb, install packages from requirements.txt. Set environment variables OPENAI_API_KEY, APP_PASSWORD and REEL_DATA_DIR, then run uvicorn:

~~~sh
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
~~~

Open http://127.0.0.1:8000. To test from the repo root, use PYTHONPATH=ReelForgeWeb python -m pytest -q ReelForgeWeb/tests.

The GitHub Actions workflow tests actual media rendering, FFmpeg transitions, API security, timeline editing, JS syntax and Docker packaging. CI does not spend billable API credits, so actual AI calls should be verified after you provide your key.

## Security and first-version limitations

- Uploaded footage lives on the Render service's persistent disk, not your Windows computer. Video frames and transcript text are sent to the configured model API for AI editing.
- This private single-user prototype uses HTTP Basic authentication. It is not a production multi-user application with per-user accounts and storage quotas.
- The initial background-job queue runs inside one server process; jobs in progress will not survive redeploys. Resubmit the video if a job is interrupted.
- Projects do not expire automatically. Use **Delete project** inside the website to permanently remove footage, subtitles and exports when no longer needed. Add time-based retention policies before commercial deployment.
- High-resolution FFmpeg exports and speech recognition are CPU-intensive and may take time.
- AI edits do not automatically track faces or follow camera subjects frame by frame. Try blur framing for off-center subjects.
- Optional music is original and synthesized rather than an unlicensed commercial track.
- Always review speech, caption accuracy, context, audio balance and crop before publishing.

Render documentation: https://render.com/docs/disks and https://render.com/docs/blueprint-spec

The Docker image bundles the offline multilingual Whisper base model at build time. If Render restarts while analysis or rendering is active, the server resumes those tasks from persisted project files; an interrupted AI chat revision can be requested again. This is a single-instance recovery mechanism, not a durable distributed job queue.
