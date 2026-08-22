from app.config_defaults import build_default_config
from app.config_normalization import normalize_runtime_config


def test_default_tray_items_include_settings():
    assert "settings" in build_default_config()["tray_menu_items"]


def test_tray_settings_action_dispatches_to_existing_window(monkeypatch):
    from ui.main_window import _SharedShangBackgroundWindow

    called = []
    class StubWindow:
        show_from_tray = lambda self: called.append(True)
        apply_html_wallpaper_from_gui = lambda self: None
        sync_bing_wallpaper = lambda self, **_kwargs: None
        open_wallpaper_sidebar = lambda self: None
        show_about_dialog = lambda self: None
        exit_app = lambda self: None

    window = StubWindow()
    monkeypatch.setattr("ui.main_window.core.config", {"mode": "幻灯片放映"})
    _SharedShangBackgroundWindow._dispatch_tray_action(window, "settings")
    assert called == [True]


def test_old_context_settings_flag_migrates_to_settings_action():
    config = {"ctx_global_settings": True, "tray_menu_items": ["show", "exit"]}
    normalized, _changed = normalize_runtime_config(config)
    assert "settings" in normalized["tray_menu_items"]
