"""init_spatial_leg 三档回归（plan/policy-spatial-head.plan.md §4 Step 2 S0-c / Step 4-5 起点）。

腿 A/腿 B 的起点由 `--policy-extra [--spatial-tower]` 造（新头/塔随机初始化）；
**A0 对照臂**的起点 = hu150 旧架构——v3 文件在 v4 loader 下被 schema 门拒收
（`build_ppo`/`load_state_into` 全链严格），故 `--legacy-arch` 档做**全量元数据迁移**
（`warmstart_missing=[]`），课程 `bc=` 才可直接使用（否则 A0 一开课即被 worker 拒收）。

本文件钉住：
  ① legacy-arch：产物过 `build_ppo`（旧架构）+ 严格 `load_state_into`，missing 为空；
  ② policy-extra：产物 arch 标志正确、新头重初始化（missing = 两个 head）、严格装载通过；
  ③ 互斥与拒绝：legacy_arch 与 policy_extra 同给 / 三者都不给 ⇒ SystemExit。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import torch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.data.weights_io import load_state_into, save_weights_json
from worker.models.student import PPOStudent
from worker.ppo.engine import build_ppo
from worker.scripts.init_spatial_leg import convert


def _legacy_source(tmp_path: Path) -> Path:
    """合成一份**旧 schema（v3）**起点：默认档模型存盘后把 meta 的 schema_major 改回 3。

    ⚠ 尺寸必须走默认档（h=64/d=8）——`convert()` 的产物模型是默认档，窄模型（h=8/d=1）
    会让卷积权重整片换形、覆盖率跌破 COVERAGE_RAISE 而响亮拒绝（这正是它该有的行为）。
    """
    torch.manual_seed(7)
    src = tmp_path / "legacy-src.json"
    save_weights_json(PPOStudent(), str(src))
    meta = json.loads(src.read_text(encoding="utf-8"))
    meta["schema_major"] = 3
    src.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return src


def test_legacy_arch_passthrough_full_migration(tmp_path: Path) -> None:
    """A0 档：全量迁移（missing=[]）⇒ build_ppo 得旧架构、严格装载通过。"""
    src = _legacy_source(tmp_path)
    dst = tmp_path / "legA0.json"
    info = convert(
        str(src), str(dst), policy_extra=False, spatial_tower=False, legacy_arch=True, seed=3
    )
    assert info["warmstart_missing"] == []
    assert info["schema_major"] == 4
    model = build_ppo(str(dst))
    assert model.policy_extra is False
    assert model.spatial_tower is False
    load_state_into(model, str(dst))  # strict（默认拒绝旧 schema）必须通过
    meta = json.loads(dst.read_text(encoding="utf-8"))
    assert meta["arch"].get("policyExtra", False) is False


def test_policy_extra_leg_a_start_reinitializes_heads(tmp_path: Path) -> None:
    """腿 A 档：新头重初始化（missing = 两个 head），产物 arch 带 policyExtra。"""
    src = _legacy_source(tmp_path)
    dst = tmp_path / "legA.json"
    info = convert(
        str(src), str(dst), policy_extra=True, spatial_tower=False, legacy_arch=False, seed=3
    )
    assert sorted(info["warmstart_missing"]) == ["fire_head.weight", "move_head.weight"]
    model = build_ppo(str(dst))
    assert model.policy_extra is True
    assert model.spatial_tower is False
    load_state_into(model, str(dst))


def test_modes_are_mutually_exclusive_and_none_refused(tmp_path: Path) -> None:
    """互斥/缺档都必须响亮拒绝（不放宽 = 防静默建错架构）。"""
    src = _legacy_source(tmp_path)
    dst = tmp_path / "x.json"
    with pytest.raises(SystemExit):
        convert(str(src), str(dst), policy_extra=True, spatial_tower=False, legacy_arch=True)
    with pytest.raises(SystemExit):
        convert(str(src), str(dst), policy_extra=False, spatial_tower=False, legacy_arch=False)
