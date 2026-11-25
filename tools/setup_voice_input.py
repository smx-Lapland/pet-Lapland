"""Install the local Mandarin dictation and multilingual recognition models."""
from pathlib import Path
import sys

from huggingface_hub import snapshot_download

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pet.voice_input import FILES, MODEL_DIR, SENSEVOICE_DIR, SENSEVOICE_FILES


if __name__ == "__main__":
    snapshot_download("csukuangfj/sherpa-onnx-whisper-base",
                      revision="bb53ee204431c90d314c1cc08d28d23e5b7927cc",
                      allow_patterns=list(FILES), local_dir=MODEL_DIR)
    snapshot_download("csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
                      revision="2365baeacb507f821a0c8120fcee3d484dba7a07",
                      allow_patterns=[*SENSEVOICE_FILES, "LICENSE"], local_dir=SENSEVOICE_DIR)
