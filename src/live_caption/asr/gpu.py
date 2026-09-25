"""Make pip-installed CUDA libraries visible to CTranslate2.

CTranslate2 dlopen()s libcublas/libcudnn by soname at first inference, and
the dynamic loader only searches system paths plus LD_LIBRARY_PATH as it
was when the process *started* — so the copies inside the venv
(nvidia-cublas-cu12, nvidia-cudnn-cu12) are invisible. Loading them first
with RTLD_GLOBAL registers the sonames, and the later dlopen reuses them.
"""

from __future__ import annotations

import ctypes
import importlib.util
from pathlib import Path

# Order matters: each library must come after the ones it links against.
_LIBS = [
    ("nvidia.cublas", "libcublasLt.so.12"),
    ("nvidia.cublas", "libcublas.so.12"),
    ("nvidia.cudnn", "libcudnn_graph.so.9"),
    ("nvidia.cudnn", "libcudnn_ops.so.9"),
    ("nvidia.cudnn", "libcudnn_cnn.so.9"),
    ("nvidia.cudnn", "libcudnn.so.9"),
]

_loaded = False


def preload_cuda_libs() -> bool:
    """Returns True if every library was found and loaded."""
    global _loaded
    if _loaded:
        return True
    try:
        for package, soname in _LIBS:
            spec = importlib.util.find_spec(package)
            if spec is None or not spec.submodule_search_locations:
                return False
            path = Path(next(iter(spec.submodule_search_locations))) / "lib" / soname
            ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL)
    except (OSError, ModuleNotFoundError):
        return False
    _loaded = True
    return True


def cuda_available() -> bool:
    import ctranslate2

    return ctranslate2.get_cuda_device_count() > 0 and preload_cuda_libs()
