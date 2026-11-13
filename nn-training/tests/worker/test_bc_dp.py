"""train/bc.py 多卡（DataParallel）设备语义 —— **需要真 torch 的那一半**。

背景（2026-09-13 多卡确认）：云端 2+ GPU 时训练必须真用上多卡——
  * PPO：worker run_job 对 --device cuda-dp 包 DataParallel（既有实现，T4x2 实测 1.92×）；
  * BC：bc.py 本无 DP——本次补齐 `_resolve_bc_device`（cuda-dp → 2+ 卡真 DP；
    单卡/无卡响亮退化）+ `_bc_raw`（DP state_dict 的 "module." 前缀绝不进 weights.json）；
  * worker `_bc_device` 改为透传（不再代砍单卡）。

2026-09-26（item 6f）：**判据**（哪些读数解析成 cuda-dp/cuda/cpu、退化打不打行、透传与
拒绝）已抽到顶层零 torch 的 `train/device.py` + `remote/bc_job.py`，判据用例在
`tests/worker/test_bc_device.py`（免 torch）。本文件只剩**必须真 torch** 的两件事：
`torch.device` 的构造（含 `cuda-dp` → `torch.device("cuda")` 映射）与 `_bc_raw` 的前缀防线。
DataParallel 可在 CPU 上构造（forward 才需要卡）。
"""

from __future__ import annotations

import pytest
import torch

import worker.train.bc as bc_mod
from worker.models.core import NNPolicy

# ------------------------------------------------------------------ 设备名 → torch.device
# 判据在 tests/worker/test_bc_device.py（免 torch）；这里只钉「cuda-dp 名字 → 真 cuda 设备」
# 与 `_bc_raw` 的 "module." 前缀防线（TPU/XLA 的句柄与 SPMD 见 test_bc_xla.py）。


def test_dp_name_maps_onto_a_real_cuda_device() -> None:
    """`cuda-dp` 不是合法 device type：名字留给日志/台账，`torch.device` 必须是 `cuda`。"""
    assert bc_mod._bc_torch_device("cuda-dp", True).type == "cuda"


@pytest.mark.parametrize("arg", ["cuda", "cpu", "cuda:1"])
def test_device_name_passthrough(arg: str) -> None:
    assert bc_mod._bc_torch_device(arg, False).type == arg.split(":")[0]


# ------------------------------------------------------------------ _bc_raw（"module." 前缀防线）


def test_bc_raw_unwraps_dataparallel_and_strips_prefix() -> None:
    """DP 包装的 state_dict 键带 "module." 前缀——weights.json 是 TS 运行时
    逐字节消费对象，落盘必须走 raw。本测试直接构造真 DataParallel 断言。"""
    model = NNPolicy()
    dp = torch.nn.DataParallel(model)
    assert bc_mod._bc_raw(dp) is model
    assert any(k.startswith("module.") for k in dp.state_dict())  # 前缀真实存在（危害是真的）
    assert not any(k.startswith("module.") for k in bc_mod._bc_raw(dp).state_dict())
    # 非包装模型原样返回
    assert bc_mod._bc_raw(model) is model


def test_bc_raw_preserves_parameter_identity() -> None:
    """DP 与 raw 共享同一批参数对象——训练原地更新，raw.state_dict 即训练终态。"""
    model = NNPolicy()
    dp = torch.nn.DataParallel(model)
    pid_before = id(next(iter(model.parameters())))
    assert id(next(iter(dp.parameters()))) == pid_before  # 共享参数
    assert bc_mod._bc_raw(dp) is model


# worker._bc_device 的透传与 tpu/xla 拒绝已移到 tests/worker/test_bc_device.py（免 torch）：
# 那条路径（remote/bc_job._bc_device）本就不 import torch。
