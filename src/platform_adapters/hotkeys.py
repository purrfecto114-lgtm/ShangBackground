from __future__ import annotations

import importlib
from pathlib import Path
import sys

_source_root = str(Path(__file__).resolve().parents[1])
if _source_root not in sys.path:
    sys.path.insert(0, _source_root)

from app.config import PLATFORM_ID

_backend = importlib.import_module(f"platform_adapters.backends.{PLATFORM_ID}.hotkeys")
sys.modules[__name__] = _backend
