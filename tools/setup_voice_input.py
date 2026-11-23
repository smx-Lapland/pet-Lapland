"""Install the multilingual Whisper base model used by push-to-talk."""
from pathlib import Path

from huggingface_hub import snapshot_download


if __name__ == "__main__":
    snapshot_download("csukuangfj/sherpa-onnx-whisper-base",
                      revision="bb53ee204431c90d314c1cc08d28d23e5b7927cc",
                      allow_patterns=["base-encoder.int8.onnx", "base-decoder.int8.onnx", "base-tokens.txt"],
                      local_dir=Path(__file__).resolve().parents[1] / "models" / "whisper-base")
