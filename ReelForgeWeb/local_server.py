"""Start ReelForge AI Studio on this Windows computer, never a public server.

Packaged with PyInstaller for a one-click local browser-based application.
The separately installed Ollama model analyzes sampled frames locally.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
import threading
import time
import webbrowser


def setup_local_paths():
    if getattr(sys, "frozen", False):
        exe_folder = Path(sys.executable).resolve().parent
        bundled_folder = Path(getattr(sys, "_MEIPASS", exe_folder))
        assets = bundled_folder
        bin_folder = exe_folder / "bin"
        model_folder = exe_folder / "models" / "whisper-base"
    else:
        assets = Path(__file__).resolve().parent
        exe_folder = assets
        bin_folder = assets / "bin"
        model_folder = assets / "models" / "whisper-base"
    if bin_folder.is_dir():
        os.environ["PATH"] = str(bin_folder) + os.pathsep + os.environ.get("PATH", "")
    os.environ["AI_PROVIDER"] = "ollama"
    os.environ.setdefault("OLLAMA_MODEL", "gemma3:4b")
    os.environ["REEL_DATA_DIR"] = str(
        Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) /
        "ReelForgeAI" / "projects"
    )
    os.environ["WHISPER_MODEL"] = str(model_folder) if model_folder.is_dir() else "base"
    os.environ["REQUIRE_AUTH"] = "0"
    os.environ["REQUIRE_AI"] = "0"
    # Binding to 127.0.0.1 is critical: other network devices can't connect.
    return assets, exe_folder


def diagnostics():
    import shutil
    import subprocess
    from local_ai import status
    for name in ("ffmpeg", "ffprobe"):
        executable = shutil.which(name)
        if not executable:
            raise RuntimeError(
                f"{name} not found. Extract the FULL Windows ZIP including bin/."
            )
        subprocess.run([executable, "-version"], capture_output=True,
                       check=True, timeout=20)
    from faster_whisper import WhisperModel
    folder = os.environ["WHISPER_MODEL"]
    if not (Path(folder) / "model.bin").exists():
        raise RuntimeError("Whisper caption model missing from models/whisper-base.")
    # Load only when --self-test-model; normal startup loads on the first video.
    if "--self-test-model" in sys.argv:
        WhisperModel(folder, compute_type="int8", device="cpu", cpu_threads=2)
    return status()


def open_when_ready(port):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                webbrowser.open(f"http://127.0.0.1:{port}/")
                return
        except OSError:
            time.sleep(0.35)


def main():
    setup_local_paths()
    if "--self-test" in sys.argv or "--self-test-model" in sys.argv:
        info = diagnostics()
        print("Local runtime ready. Ollama status: " + info["message"])
        return 0
    from local_ai import status
    info = status()
    print("ReelForge AI Studio — local website", flush=True)
    print("AI: " + info["message"], flush=True)
    print("Your videos stay on this computer.", flush=True)
    import uvicorn
    from app import app
    port = int(os.environ.get("REEL_LOCAL_PORT", "18765"))
    if not 1024 <= port <= 65535:
        raise ValueError("Local port must be between 1024 and 65535.")
    threading.Thread(target=open_when_ready, args=(port,), daemon=True).start()
    print(f"Open http://127.0.0.1:{port}/ in your browser.", flush=True)
    print("Close this window or press Ctrl+C to stop the website.", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning",
                access_log=False, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
