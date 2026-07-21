"""Small Qt-free helpers for smooth video-volume transitions."""
from __future__ import annotations


def volume_fade_steps(
    start: int,
    target: int,
    *,
    duration_ms: int = 360,
    interval_ms: int = 40,
) -> tuple[int, ...]:
    """Return monotonic, eased volume steps including the final target.

    The helper is deliberately independent of Qt and media backends. Callers
    may feed the values to mpv JSON IPC, libmpv or AVPlayer on a timer.
    """
    start_value = max(0, min(100, int(start)))
    target_value = max(0, min(100, int(target)))
    if start_value == target_value:
        return (target_value,)
    duration = max(1, int(duration_ms))
    interval = max(10, int(interval_ms))
    count = max(1, round(duration / interval))
    values: list[int] = []
    for index in range(1, count + 1):
        progress = index / count
        # Smoothstep avoids an abrupt first/last volume jump.
        eased = progress * progress * (3.0 - 2.0 * progress)
        value = round(start_value + (target_value - start_value) * eased)
        value = max(0, min(100, value))
        if not values or values[-1] != value:
            values.append(value)
    if not values or values[-1] != target_value:
        values.append(target_value)
    return tuple(values)
