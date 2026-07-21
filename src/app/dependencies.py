from __future__ import annotations

import importlib
import sys

from app.config import PLATFORM_ID

_backend = importlib.import_module(f"app.backends.{PLATFORM_ID}.dependencies")
if __name__ == "__main__":
    _main = getattr(_backend, "main", None)
    if _main is None:
        raise SystemExit(f"{_backend.__name__} has no main()")
    raise SystemExit(_main())
sys.modules[__name__] = _backend
