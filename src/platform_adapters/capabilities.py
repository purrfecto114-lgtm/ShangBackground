from __future__ import annotations

import importlib
from pathlib import Path
import sys

# Support direct source execution (python src/platform_adapters/<tool>.py).
_source_root = str(Path(__file__).resolve().parents[1])
if _source_root not in sys.path:
    sys.path.insert(0, _source_root)

from app.config import PLATFORM_ID

_backend = importlib.import_module(f"platform_adapters.backends.{PLATFORM_ID}.capabilities")
if __name__ == "__main__":
    _main = getattr(_backend, "main", None)
    if _main is None:
        raise SystemExit(f"{_backend.__name__} has no main()")
    raise SystemExit(_main())
sys.modules[__name__] = _backend
