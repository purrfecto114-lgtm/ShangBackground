"""v1.6.3 navigation-latency contract tests.

Two hot-path regressions are pinned here:

1. ``operation_persists_itself`` — the run_core trailing ``save_config()`` is
   skipped exactly for engine operations whose service layer commits the
   config transaction itself (the duplicate write the v1.6.1 hotkey path
   already dropped).
2. History navigation must anchor through the service cache, and the
   WM_SETTINGCHANGE handler must bypass it — see
   ``test_navigation_and_setting_change_cache_policy``.
"""

from __future__ import annotations

from pathlib import Path

from app.wallpaper_action_policy import operation_persists_itself, wallpaper_action_availability


def test_self_persisting_operations_are_recognized():
    for name in (
        "previous_wallpaper",
        "next_wallpaper",
        "random_wallpaper",
        "set_wallpaper",
        "set_wallpaper_direct",
    ):
        assert operation_persists_itself(name) is True


def test_non_persisting_operations_keep_trailing_save():
    # Slideshow timer operations do not persist config inside the service.
    for name in ("start_slideshow", "stop_slideshow", "restart_slideshow", "set_fit_mode", "unknown_op"):
        assert operation_persists_itself(name) is False


def test_operation_persists_itself_accepts_function_objects():
    def previous_wallpaper():  # pragma: no cover - never called
        raise AssertionError

    assert operation_persists_itself(previous_wallpaper) is True

    def unrelated():  # pragma: no cover - never called
        raise AssertionError

    assert operation_persists_itself(unrelated) is False


def test_availability_contract_unchanged():
    """Guard against the new predicate drifting the existing policy module."""
    assert wallpaper_action_availability("幻灯片放映", "previous").allowed is True
    assert wallpaper_action_availability("图片", "next").allowed is False
    assert wallpaper_action_availability("HTML", "refresh_html").allowed is True


def test_navigation_and_setting_change_cache_policy():
    """Source-level contract for the v1.6.3 navigation cache policy.

    - ``previous_wallpaper``/``next_wallpaper`` must anchor on the cached
      service read (no ``use_cache=False``), so consecutive clicks do not pay
      a system wallpaper query each.
    - ``handle_system_setting_change`` must query with ``use_cache=False``
      because apply() now primes the cache; a cached read there would mask
      external wallpaper changes for the rest of the TTL. It must also feed
      the authoritative answer back via ``note_current_wallpaper`` so the
      navigation cache re-syncs immediately (external-change staleness
      window: TTL-bound → zero).
    - ``set_wallpaper`` (the sidebar/browse path) must derive its rollback
      anchor from the cached config/facade values, never a forced system
      query — the other half of the "sidebar never pays a system query"
      claim.
    """
    engine_text = (Path("src/core/engine.py")).read_text(encoding="utf-8")

    previous_body = engine_text.split("def previous_wallpaper():", 1)[1].split("def next_wallpaper():", 1)[0]
    next_body = engine_text.split("def next_wallpaper():", 1)[1].split("def random_wallpaper():", 1)[0]
    for body, label in ((previous_body, "previous_wallpaper"), (next_body, "next_wallpaper")):
        assert "wallpaper.get_current()" in body, f"{label} must use the cached service read"
        assert "use_cache=False" not in body, (
            f"{label} must not force a fresh system query on every click (v1.6.3 navigation latency fix)"
        )

    setting_body = engine_text.split("def handle_system_setting_change() -> None:", 1)[1].split("@WNDPROC", 1)[0]
    assert "get_current_wallpaper(use_cache=False)" in setting_body, (
        "handle_system_setting_change must bypass the primed cache (event-driven freshness)"
    )
    assert "wallpaper.note_current_wallpaper(current)" in setting_body, (
        "handle_system_setting_change must re-sync the navigation cache with the authoritative value"
    )

    set_body = engine_text.split("def set_wallpaper(path, operation_name=", 1)[1].split("def apply_browsed_wallpaper", 1)[0]
    assert 'config.get("current_wallpaper") or get_current_wallpaper()' in set_body, (
        "set_wallpaper must prefer the in-memory config value, then the cached facade read"
    )
    assert "use_cache=False" not in set_body, (
        "set_wallpaper (sidebar/browse path) must not force a fresh system query"
    )


def test_run_core_gates_trailing_save_on_policy():
    """run_core's worker must gate the trailing save_config on the policy
    predicate instead of saving unconditionally."""
    main_text = (Path("src/ui/main_window.py")).read_text(encoding="utf-8")
    assert "if not operation_persists_itself(name):" in main_text, (
        "run_core must skip the duplicate trailing save for self-persisting operations"
    )
    worker_body = main_text.split("def run_core(self, fn, *args):", 1)[1]
    gated_pos = worker_body.find("if not operation_persists_itself(name):")
    save_pos = worker_body.find("core.save_config()")
    assert gated_pos != -1 and save_pos > gated_pos, "core.save_config() must follow the policy gate"


def test_previous_wallpaper_anchors_on_cached_current(monkeypatch, tmp_path):
    """Behavioral proof of the v1.6.3 navigation contract: the anchor read
    must go through the cached service getter and the apply transaction must
    receive the cached value as ``previous_path``."""
    from core import engine as core

    wallpapers = [tmp_path / f"{index}.jpg" for index in range(3)]
    for wallpaper in wallpapers:
        wallpaper.write_bytes(b"image")

    calls: dict[str, list] = {"get_current": [], "apply": []}

    class _WallpaperService:
        def get_current(self, *, use_cache: bool = True):
            calls["get_current"].append(use_cache)
            return str(wallpapers[2])

        def apply(self, path, operation_name="系统", **kwargs):
            calls["apply"].append((path, operation_name, kwargs))
            return True

    class _Slideshow:
        def reset(self):
            return True

    class _Services:
        wallpaper = _WallpaperService()
        slideshow = _Slideshow()

    monkeypatch.setattr(core, "_get_application_services", lambda: _Services())
    monkeypatch.setattr(core, "reset_slide_timer", lambda: True)

    old_mode = core.config.get("mode")
    old_history = core.config.get("history")
    old_current = core.config.get("current_wallpaper")
    # history is newest-first; current anchor is wallpapers[2].
    core.config["mode"] = "幻灯片放映"
    core.config["history"] = [str(path) for path in reversed(wallpapers)]
    core.config["current_wallpaper"] = str(wallpapers[2])
    try:
        assert core.previous_wallpaper() is True
    finally:
        core.config["mode"] = old_mode
        core.config["history"] = old_history
        core.config["current_wallpaper"] = old_current

    assert calls["get_current"] == [True], "anchor must use the cached getter (use_cache defaults to True)"
    assert len(calls["apply"]) == 1
    applied_path, _operation, kwargs = calls["apply"][0]
    assert applied_path == str(wallpapers[1]), "navigation must move one entry back in newest-first history"
    assert kwargs.get("record_history") is False, "history navigation must not reorder the MRU list"
    assert kwargs.get("previous_path") == str(wallpapers[2]), "rollback anchor must be the cached current wallpaper"


def test_setting_change_handler_resyncs_navigation_cache(monkeypatch, tmp_path):
    """v1.6.3 behavioral contract: after an external wallpaper change the
    WM_SETTINGCHANGE handler (1) queries the authoritative system value with
    the cache bypassed and (2) feeds it back into the navigation cache via
    ``note_current_wallpaper``."""
    from core import engine as core

    authoritative = tmp_path / "external.jpg"
    authoritative.write_bytes(b"external")
    queries: list[dict] = []
    notes: list[str] = []
    pushes: list[str] = []

    class _WallpaperService:
        def note_current_wallpaper(self, path):
            notes.append(str(path))

    class _Services:
        wallpaper = _WallpaperService()

    def _fake_get_current(*, use_cache: bool = True):
        queries.append({"use_cache": use_cache})
        return str(authoritative)

    monkeypatch.setattr(core, "get_current_wallpaper", _fake_get_current)
    monkeypatch.setattr(core, "_get_application_services", lambda: _Services())
    monkeypatch.setattr(core, "push_wallpaper", lambda path, **_kwargs: pushes.append(str(path)) or True)
    monkeypatch.setattr(core, "_queue_ui_preview_update", lambda *_args: None)

    old_current = core.config.get("current_wallpaper")
    core.config["current_wallpaper"] = str(authoritative)
    try:
        core.handle_system_setting_change()
        assert queries == [{"use_cache": False}], "the handler must query with the cache bypassed"
        assert notes == [str(authoritative)], "the authoritative value must re-sync the navigation cache"
        assert pushes == [], "an unchanged wallpaper must not be pushed into history"

        # A genuine external change (config disagrees) must also be recorded.
        core.config["current_wallpaper"] = str(tmp_path / "old.jpg")
        core.handle_system_setting_change()
        assert queries == [{"use_cache": False}, {"use_cache": False}]
        assert notes == [str(authoritative), str(authoritative)]
        assert pushes == [str(authoritative)], "the external wallpaper must be pushed into history"
    finally:
        core.config["current_wallpaper"] = old_current
