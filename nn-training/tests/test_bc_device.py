"""tests/test_bc_device.py —— BC 设备串的**判据**（免 torch，2026-09-26，item 6f）。

## 为什么有这一份

`tests/test_bc_dp.py` 里那组 `_resolve_bc_device` 用例要 `import torch` 才能
`monkeypatch.setattr(torch.cuda, "is_available", …)`。判据本身只吃两个读数
（`cuda_available` / `device_count`）⇒ 探针降成参数（`cuda_probe`）后，
`train/device.py` 顶层零 torch，判据可以在这里**不 import torch** 测。

本文件钉：

  1. `cuda-dp` 在 2+ 卡上才是 DataParallel；单卡 / 无卡**响亮退化**（stdout 一行
     `退化`——静默降级是这里最坏的失败形态：云端 benchmark 会以单卡数据冒充多卡）；
  2. 判据看的是**卡数**而不是 `is_available` 标志（`available=True, count=0` 也要落 cpu）；
  3. 非 `cuda-dp` 路径**不得调用探针**（`torch.cuda.is_available()` 不是零成本，
     纯 CPU 机上更不该被顺带调用）；
  4. `remote/bc_job._bc_device` 的透传 + tpu/xla 响亮拒绝（那半边本就零 torch）。

**不在这里**：`torch.device` 的构造（含 `cuda-dp` → `torch.device("cuda")` 这条映射）
与 `_bc_raw` 的 `"module."` 前缀防线——它们要真 torch，留在 `tests/test_bc_dp.py`。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import ProtocolError
from remote.bc_job import _bc_device
from train.device import DP_DEVICE_ALIASES, resolve_bc_device


def _probe(available: bool, count: int):
    """替身探针：把「这台机器长什么样」变成测试参数。"""
    return lambda: (available, count)


def test_multi_gpu_keeps_the_dp_name_and_arms_dp() -> None:
    assert resolve_bc_device("cuda-dp", cuda_probe=_probe(True, 2)) == ("cuda-dp", True)
    assert resolve_bc_device("dp", cuda_probe=_probe(True, 8)) == ("cuda-dp", True)
    assert DP_DEVICE_ALIASES == ("cuda-dp", "dp")


def test_single_gpu_degrades_to_single_card_loudly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert resolve_bc_device("cuda-dp", cuda_probe=_probe(True, 1)) == ("cuda", False)
    assert "退化" in capsys.readouterr().out, "单卡退化必须响亮（不得静默）"


def test_no_gpu_degrades_to_cpu_loudly(capsys: pytest.CaptureFixture[str]) -> None:
    assert resolve_bc_device("cuda-dp", cuda_probe=_probe(False, 0)) == ("cpu", False)
    assert "退化" in capsys.readouterr().out


def test_the_verdict_reads_the_card_count_not_the_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`is_available=True` 但可见 0 张卡 ⇒ 仍是 cpu（判据看卡数）。"""
    assert resolve_bc_device("cuda-dp", cuda_probe=_probe(True, 0)) == ("cpu", False)
    assert "退化" in capsys.readouterr().out


@pytest.mark.parametrize(
    "arg,expect",
    [("cuda", "cuda"), ("cpu", "cpu"), ("", "cpu"), ("cuda:1", "cuda:1")],
)
def test_passthrough_never_probes(arg: str, expect: str) -> None:
    """非 cuda-dp 路径原样返回，且**不碰探针**（探针一调就炸 = 断言失败）。"""

    def boom() -> tuple[bool, int]:
        raise AssertionError("非 cuda-dp 路径不得调用 CUDA 探针")

    assert resolve_bc_device(arg, cuda_probe=boom) == (expect, False)


@pytest.mark.parametrize(
    "arg,expect",
    [("cuda-dp", "cuda-dp"), ("cuda", "cuda"), ("cpu", "cpu"), ("", "cpu")],
)
def test_worker_bc_device_passthrough(arg: str, expect: str) -> None:
    """worker 不再代砍单卡：`cuda-dp` 透传，语义由 train/bc.py 定。"""
    assert _bc_device(arg) == expect


@pytest.mark.parametrize("arg", ["tpu", "xla"])
def test_worker_bc_device_tpu_rejected(arg: str) -> None:
    """train/bc.py 无 xla 路径 ⇒ 确定性拒绝，绝不静默降级。"""
    with pytest.raises(ProtocolError, match="cuda/cuda-dp"):
        _bc_device(arg)
