"""Download the multilingual faster-whisper base model for full offline packaging.

Run in repository root:
    python ReelForge/scripts/download_model.py
"""
from pathlib import Path
from faster_whisper.utils import download_model

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "models" / "whisper-base"
OUTPUT.mkdir(parents=True, exist_ok=True)
downloaded = download_model("base", output_dir=str(OUTPUT))
if not (OUTPUT / "model.bin").is_file():
    raise RuntimeError(f"Model was downloaded but model.bin not found: {downloaded}")
print(f"Offline multilingual caption model ready: {OUTPUT}")
