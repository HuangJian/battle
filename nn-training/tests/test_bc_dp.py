"""train/bc.py 多卡（DataParallel）设备语义 + remote/worker._bc_device 透传测试。

背景（2026-09-13 多卡确认）：云端 2+ GPU 时训练必须真用上多卡——
  * PPO：worker run_job 对 --device cuda-dp 包 DataParallel（既有实现，T4x2 实测 1.92×）；
  * BC：bc.py 本无 DP——本次补齐 `_resolve_bc_device`（cuda-dp → 2+ 卡真 DP；
    单卡/无卡响亮退化）+ `_bc_raw`（DP state_dict 的 "module." 前缀绝不进 weights.json）；
  * worker `_bc_device` 改为透传（不再代砍单卡）。

全部 hermetic：torch.cuda.is_available/device_count 注入（真 torch，无 CUDA 也能测）；
DataParallel 可在 CPU 上构造（forward 才需要卡）。
"""

from __future__ import annotations

import pytest
import torch

import train.bc as bc_mod
from models.core import NNPolicy
from remote.worker import _bc_device

# ------------------------------------------------------------------ _resolve_bc_device


def test_resolve_cuda_dp_multi_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 2)
    name, dev, use_dp = bc_mod._resolve_bc_device("cuda-dp")
    assert (name, use_dp) == ("cuda-dp", True)
    assert dev.type == "cuda"


def test_resolve_cuda_dp_single_gpu_degrades_loud(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 1)
    name, dev, use_dp = bc_mod._resolve_bc_device("cuda-dp")
    assert (name, use_dp) == ("cuda", False)
    assert dev.type == "cuda"
    assert "退化" in capsys.readouterr().out  # 响亮退化，不静默


def test_resolve_cuda_dp_no_gpu_degrades_to_cpu(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    name, dev, use_dp = bc_mod._resolve_bc_device("cuda-dp")
    assert (name, use_dp) == ("cpu", False)
    assert dev.type == "cpu"
    assert "退化" in capsys.readouterr().out


@pytest.mark.parametrize("arg,expect", [("cuda", "cuda"), ("cpu", "cpu"), ("", "cpu")])
def test_resolve_passthrough(
    arg: str, expect: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    name, dev, use_dp = bc_mod._resolve_bc_device(arg)
    assert name == expect and use_dp is False
    assert dev.type == expect


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


# ------------------------------------------------------------------ worker._bc_device 透传


@pytest.mark.parametrize(
    "arg,expect",
    [("cuda-dp", "cuda-dp"), ("cuda", "cuda"), ("cpu", "cpu"), ("", "cpu")],
)
def test_worker_bc_device_passthrough(arg: str, expect: str) -> None:
    assert _bc_device(arg) == expect


def test_worker_bc_device_tpu_rejected() -> None:
    import remote.protocol

    with pytest.raises(remote.protocol.ProtocolError, match="cuda/cuda-dp"):
        _bc_device("tpu")
