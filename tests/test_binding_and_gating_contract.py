"""绑定与门控契约：use_internal_libmpv 真值表、VIDEO_EXTENSIONS 平台差异、
suffixes 端到端转发与双侧 AST 契约（P0 回归防护）。

ui.source_inputs 依赖 PySide6/Qt，但绑定逻辑本身 Qt-free：本文件以桩替身
导入（PySide6.QtWidgets 与 ui.control_setup），测试结束后弹出桩模块，
不污染真实 Qt 环境。main_window.py 则只做 AST 纯文本契约（绝不导入）。
"""
from __future__ import annotations

import ast
import functools
import importlib
import inspect
import sys
import types
from pathlib import Path

import pytest

from app.source_validation import SourceValidation

_REPO_ROOT = Path(__file__).resolve().parents[1]
_STUB_MODULES = ("PySide6", "PySide6.QtWidgets", "ui.control_setup")


class _FakeEdit:
    """QLineEdit 桩：仅提供 SourceBinding 用到的最小接口。"""

    def __init__(self, text: str = "") -> None:
        self._text = text
        self.connected: list = []

    def text(self) -> str:
        return self._text

    def setText(self, value: str) -> None:
        self._text = value

    @property
    def editingFinished(self):
        return types.SimpleNamespace(connect=lambda callback: self.connected.append(callback))

    def setFocus(self) -> None:
        return None

    def selectAll(self) -> None:
        return None


def _make_controller(source_inputs):
    return source_inputs.SourceInputController(
        parent=object(),
        config={},
        persist=lambda: None,
        set_status=lambda _message: None,
        show_warning=lambda *_args: None,
        translate=lambda text: text,
    )


@pytest.fixture()
def source_inputs_module():
    """以桩替身导入 ui.source_inputs：无 PySide6 的环境也能验证真实绑定逻辑。"""
    saved = {name: sys.modules.get(name) for name in _STUB_MODULES}
    qt_widgets = types.ModuleType("PySide6.QtWidgets")
    qt_widgets.QLineEdit = type("QLineEdit", (), {})
    qt_widgets.QWidget = type("QWidget", (), {})
    pyside6 = types.ModuleType("PySide6")
    pyside6.QtWidgets = qt_widgets
    control_setup = types.ModuleType("ui.control_setup")
    control_setup.set_text_input_validation = lambda *_args, **_kwargs: None
    sys.modules["PySide6"] = pyside6
    sys.modules["PySide6.QtWidgets"] = qt_widgets
    sys.modules["ui.control_setup"] = control_setup
    sys.modules.pop("ui.source_inputs", None)
    try:
        import ui.source_inputs as module

        yield module
    finally:
        sys.modules.pop("ui.source_inputs", None)
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("bundled", True), ("native", True), ("source", True), ("system", False), ("disabled", False)],
)
def test_use_internal_libmpv_truth_table(monkeypatch: pytest.MonkeyPatch, mode: str, expected: bool):
    """内部门控真值表：bundled/native/source 用内部 libmpv；system/disabled 不用。"""
    from app import build_features

    monkeypatch.setattr(build_features, "video_runtime_mode", lambda: mode)
    assert build_features.use_internal_libmpv() is expected


@pytest.mark.parametrize(
    ("platform_id", "expect_wmv"),
    [("windows", True), ("linux", True), ("macos", False)],
)
def test_video_extensions_include_wmv_by_platform(
    monkeypatch: pytest.MonkeyPatch, platform_id: str, expect_wmv: bool
):
    """VIDEO_EXTENSIONS 平台契约：Windows/Linux 含 .wmv，macOS 不含。

    该常量为模块顶层求值：用文档化的 SHANGBACKGROUND_PLATFORM_OVERRIDE +
    importlib.reload(app.config) 驱动；finally 中撤销补丁并 reload 回宿主
    真值，避免污染其他测试。
    """
    from app import build_features, config as app_config

    original_is_enabled = build_features.is_feature_enabled
    monkeypatch.setattr(
        build_features,
        "is_feature_enabled",
        lambda key: True if key == "video" else original_is_enabled(key),
    )
    monkeypatch.setenv("SHANGBACKGROUND_PLATFORM_OVERRIDE", platform_id)
    try:
        importlib.reload(app_config)
        assert ".mp4" in app_config.VIDEO_EXTENSIONS  # video 特性已强制开启
        assert (".wmv" in app_config.VIDEO_EXTENSIONS) is expect_wmv
    finally:
        monkeypatch.undo()
        importlib.reload(app_config)  # 恢复宿主平台真值


def test_bind_with_suffixes_wraps_validator_as_partial_and_filters(
    tmp_path: Path, source_inputs_module
):
    """suffixes 非 None：校验器被 functools.partial 包装（suffixes 关键字
    注入既有校验器链）；白名单外扩展名报 unsupported_type，大小写不敏感放行。"""
    source_inputs = source_inputs_module
    (tmp_path / "clip.avi").write_text("x", encoding="utf-8")
    (tmp_path / "clip.MP4").write_text("x", encoding="utf-8")
    controller = _make_controller(source_inputs)
    edit = _FakeEdit()

    binding = controller.bind(
        edit,
        key="video_file",
        label="视频文件",
        validator=source_inputs.validate_existing_file,
        suffixes=(".mp4",),
    )

    assert isinstance(binding.validator, functools.partial)
    assert binding.validator.func is source_inputs.validate_existing_file
    assert binding.validator.keywords == {"suffixes": (".mp4",)}

    edit.setText(str(tmp_path / "clip.avi"))
    result = binding.validate()
    assert result.error == "unsupported_type"

    edit.setText(str(tmp_path / "clip.MP4"))
    result = binding.validate()
    assert result.valid is True
    assert result.value.endswith("clip.MP4")


def test_bind_without_suffixes_keeps_validator_identity_and_call_shape(source_inputs_module):
    """suffixes=None：校验器身份不变，调用形状保持 (value, optional=...)，
    不注入 suffixes 关键字——既有调用零行为变化。"""
    source_inputs = source_inputs_module
    calls: list[tuple] = []

    def validator(value, *, optional=False):
        calls.append((value, optional))
        return SourceValidation(str(value))

    controller = _make_controller(source_inputs)
    edit = _FakeEdit("/tmp/demo.mp4")
    binding = controller.bind(edit, key="k", label="l", validator=validator)

    assert binding.validator is validator

    result = binding.validate()

    assert result.valid is True
    assert calls == [("/tmp/demo.mp4", True)]  # 精确形状：没有 suffixes 关键字


def test_bind_existing_file_forwards_suffixes(tmp_path: Path, source_inputs_module):
    """bind_existing_file 经 **kwargs 透传 suffixes 到真实校验器链。"""
    source_inputs = source_inputs_module
    (tmp_path / "sound.wav").write_text("x", encoding="utf-8")
    controller = _make_controller(source_inputs)
    edit = _FakeEdit(str(tmp_path / "sound.wav"))

    binding = controller.bind_existing_file(
        edit, key="video_file", label="视频文件", suffixes=(".mp4", ".mkv")
    )

    assert isinstance(binding.validator, functools.partial)
    assert binding.validator.func is source_inputs.validate_existing_file
    assert binding.validator.keywords == {"suffixes": (".mp4", ".mkv")}
    assert binding.validate().error == "unsupported_type"  # .wav 不在白名单


def test_controller_binding_signatures_contract(source_inputs_module):
    """inspect 契约：bind 显式关键字参数 suffixes（默认 None）；
    bind_existing_file 仅 **kwargs 透传（无同名显式参数旁路）。"""
    source_inputs = source_inputs_module
    bind_parameters = inspect.signature(source_inputs.SourceInputController.bind).parameters
    suffixes = bind_parameters["suffixes"]
    assert suffixes.kind is inspect.Parameter.KEYWORD_ONLY
    assert suffixes.default is None

    passthrough = inspect.signature(source_inputs.SourceInputController.bind_existing_file).parameters
    assert any(p.kind is inspect.Parameter.VAR_KEYWORD for p in passthrough.values())
    assert "suffixes" not in passthrough  # 靠 **kwargs 透传而非重复声明


def test_source_text_ast_contracts():
    """AST 纯文本契约（不导入 main_window.py —— 它 import PySide6）：

    1. source_inputs.SourceInputController.bind 保留显式 kwonly ``suffixes``
       且默认 None；
    2. main_window.py 中每个 bind_existing_file 调用点都必须携带 ``suffixes``
       关键字（P0 回归防护：缺失即启动崩溃）。
    """
    source = (_REPO_ROOT / "src/ui/source_inputs.py").read_text(encoding="utf-8")
    bind_function = None
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef) and node.name == "SourceInputController":
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == "bind":
                    bind_function = child
    assert bind_function is not None, "SourceInputController.bind 必须存在"
    keyword_only_names = [argument.arg for argument in bind_function.args.kwonlyargs]
    assert "suffixes" in keyword_only_names
    defaults = dict(zip(keyword_only_names, bind_function.args.kw_defaults))
    suffixes_default = defaults["suffixes"]
    assert isinstance(suffixes_default, ast.Constant) and suffixes_default.value is None

    window_source = (_REPO_ROOT / "src/ui/main_window.py").read_text(encoding="utf-8")
    call_sites = [
        node
        for node in ast.walk(ast.parse(window_source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "bind_existing_file"
    ]
    assert call_sites, "main_window.py 应存在 bind_existing_file 调用点"
    for call in call_sites:
        keyword_names = {keyword.arg for keyword in call.keywords}
        assert "suffixes" in keyword_names, "bind_existing_file 调用点缺少 suffixes（P0 回归）"
