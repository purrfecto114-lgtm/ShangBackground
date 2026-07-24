from __future__ import annotations

import gzip

import pytest

from build_tools.buildlib.bundle import assert_plain_json_resources


def test_plain_json_resources_are_accepted(tmp_path):
    (tmp_path / "en.json").write_text('{"设置":"Settings"}', encoding="utf-8")
    assert_plain_json_resources(tmp_path)


def test_disguised_gzip_json_is_rejected(tmp_path):
    (tmp_path / "en.json").write_bytes(gzip.compress(b'{}'))
    with pytest.raises(RuntimeError, match="gzip-compressed resource"):
        assert_plain_json_resources(tmp_path)
