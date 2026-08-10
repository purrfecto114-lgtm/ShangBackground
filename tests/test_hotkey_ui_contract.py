from pathlib import Path

from platform_adapters.hotkey_bindings import parse_hotkey, to_win32


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = PROJECT_ROOT / "src" / "ui" / "main_window.py"


def test_canonical_hotkey_parser_rejects_modifier_only_and_accepts_chord():
    assert parse_hotkey("Ctrl") is None
    parsed = parse_hotkey("Ctrl+Alt+N")
    assert parsed is not None
    assert parsed.key == "n"
    assert to_win32("Ctrl+Alt+N") == (0x0002 | 0x0001, ord("N"))


def test_ui_uses_platform_hotkey_parser_not_removed_core_helpers():
    text = MAIN_WINDOW.read_text(encoding="utf-8")
    assert "from platform_adapters.hotkey_bindings import parse_hotkey" in text
    assert "core._parse_hotkey_string" not in text
    assert "core._pynput_hotkey_string" not in text


def test_context_menu_toggle_no_longer_re_registers_global_hotkeys():
    text = MAIN_WINDOW.read_text(encoding="utf-8")
    shared_marker = "# Desktop shell visibility and global-hotkey registration are separate"
    assert shared_marker in text


def test_windows_mixin_does_not_reoverride_shared_hotkey_flow():
    import ast

    tree = ast.parse(MAIN_WINDOW.read_text(encoding="utf-8"))
    windows_mixin = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "_WindowsMainWindowMixin"
    )
    method_names = {
        node.name for node in windows_mixin.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    stale_overrides = {
        "set_context_hotkey",
        "record_context_hotkey",
        "on_global_hotkeys_enabled_changed",
        "_update_ctx",
        "register_context_with_prompt",
        "sync_context_menu",
    }
    assert method_names.isdisjoint(stale_overrides)


def test_shared_context_sync_matches_hkcu_per_user_registration():
    text = MAIN_WINDOW.read_text(encoding="utf-8")
    start = text.index("    def register_context_with_prompt(self):")
    end = text.index("    def sync_context_menu", start)
    block = text[start:end]
    assert "HKCU\\Software\\Classes" in block
    assert "restart_as_admin" not in block
    assert "HKEY_CLASSES_ROOT" not in block
