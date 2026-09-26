"""tests/test_weights_meta.py —— 权重 JSON 清单的强校验与「最新权重」发现（**免 torch**）。

2026-09-26（item 6d）：「坏文件必须响亮拒绝」这一族用例原本住在 `test_weights_io.py`，
只因为与张量编解码同住一个模块，就被 `import torch` 拖进 torch 测试路径。校验逻辑
（format / schema_major / 非空 params）与版本化文件发现都是**纯 stdlib + json**，已抽到
`data/weights_meta.py` ⇒ 本文件既不 import torch，也不 import `data.weights_io`。

**反例守线**：除三条 raise 用例之外，另有一条正例（合法 meta 静默通过）——否则一个
「无条件抛」的实现也能让这族用例全绿。

**接线锚留在 `test_weights_io.py`**：那边断言 `load_weights_json` 真的调到了本校验器
（本文件只负责校验器本身的判据）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.weights_meta import COVERAGE_RAISE, COVERAGE_WARN, validate_weights_meta
from schema import OBS_SCHEMA_MAJOR

#: 手写的 `data` 字段：四个 little-endian float32 `1.0`（2×2 全 1 张量）的 base64。
#: 校验器**不碰** `data`（base64 → 张量在 weights_io 那一半），写成真值只是为了让
#: 这份 JSON 与 `save_weights_json` 写出的形态逐字段同构。
ONES_2X2_B64 = "AACAPwAAgD8AAIA/AACAPw=="


def _write_meta(tmp_path: Path, **meta_overrides: object) -> Path:
    """写一个最小权重文件：1 个参数 + 默认 meta；可覆盖字段模拟损坏/旧版本。"""
    p = tmp_path / "weights.json"
    meta: dict[str, object] = {
        "format": "nn-weights-json",
        "version": 1,
        "schema_major": OBS_SCHEMA_MAJOR,
        "arch": {"kind": "test"},
        "params": {"x.weight": {"shape": [2, 2], "data": ONES_2X2_B64}},
    }
    meta.update(meta_overrides)
    p.write_text(json.dumps(meta), encoding="utf-8")
    return p


def _validate(path: Path) -> None:
    """读盘 + 校验（与 `load_weights_json` 的前半步同序）。"""
    validate_weights_meta(json.loads(path.read_text(encoding="utf-8")), str(path))


def test_valid_meta_passes_silently() -> None:
    """正例：合法 meta 必须**静默**通过（没有这条，「永远抛」也算绿）。"""
    validate_weights_meta(
        {
            "format": "nn-weights-json",
            "schema_major": OBS_SCHEMA_MAJOR,
            "params": {"x.weight": {"shape": [2, 2], "data": ONES_2X2_B64}},
        },
        "/tmp/not-read.json",
    )


def test_load_schema_mismatch_raises(tmp_path: Path) -> None:
    """schema_major ≠ 当前 → 拒绝加载（旧布局权重静默灌进新模型 = 逐字段错位）。"""
    p = _write_meta(tmp_path, schema_major=OBS_SCHEMA_MAJOR - 1)
    with pytest.raises(ValueError, match="schema_major"):
        _validate(p)


def test_load_unknown_format_raises(tmp_path: Path) -> None:
    p = _write_meta(tmp_path, format="not-nn-weights-json")
    with pytest.raises(ValueError, match="format"):
        _validate(p)


def test_load_empty_params_raises(tmp_path: Path) -> None:
    p = _write_meta(tmp_path, params={})
    with pytest.raises(ValueError, match="params"):
        _validate(p)


def test_missing_params_key_raises(tmp_path: Path) -> None:
    """整个 `params` 键缺失（截断/写坏）也必须拒，而不是 KeyError 逃逸。"""
    p = _write_meta(tmp_path)
    meta = json.loads(p.read_text(encoding="utf-8"))
    meta.pop("params")
    with pytest.raises(ValueError, match="params"):
        validate_weights_meta(meta, str(p))


def test_non_dict_json_raises() -> None:
    """文件里是个 JSON 数组（有人拿错文件）⇒ 同样走「无 params」这条响亮拒绝。"""
    with pytest.raises(ValueError, match="params"):
        validate_weights_meta([1, 2, 3], "/tmp/nope.json")


def test_coverage_constants_guard_the_legit_boundary() -> None:
    """门禁常量必须保护合法 warm-start 边界（95.2% ≥ WARN ≥ RAISE），防未来误调。"""
    assert COVERAGE_RAISE < 0.90 < COVERAGE_WARN
    # PPOStudent←StudentNet 的覆盖率略高于 0.95（value 头 2/42 缺失）
    # —— 若未来有人把 COVERAGE_WARN 提到 0.99，合法路径会被误杀，故锚定该值。
    assert COVERAGE_WARN <= 0.96


def test_latest_weights_path_prefers_newest_versioned_and_falls_back(tmp_path: Path) -> None:
    """自动发现规则（无版本化文件时回落 `weights.json`，全无则 None）。"""
    from data.weights_meta import latest_weights_path

    assert latest_weights_path(str(tmp_path / "missing")) is None
    assert latest_weights_path(str(tmp_path)) is None

    active = tmp_path / "weights.json"
    active.write_text("{}", encoding="utf-8")
    assert latest_weights_path(str(tmp_path)) == str(active)

    for name in (
        "weights.20260901-000000_ep1_val0.1.json",
        "weights.20260920-120000_ep9_val0.0.json",
        "weights.20260910-235959_ep5_val0.2.json",
    ):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    assert latest_weights_path(str(tmp_path)) == str(
        tmp_path / "weights.20260920-120000_ep9_val0.0.json"
    )
