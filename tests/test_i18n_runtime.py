from __future__ import annotations

import gzip
import json

import pytest

from app import i18n


@pytest.fixture(autouse=True)
def reset_i18n(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(i18n, "LANG_DIR", str(tmp_path))
    monkeypatch.setattr(i18n, "_CURRENT_LANG", "zh")
    monkeypatch.setattr(i18n, "_TRANSLATIONS", {})
    monkeypatch.setattr(i18n, "_LISTENERS", {})
    yield


def test_runtime_language_switch_updates_t_and_emits_event(tmp_path):
    (tmp_path / "en.json").write_text(
        json.dumps({"设置": "Settings"}, ensure_ascii=False), encoding="utf-8"
    )
    events = []
    unsubscribe = i18n.subscribe_language_changes(events.append)

    assert i18n.t("设置") == "设置"
    i18n.load_language("en")

    assert i18n.get_language() == "en"
    assert i18n.t("设置") == "Settings"
    assert events[-1] == i18n.LanguageChangeEvent("zh", "en", True)
    unsubscribe()


def test_gzip_payload_with_json_suffix_is_recovered(tmp_path):
    raw = json.dumps({"设置": "Settings"}, ensure_ascii=False).encode("utf-8")
    (tmp_path / "en.json").write_bytes(gzip.compress(raw))

    i18n.load_language("en")

    assert i18n.t("设置") == "Settings"


def test_failed_translation_load_falls_back_to_key_and_reports_state(tmp_path):
    (tmp_path / "en.json").write_bytes(b"not-json")
    events = []
    unsubscribe = i18n.subscribe_language_changes(events.append)

    i18n.load_language("en")

    assert i18n.t("设置") == "设置"
    assert events[-1].translations_loaded is False
    unsubscribe()


def test_set_language_preserves_public_signature_and_changes_state():
    i18n.set_language("en")
    assert i18n.get_language() == "en"
    assert i18n.t("missing", default="fallback") == "fallback"


def test_every_static_t_literal_has_an_english_translation():
    """v1.6.0 审计 P1-6 回归钉。

    静态可提取的 ``t("字面量")`` 调用必须全部收录进 en.json，否则英文界面
    会在这些位置回退显示中文原文。运行时拼接的键（少量）不在此列。
    """
    import ast
    from pathlib import Path

    src_dir = Path(i18n.__file__).resolve().parent.parent
    keys: set[str] = set()
    for py_file in src_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            arg = node.args[0]
            if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                continue
            func = node.func
            name = getattr(func, "id", getattr(func, "attr", None))
            if name == "t":
                keys.add(arg.value)

    en = json.loads((src_dir / "lang" / "en.json").read_text(encoding="utf-8"))
    missing = sorted(key for key in keys if key not in en)
    assert not missing, f"en.json 缺少 {len(missing)} 个 t() 键，例如: {missing[:10]}"
