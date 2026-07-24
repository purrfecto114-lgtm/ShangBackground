from __future__ import annotations

from itertools import combinations

from build_tools.buildlib.bundle import dynamic_modules, excluded_modules
from build_tools.buildlib.features import FEATURE_KEYS, manifest_payload
from build_tools.buildlib.mpv_runtime import MpvBuildSelection
from build_tools.buildlib.plan import BuildPlan


def _feature_sets():
    for count in range(len(FEATURE_KEYS) + 1):
        yield from (frozenset(values) for values in combinations(FEATURE_KEYS, count))


def _plan(features: frozenset[str]) -> BuildPlan:
    mpv = MpvBuildSelection(
        requested_mode="system",
        mode="system" if "video" in features else "disabled",
        target="linux",
        arch="x86_64",
        runtime_id="",
        payload_dir=None,
        metadata={},
    )
    from pathlib import Path
    return BuildPlan(
        tool="pyinstaller",
        target="linux",
        profile="full",
        mode="standalone",
        jobs=2,
        arch="x86_64",
        features=features,
        mpv=mpv,
        variant="matrix",
        generated_dir=Path("build-generated/test"),
        manifest_path=Path("build-generated/test/build-features.json"),
        staged_mpv_dir=None,
    )


def test_all_feature_combinations_have_consistent_manifest_and_modules():
    checked = 0
    for features in _feature_sets():
        plan = _plan(features)
        payload = manifest_payload(
            "linux", "full", features, tool="pyinstaller", arch="x86_64", video_runtime=plan.mpv.manifest()
        )
        included = set(dynamic_modules(plan))
        excluded = set(excluded_modules(plan))
        for key in FEATURE_KEYS:
            assert payload["enabled"][key] is (key in features)
        assert ("app.mpv_backend" in included) is ("video" in features)
        assert ("platform_adapters.backends.linux.portal_hotkeys" in included) is ("hotkeys" in features)
        assert not included.intersection(excluded)
        checked += 1
    assert checked == 64
