import os
import sys
from pathlib import Path


def app_root():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


APP_ROOT = app_root()
DATA_DIR = APP_ROOT / "data"
CAPTURES_DIR = DATA_DIR / "captures"
DOWNLOADS_DIR = DATA_DIR / "downloads"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
LOG_DIR = DATA_DIR / "logs"


def ensure_data_dirs():
    for folder in [DATA_DIR, CAPTURES_DIR, DOWNLOADS_DIR, TRANSCRIPTS_DIR, LOG_DIR]:
        folder.mkdir(parents=True, exist_ok=True)


def configure_cuda_dll_paths():
    search_roots = []
    if getattr(sys, "frozen", False):
        search_roots.append(Path(sys.executable).resolve().parent / "_internal" / "nvidia")
        search_roots.append(Path(sys.executable).resolve().parent / "nvidia")
    else:
        search_roots.append(Path(sys.prefix) / "Lib" / "site-packages" / "nvidia")

    dll_dirs = []
    for root in search_roots:
        dll_dirs.extend([
            root / "cublas" / "bin",
            root / "cudnn" / "bin",
            root / "cuda_nvrtc" / "bin",
        ])

    existing_dirs = [str(path) for path in dll_dirs if path.exists()]
    for folder in existing_dirs:
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(folder)
            except OSError:
                pass

    if existing_dirs:
        os.environ["PATH"] = os.pathsep.join(existing_dirs + [os.environ.get("PATH", "")])

