"""权重序列化/加载回归（plan/python-refactor.md P0-4）。

**背景**：`load_state_into` 用 `strict=False` + `latest_weights_path` 自动挑最新
文件，形成一条静默失败链——目录里混入错误模型族（intent/goal/teacher）的权重时，
绝大多数 key 变 `unexpected` 被丢弃，模型保持随机初始化，训练照常跑、日志照常绿，
唯一线索是 stdout 一行 print。P0-4 修复：读入端强校验（format/schema_major）+ 加载
端覆盖率两档门禁（<50% raise / <95% warn）。

本文件需要真张量（往返/覆盖率/NaN/Inf），故保留顶层 `import torch`；**免 torch 的那一半**
（清单校验的判据 + 覆盖率常量 + 最新权重发现）在 `tests/test_weights_meta.py`（2026-09-26）。

实测覆盖率基线（2026-09-02）：
  PPOStudent ← StudentNet = 95.2%（合法 warm-start，value 头缺失）→ warn 不 raise
  PPOStudent ← NNPolicy   = 14.3%（错误族）                          → raise
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import torch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.weights_io import (
    load_state_into,
    load_weights_json,
    save_weights_json,
    tensor_to_b64,
)
from models.core import NNPolicy
from models.student import PPOStudent, StudentNet
from schema import OBS_SCHEMA_MAJOR


def _save_meta(tmp_path: Path, **meta_overrides) -> Path:
    """写一个最小权重文件：1 个参数 + 默认 meta；可覆盖字段模拟损坏/旧版本。"""
    p = tmp_path / "weights.json"
    params = {"x.weight": {"shape": [2, 2], "data": tensor_to_b64(torch.ones(2, 2))}}
    meta = {
        "format": "nn-weights-json",
        "version": 1,
        "schema_major": OBS_SCHEMA_MAJOR,
        "arch": {"kind": "test"},
        "params": params,
    }
    meta.update(meta_overrides)
    p.write_text(json.dumps(meta), encoding="utf-8")
    return p


# ---- 读入端强校验 ----
# 校验器本身的判据（schema 不匹配 / 未知 format / 空 params / 正例 / 覆盖率常量）已搬到
# `tests/test_weights_meta.py`（2026-09-26，item 6d：那半边是纯 stdlib + json，不该被
# 本文件的 `import torch` 拖进 torch 测试路径）。本文件只留一条**接线锚**。


def test_load_weights_json_runs_the_meta_validation(tmp_path: Path) -> None:
    """接线锚：`load_weights_json` 必须把盘上的 meta 真的交给 weights_meta 的校验器。

    判据只关心「那一步发生了」——具体哪些 meta 该拒在 test_weights_meta.py；没有这条，
    哪天有人为省一次调用把校验摘掉，那边的用例照样全绿。
    """
    p = _save_meta(tmp_path, schema_major=OBS_SCHEMA_MAJOR - 1)
    with pytest.raises(ValueError, match="schema_major"):
        load_weights_json(str(p))


def test_load_roundtrip_preserves_values(tmp_path: Path) -> None:
    """save → load 往返：张量逐值一致。"""
    p = tmp_path / "rt.json"
    model = StudentNet()
    save_weights_json(model, str(p))
    _meta, params = load_weights_json(str(p))
    for k, v in model.state_dict().items():
        torch.testing.assert_close(params[k], v)


# ---- 加载端覆盖率门禁 ----
def test_load_wrong_family_raises(tmp_path: Path) -> None:
    """错误权重族（NNPolicy → PPOStudent，覆盖率 ~14%）必须 raise，不得静默随机初始化。

    这是 P0-4 的核心回归锚点：修复前该场景只有一行 print，训练照常跑。
    """
    src = NNPolicy()
    p = tmp_path / "wrong-family.json"
    save_weights_json(src, str(p))
    dst = PPOStudent()
    with pytest.raises(ValueError, match="覆盖率"):
        load_state_into(dst, str(p))


def test_load_legit_warmstart_partial_ok(tmp_path: Path) -> None:
    """合法 warm-start（StudentNet → PPOStudent，value 头缺失）→ 不 raise，继续。

    coverage=95.2% ≥ COVERAGE_WARN：仅打印 missing 提示；value_head 保持加载前
    状态（BC 检查点没有该键，不得被污染）。
    """
    src = StudentNet()
    p = tmp_path / "warm.json"
    save_weights_json(src, str(p))
    dst = PPOStudent()
    value_before = dst.value_head.weight.detach().clone()
    load_state_into(dst, str(p))  # 不应抛错
    # value 头保持加载前状态（BC 检查点无该键，严格为"未被触碰"而非仅非零）
    torch.testing.assert_close(dst.value_head.weight, value_before)
    # 共享主干确实被加载（与 src 一致）
    torch.testing.assert_close(dst.stem.weight, src.stem.weight)


def test_load_exact_family_full_match(tmp_path: Path) -> None:
    """同族同架构：覆盖率为 100%，静默无 print。"""
    src = StudentNet()
    p = tmp_path / "same.json"
    save_weights_json(src, str(p))
    dst = StudentNet()
    load_state_into(dst, str(p))
    torch.testing.assert_close(dst.stem.weight, src.stem.weight)


# ---- P2-6c：NaN/Inf 权重拒绝写出 ----
def test_save_rejects_nan_weights(tmp_path: Path) -> None:
    """参数含 NaN 时 save 必须 raise（fail fast），不得写出让 TS 静默跑歪的坏文件。"""
    model = StudentNet()
    with torch.no_grad():
        model.stem.weight[0, 0, 0, 0] = float("nan")
    with pytest.raises(ValueError, match="非有限值"):
        save_weights_json(model, str(tmp_path / "nan.json"))


def test_save_rejects_inf_weights(tmp_path: Path) -> None:
    model = StudentNet()
    with torch.no_grad():
        model.fc.bias[0] = float("inf")
    with pytest.raises(ValueError, match="非有限值"):
        save_weights_json(model, str(tmp_path / "inf.json"))
