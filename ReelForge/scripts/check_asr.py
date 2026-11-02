"""Run a real offline Whisper inference after model download on CI."""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from asr_health import check_asr_runtime

if __name__ == "__main__":
    result = check_asr_runtime(str(root / "models" / "whisper-base"))
    print(result)
