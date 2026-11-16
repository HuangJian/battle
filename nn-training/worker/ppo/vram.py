"""上卡前的显存余量预检（2026-11-16，用户口径：搬语料前先查够不够）。

为什么需要它：eager 路径 `tensored_chunks` 把**整个语料**一次性搬到 device，35×20 那档
obs 3.97 GB + scalars 64 MB + extra 79 MB ≈ 4.1 GB。产线平台（Kaggle / Colab / AIStudio）
显存均 ≥16 GB，最坏情况加模型/激活 ~1.4 GB 仍余 10 GB —— 但这是**算出来的**，代码里没有任何
一处真的确认过。与其找一张 6 GB 小卡做真机实验，不如每次上卡前查一次余量，不足就响亮拒绝。

设计分工：
  * 本模块只做**纯判定**（`corpus_bytes` / `require_vram`），不 import torch/paddle ⇒ 可在 CPU-only
    机器和单测里跑；
  * 两个后端的余量探测各自是薄壳（`torch_free_bytes` / `paddle_free_bytes`），探测不到返回
    **None = 放行**（CPU 训练、老版本 CUDA API 都没有理由拦住训练）。

pyclause 纪律：探测失败绝不把一次可跑的训练变成失败 —— 只有**明确测到不足**才抛。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from common.errors import ProtocolError

#: 语料之外的 torch 上下文 / 临时张量留 20%；它不是显存总预算公式，只防止卡在 99% 上。
_HEADROOM_NUM = 6
_HEADROOM_DEN = 5


def corpus_bytes(chunks: Any) -> int:
    """eager chunks 的 numpy 数组总字节数（`LazyChunks` 不该走这里）。"""
    total = 0
    for chunk in chunks:
        for v in chunk.values():
            n = getattr(v, 'nbytes', None)
            if n is None:
                continue
            total += int(n)
    return total


def require_bytes(need_bytes: int, free_bytes: int | None, *, label: str = 'corpus') -> None:
    """`free` 明确给出且不够放 `need` ⇒ 抛 `ProtocolError`；`None` 或够 ⇒ 静默放行。

    独立成纯函数是为了单测能在没有 GPU 的机器上钉住三条路径（未知/够/不够），
    而不是等下次真机 OOM 才发现那行保护根本没生效。
    """
    if free_bytes is None:
        return
    need = need_bytes * _HEADROOM_NUM // _HEADROOM_DEN
    if int(free_bytes) >= need:
        return
    raise ProtocolError(
        f"显存不足（{label}）：语料需 {need_bytes / 1e6:.0f} MB"
        f"（含 {_HEADROOM_NUM}/{_HEADROOM_DEN} 余量后 {need / 1e6:.0f} MB），"
        f"device 仅剩 {int(free_bytes) / 1e6:.0f} MB —— 改用 lazy 或减小 --mb，"
        "不要靠 OOM 事后补救"
    )


def require_corpus(chunks: Any, free_bytes: int | None, *, label: str = 'eager corpus') -> None:
    """`require_bytes(corpus_bytes(chunks), free_bytes)` 的常用包装。"""
    require_bytes(corpus_bytes(chunks), free_bytes, label=label)


def torch_free_bytes(device: Any) -> int | None:
    """torch CUDA 剩余显存；非 CUDA / 探测失败 ⇒ None（放行）。"""
    try:
        import torch
    except ImportError:
        return None
    if not torch.cuda.is_available():
        return None
    try:
        return int(torch.cuda.mem_get_info(device)[0])
    except Exception:
        return None


def paddle_free_bytes(device: Any) -> int | None:
    """paddle CUDA 剩余显存；非 CUDA / 该版本无 API / 探测失败 ⇒ None（放行）。"""
    try:
        import paddle
    except ImportError:
        return None
    try:
        if not paddle.is_compiled_with_cuda():
            return None
        probe = getattr(paddle.cuda, 'mem_get_info', None)
        if probe is None:
            return None
        return int(probe()[0])
    except Exception:
        return None


#: 单测注入点（避免为了假探测去 monkeypatch torch/paddle 模块）。
FREE_PROBES: dict[str, Callable[[Any], int | None]] = {
    'torch': torch_free_bytes,
    'paddle': paddle_free_bytes,
}
