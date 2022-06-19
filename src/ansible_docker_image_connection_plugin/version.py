"""Package version"""

import importlib.metadata

# Define default for missing installation (e.g. development environment with cloned source code)
__version__: str = "0.0.0"
try:
    __version__ = importlib.metadata.version(__package__)
except importlib.metadata.PackageNotFoundError:
    pass

__all__ = [
    "__version__",
]
