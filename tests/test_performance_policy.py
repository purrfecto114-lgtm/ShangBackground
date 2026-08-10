from app.performance import performance_profile


def test_performance_profiles_are_monotonic_for_responsiveness():
    saver = performance_profile("power_saver")
    balanced = performance_profile("balanced")
    fast = performance_profile("performance")

    assert saver.preview_poll_ms > balanced.preview_poll_ms > fast.preview_poll_ms
    assert saver.preview_startup_ms > balanced.preview_startup_ms > fast.preview_startup_ms
    assert saver.icon_decode_limit_mb < balanced.icon_decode_limit_mb < fast.icon_decode_limit_mb
    assert saver.icon_cache_items < balanced.icon_cache_items < fast.icon_cache_items
    assert max(saver.followup_refresh_ms) >= max(balanced.followup_refresh_ms) >= max(fast.followup_refresh_ms)


def test_unknown_performance_profile_is_balanced():
    assert performance_profile("future-value") == performance_profile("balanced")
