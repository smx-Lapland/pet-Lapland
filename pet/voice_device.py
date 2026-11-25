"""Optional CUDA runtime, loaded only inside a local speech synthesis worker."""
from functools import lru_cache
import os
from pathlib import Path
import sys


CUDA_RUNTIME = Path(__file__).resolve().parents[1] / "models" / "ort-cuda"
DEVICE_LABELS = {"auto": "GPU 优先（自动回退 CPU）", "cpu": "仅 CPU"}
_dll_directories = []


def venv_python(venv):
    """Return the base executable so cancellation reaches inference directly on Windows."""
    if (venv / "Scripts/python.exe").is_file():
        for line in (venv / "pyvenv.cfg").read_text("utf8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "home":
                python = Path(value.strip()) / "python.exe"
                return python if python.is_file() else None
    return None


def cuda_installed():
    return (CUDA_RUNTIME / "onnxruntime/capi/onnxruntime_providers_cuda.dll").is_file()


@lru_cache(maxsize=1)
def execution_providers():
    if os.environ.get("PET_TTS_DEVICE") != "cuda" or not cuda_installed():
        return ["CPUExecutionProvider"]
    # Keep CUDA libraries out of the GUI, microphone ASR and CPU-only workers.
    sys.path.insert(0, str(CUDA_RUNTIME))
    import onnxruntime as ort
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        return ["CPUExecutionProvider"]
    toolkit = os.environ.get("CUDA_PATH_V12_8") or os.environ.get("CUDA_PATH")
    cuda_bin = Path(toolkit) / "bin" if toolkit else None
    cudnn_bin = CUDA_RUNTIME / "nvidia/cudnn/bin"
    for directory in (cuda_bin, cudnn_bin):
        if directory and directory.is_dir():
            _dll_directories.append(os.add_dll_directory(str(directory)))
    ort.preload_dlls(cuda=True, cudnn=False, directory=str(cuda_bin) if cuda_bin else None)
    ort.preload_dlls(cuda=False, cudnn=True, directory=str(cudnn_bin))
    return [("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC",
                                       "cudnn_conv_use_max_workspace": "0"}), "CPUExecutionProvider"]
