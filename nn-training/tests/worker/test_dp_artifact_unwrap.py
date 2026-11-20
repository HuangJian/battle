"""DP 包装必须剥壳后再落产物（2026-11-20 kaggle-c 双 T4 现场）。

**事故**：`remote/train_core._torch_artifacts` 里 `raw_model = model`，而 `st.model` 在
`cuda-dp` 下已经是被 `torch.nn.DataParallel` 包过的对象 ⇒ `state_dict()` 的键全带
`module.` 前缀 ⇒ 回传权重与 init 权重（`blocks.0.dw.weight` 等 44 个）**零交集** ⇒
`remote/weights_delta.pack_weights_delta` 判「新权重缺 44 个参数」，整个 job 被拒
（`kind=ProtocolError`，`skip (not retried)`）。

**为什么潜伏这么久**：只有 ≥2 卡的节点才走 DP（DSW 单卡从不触发）。第一次见到 2×T4 的
节点就把 4 条腿全部打死。

这里钉三件事：
  1. `unwrap_module` 能剥 `DataParallel`（含嵌套）；
  2. 剥壳后的 `state_dict()` 键**不带** `module.` 前缀，且与原始模块逐键相同；
  3. 剥壳不换参数对象（DP 与底层模块共享 storage ⇒ 数值等价）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import torch
import torch.nn as nn

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.ppo.common import HostModule, unwrap_module


def _tiny() -> nn.Module:
    return nn.Sequential(nn.Linear(4, 3), nn.Linear(3, 1))


def test_unwrap_strips_dataparallel() -> None:
    inner = _tiny()
    dp = nn.DataParallel(inner)
    assert unwrap_module(dp) is inner
    assert unwrap_module(inner) is inner  # 未包装 = 原样返回


def test_unwrap_handles_nested() -> None:
    inner = _tiny()
    nested = nn.DataParallel(nn.DataParallel(inner))
    assert unwrap_module(nested) is inner


def test_dataparallel_prefixed_keys_are_the_bug() -> None:
    """不剥壳就落产物会得到 `module.` 前缀 —— 这正是 delta 判「缺参数」的来源。"""
    inner = _tiny()
    dp = nn.DataParallel(inner)
    assert all(k.startswith("module.") for k in dp.state_dict())
    assert set(dp.state_dict()) & set(inner.state_dict()) == set()


def test_hostmodule_after_unwrap_has_clean_keys_and_same_values() -> None:
    inner = _tiny()
    dp = nn.DataParallel(inner)
    host = HostModule(unwrap_module(dp))
    sd = host.state_dict()
    assert list(sd) == list(inner.state_dict())
    assert not any(k.startswith("module.") for k in sd)
    # 数值等价（同一个 storage，不是拷贝出来的另一份）
    for k, v in inner.state_dict().items():
        assert torch.equal(sd[k], v), k
