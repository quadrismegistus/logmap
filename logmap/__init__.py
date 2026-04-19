from importlib.metadata import PackageNotFoundError, version as _pkg_version

from .logmap import configure, logmap, pmap, pmap_iter, pmap_run

try:
    __version__ = _pkg_version("logmap")
except PackageNotFoundError:  # running from source tree, not installed
    __version__ = "0.0.0+unknown"

__all__ = [
    "configure",
    "logmap",
    "pmap",
    "pmap_iter",
    "pmap_run",
    "__version__",
]
