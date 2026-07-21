from __future__ import annotations

from pathlib import Path

import pytest

from build_tools.buildlib.locking import ExclusiveBuildLock


def test_second_build_process_lock_is_rejected(tmp_path: Path):
    lock_path = tmp_path / ".build.lock"
    with ExclusiveBuildLock(lock_path):
        with pytest.raises(RuntimeError, match="already in progress"):
            with ExclusiveBuildLock(lock_path):
                pass

    with ExclusiveBuildLock(lock_path):
        pass
