"""train/device.py — BC 设备串解析的**判据**（顶层零 torch，2026-09-26，item 6f）。

## 为什么单独一个模块

`train/bc.py` 顶层 `import torch`（整个训练器都要），于是「`cuda-dp` 在多卡/单卡/无卡上
分别解析成什么、退化时是否**响亮**（打一行 WARNING）」这条**策略**，在测试里必须连坐
torch，还得 `monkeypatch.setattr(torch.cuda, ...)` 去装探针。策略本身只吃两个读数
（`cuda_available` / `device_count`）⇒ 把**探针**降成一个参数，判据就能免 torch 测
（`tests/test_bc_device.py`）。

## 约定

* 返回 `(规范化 device 串, use_dp)`；`use_dp=True` 时名字是 `"cuda-dp"`，但**真实的
  `torch.device` 由调用方按 `"cuda"` 构造**——`cuda-dp` 不是合法 device type（见
  `train/bc.py::_resolve_bc_device` 的映射，`tests/test_bc_dp.py` 钉住这一条）。
* 退化（单卡/无卡）**必须响亮**：一行 `[train] WARNING: …退化为…`。这条日志的措辞是
  既有产物（云端日志面板按 `[train]` 抓），一字不改。
* 除 `cuda-dp` / `dp` 外一律原样返回（`""` ⇒ `cpu`），**不触发探针**——`torch.cuda.is_available()`
  在纯 CPU 机上不是零成本，也不该在非 DP 路径上被顺带调用。
"""

from __future__ import annotations

from collections.abc import Callable

#: `--device` 里表示「多卡 DataParallel」的两个写法。
DP_DEVICE_ALIASES = ("cuda-dp", "dp")


def _probe_cuda() -> tuple[bool, int]:
    """默认探针：`(是否可用, 可见卡数)`；函数内**延迟** `import torch`。

    延迟 import 是刻意的：本模块顶层零 torch ⇒ `tests/test_bc_device.py` 不 import torch
    也能测判据（探针从参数注入，默认这条路径在测试里根本不执行）。
    """
    import torch

    available = bool(torch.cuda.is_available())
    return available, (int(torch.cuda.device_count()) if available else 0)


def resolve_bc_device(
    device: str, *, cuda_probe: Callable[[], tuple[bool, int]] | None = None
) -> tuple[str, bool]:
    """→ `(规范化 device 串, use_dp)`；判据见模块 docstring。

    `cuda_probe` 为 None 时用 `_probe_cuda`（真 torch）；测试传 `lambda: (True, 2)` 之类的
    读数即可，无需 monkeypatch torch。只有 `cuda-dp` / `dp` 才会调用探针。
    """
    s = str(device or "cpu").lower()
    if s not in DP_DEVICE_ALIASES:
        return s or "cpu", False
    probe = _probe_cuda if cuda_probe is None else cuda_probe
    available, count = probe()
    n = int(count) if available else 0
    if n > 1:
        return "cuda-dp", True
    print(
        f"[train] WARNING: 请求 cuda-dp 但可见 {n} 张卡——退化为单卡 "
        f"{'cuda' if n else 'cpu'}（与 remote/worker 的 cuda-dp 退化语义一致）",
        flush=True,
    )
    return ("cuda" if n else "cpu"), False
